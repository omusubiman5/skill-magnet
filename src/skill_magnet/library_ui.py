from __future__ import annotations

import json
import os
import re
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .core import SkillMagnetError
from .library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LibraryTransaction,
    discover_skill_sources,
    import_skill_source,
    initialize_library,
    find_resumable_transaction,
    list_transactions,
    validate_library,
)


LIBRARY_WIZARD_STEPS = (
    "Skill Library Manager",
)

LIBRARY_ACTION_LABELS = {
    "prepare": "送信内容を確認する",
    "publish": "GitHubへ送る",
    "open_pr": "GitHubでPRを開く",
    "verify": "GitHubのマージを確認する",
    "activate": "Skill Magnetへ反映",
    "complete": "完了",
}


def library_wizard_steps() -> tuple[str, ...]:
    return LIBRARY_WIZARD_STEPS


def library_action_label(stage: str) -> str:
    """Return the only action exposed for the current transaction stage."""
    try:
        return LIBRARY_ACTION_LABELS[stage]
    except KeyError as exc:
        raise SkillMagnetError(f"Unknown library action stage: {stage}") from exc


def managed_repository_path(state_dir: Path) -> Path:
    """Return the app-owned library workspace; users do not manage this path."""
    return (state_dir.resolve() / "library" / DEFAULT_REPOSITORY_NAME).resolve()


def configured_repository_url(config_path: Path) -> str:
    """Return the existing repository URL when the active config has one clear choice."""
    if not config_path.is_file():
        return ""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    urls = {
        str(pack.get("repo_url", "")).strip()
        for pack in config.get("packs", [])
        if str(pack.get("repo_url", "")).strip()
    }
    return next(iter(urls)) if len(urls) == 1 else ""


def require_registration_source(value: str) -> Path:
    """Require a folder containing a skill, a pack, or multiple packs."""
    if not value.strip():
        raise SkillMagnetError("登録するスキルまたはスキルパックのフォルダーを選択してください")
    source = Path(value).resolve()
    discover_skill_sources(source)
    return source


def skill_registration_metadata(source: Path) -> tuple[str, str, str]:
    """Derive internal ID and user-facing metadata from an existing skill."""
    source = require_registration_source(str(source))
    text = (source / "SKILL.md").read_text(encoding="utf-8")

    def metadata(key: str) -> str:
        match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
        return match.group(1).strip(" '\"") if match else ""

    skill_id = metadata("name") or source.name
    display_match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    display_name = display_match.group(1).strip() if display_match else skill_id
    purpose = metadata("description") or f"Imported skill: {display_name}"
    return skill_id, display_name, purpose


def source_already_registered(repository: Path, source: Path) -> bool:
    """Return true for an exact registered source and reject partial pack overlap."""
    discovered = discover_skill_sources(source)
    catalog_path = repository / CATALOG_FILENAME
    if not catalog_path.is_file():
        return False
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    existing = {
        str(pack.get("id", "")): set(pack.get("skills", []))
        for pack in catalog.get("packs", [])
    }
    regular = [pack for pack in discovered if pack["id"] != "custom-skills"]
    overlapping = [pack for pack in regular if pack["id"] in existing]
    if overlapping:
        exact = len(overlapping) == len(regular) and all(
            existing[pack["id"]] == set(pack["skills"])
            and all((repository / skill).is_dir() for skill in pack["skills"])
            for pack in regular
        )
        if exact:
            return True
        raise SkillMagnetError(
            "同じパックIDの登録情報と保存ファイルが一致しません。"
            "一部だけを上書きせず処理を停止しました"
        )
    if regular:
        return False
    incoming = {skill for pack in discovered for skill in pack["skills"]}
    custom = existing.get("custom-skills", set())
    return incoming <= custom and all((repository / skill).is_dir() for skill in incoming)


def register_skill_source(repository: Path, source: Path) -> dict[str, Any]:
    """Register once or return a successful no-op for the same complete source."""
    discovered = discover_skill_sources(source)
    pack_ids = [str(pack["id"]) for pack in discovered]
    skill_ids = [skill for pack in discovered for skill in pack["skills"]]
    if source_already_registered(repository, source):
        return {
            "already_registered": True,
            "imported_pack_ids": pack_ids,
            "imported_skill_ids": skill_ids,
        }
    result = import_skill_source(repository, source)
    result["already_registered"] = False
    return result


def import_selected_skill(repository: Path, source: Path | None) -> bool:
    """Import a skill, a complete pack, or a directory containing packs."""
    if source is None:
        return False
    try:
        discovered = discover_skill_sources(source)
    except SkillMagnetError:
        return False
    register_skill_source(repository, source)
    return True


def show_library_manager(
    *,
    config_path: Path,
    state_dir: Path,
    initial_repository: Path | None = None,
    menu_update: Callable[[Path, str], Any] | None = None,
) -> dict[str, Any]:
    """Open the compact library manager and return its final status."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise SkillMagnetError("Tk is required for the Skill Library Manager UI") from exc

    root = tk.Tk()
    root.title("Library Manager")
    root.geometry("920x680")
    root.minsize(760, 560)
    page = ttk.Frame(root, padding=12)
    page.pack(fill="both", expand=True)
    page.columnconfigure(0, weight=1)
    page.rowconfigure(1, weight=1)

    repository_path = managed_repository_path(state_dir)
    catalog_path = repository_path / CATALOG_FILENAME
    if catalog_path.is_file():
        json.loads(catalog_path.read_text(encoding="utf-8"))
    else:
        initialize_library(repository_path, DEFAULT_REPOSITORY_NAME)
    repository = tk.StringVar(value=str(repository_path))
    remote = tk.StringVar(value=configured_repository_url(config_path))
    import_source = tk.StringVar(
        value=(
            str(initial_repository.resolve())
            if initial_repository is not None
            and initial_repository.is_dir()
            else ""
        )
    )
    transaction_id = tk.StringVar()
    action_stage = tk.StringVar(value="prepare")
    platform = "windows" if os.name == "nt" else "macos"
    result: dict[str, Any] = {"status": "closed_without_activation"}
    busy = False

    def row(page: Any, number: int, label: str, variable: Any, browse: Callable[[], None] | None = None) -> None:
        ttk.Label(page, text=label).grid(row=number, column=0, sticky="w", padx=4, pady=5)
        ttk.Entry(page, textvariable=variable, width=74).grid(
            row=number, column=1, sticky="ew", padx=4, pady=5
        )
        if browse is not None:
            ttk.Button(page, text="Browse", command=browse).grid(row=number, column=2, padx=4)
        page.columnconfigure(1, weight=1)

    def select_import() -> None:
        value = filedialog.askdirectory(title="Select skill directory")
        if value:
            try:
                source = require_registration_source(value)
                import_source.set(str(source))
            except Exception as exc:
                show_error(exc)

    def show_error(exc: Exception) -> None:
        messagebox.showerror("Skill Library Manager", str(exc), parent=root)

    def abandon_current() -> None:
        if not transaction_id.get().strip():
            return
        abandoned = transaction().abandon(confirmed=True)
        set_text(preview_output, abandoned)
        transaction_id.set("")
        set_stage("prepare")

    def handle_transaction_error(exc: Exception, failed_stage: str) -> None:
        """Give every interrupted transaction a user-controlled exit path."""
        if not transaction_id.get().strip():
            show_error(exc)
            return
        journal = transaction()._journal()
        remote_effect_possible = bool(journal.get("commit") or journal.get("pr_url")) or str(
            journal.get("status", "")
        ) in {"publishing", "published_pending", "verified", "active"}
        if remote_effect_possible:
            choice = messagebox.askyesno(
                "処理を再試行できます",
                f"{exc}\n\nGitHubへ送信済みの可能性があるため、この作業は破棄しません。\n"
                "「はい」: 同じ作業を復旧して再試行\n"
                "「いいえ」: 状態を保存したまま画面へ戻る",
                parent=root,
            )
            if choice:
                try:
                    recovered = transaction().recover()
                    set_text(preview_output, recovered)
                    set_stage(stage_for_status(str(recovered.get("status")), failed_stage))
                    root.after(0, run_current_action)
                except Exception as recovery_error:
                    show_error(recovery_error)
            return
        choice = messagebox.askyesnocancel(
            "途中で処理が止まりました",
            f"{exc}\n\n"
            "「はい」: 保存済みの状態から復旧して、同じ処理を再試行\n"
            "「いいえ」: このローカル作業を破棄して最初からやり直す\n"
            "「キャンセル」: 状態を保存したまま画面へ戻る\n\n"
            "公開済みのGitHub branchやPRは自動削除しません。",
            parent=root,
        )
        if choice is True:
            try:
                recovered = transaction().recover()
                set_text(preview_output, recovered)
                recovered_stage = stage_for_status(str(recovered.get("status")), failed_stage)
                set_stage(recovered_stage)
                root.after(0, run_current_action)
            except Exception as recovery_error:
                show_error(recovery_error)
        elif choice is False and messagebox.askyesno(
            "この作業を破棄",
            "ローカルの一時作業を破棄して最初からやり直しますか？\n"
            "GitHubへ送信済みの内容は残ります。",
            parent=root,
        ):
            try:
                abandon_current()
            except Exception as abandon_error:
                show_error(abandon_error)

    def require_repository() -> Path:
        if not repository.get().strip():
            raise SkillMagnetError("スキルを保存するフォルダーを指定してください")
        return Path(repository.get()).resolve()

    try:
        selected_skill_imported = import_selected_skill(repository_path, initial_repository)
    except Exception as exc:
        selected_skill_imported = False
        show_error(exc)

    registration = ttk.LabelFrame(page, text="作成済みスキルを登録", padding=10)
    registration.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    ttk.Label(registration, text="スキル、スキルパック、または複数パックを含むフォルダーを登録します。").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    row(registration, 1, "スキル／スキルパックのフォルダー", import_source, select_import)

    def add() -> None:
        try:
            source = require_registration_source(import_source.get())
            repository_root = require_repository()
            imported = register_skill_source(repository_root, source)
            if imported["already_registered"]:
                messagebox.showinfo(
                    "スキルを登録",
                    "選択したスキルまたはスキルパックは登録済みです。",
                    parent=root,
                )
                registration.grid_remove()
                return
            messagebox.showinfo(
                "Skill",
                f"{len(imported['imported_pack_ids'])}パック、"
                f"{len(imported['imported_skill_ids'])}スキルを登録しました。",
                parent=root,
            )
            registration.grid_remove()
        except Exception as exc:
            show_error(exc)

    ttk.Button(registration, text="登録", command=add).grid(
        row=2, column=1, sticky="e", pady=(8, 0)
    )

    if selected_skill_imported:
        registration.grid_remove()

    def set_text(widget: Any, value: Any) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2))
        widget.configure(state="disabled")

    publish_frame = ttk.LabelFrame(page, text="GitHubへ送る", padding=10)
    publish_frame.grid(row=1, column=0, sticky="nsew")
    ttk.Label(
        publish_frame,
        text=(
            "公開先のGitHub URLを入力し、送信予定のファイルを確認してからPRを作成します。"
            "URL未入力、ファイル構成不正、検査エラーがあれば送信せずエラーを表示します。"
        ),
        wraplength=820,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    row(publish_frame, 1, "公開先のGitHub URL", remote)
    ttk.Label(
        publish_frame,
        text="例: https://github.com/OWNER/skill-magnet-skills.git",
        wraplength=720,
    ).grid(row=2, column=1, columnspan=2, sticky="w", padx=4, pady=(0, 8))
    preview_output = tk.Text(publish_frame, wrap="word", state="disabled")
    preview_output.grid(row=3, column=0, columnspan=3, sticky="nsew")
    publish_frame.rowconfigure(3, weight=1)
    publish_frame.columnconfigure(1, weight=1)

    def prepare() -> None:
        current: LibraryTransaction | None = None
        try:
            if not remote.get().strip():
                raise SkillMagnetError("公開先のGitHub URLを入力してください")
            current = find_resumable_transaction(
                state_dir,
                draft=require_repository(),
                remote=remote.get().strip(),
            ) or LibraryTransaction(state_dir)
            transaction_id.set(current.transaction_id)
            existing = current._journal()
            if str(existing.get("status")) != "draft":
                recovered = current.recover()
                set_text(preview_output, current._journal())
                set_stage(stage_for_status(str(recovered.get("status")), "prepare"))
                return
            validate_library(require_repository())
            preview = current.prepare(
                draft=require_repository(), remote=remote.get().strip()
            )
            set_text(preview_output, preview)
            if preview.get("no_changes"):
                messagebox.showinfo(
                    "変更はありません",
                    "GitHub上の内容と同じため、PRは作成しません。",
                    parent=root,
                )
                set_stage("complete")
            else:
                set_stage("publish")
        except Exception as exc:
            if current is not None and current.journal_path.is_file():
                handle_transaction_error(exc, "prepare")
            else:
                transaction_id.set("")
                show_error(exc)

    def transaction() -> LibraryTransaction:
        if not transaction_id.get().strip():
            raise SkillMagnetError("先に送信内容を確認してください")
        return LibraryTransaction(state_dir, transaction_id.get().strip())

    def publish() -> None:
        try:
            if not messagebox.askyesno(
                "GitHubへ送る",
                "表示された公開先とファイルを確認しましたか？\n"
                "専用branchへcommit・pushしてPRを作成します。",
                parent=root,
            ):
                return
            published = transaction().publish(confirmed=True)
            set_text(preview_output, published)
            set_stage("open_pr" if published.get("pr_url") else "verify")
        except Exception as exc:
            handle_transaction_error(exc, "publish")

    def verify_merged() -> None:
        try:
            verified = transaction().mark_merged()
            set_text(preview_output, verified)
            wait_state = str(verified.get("wait_state", ""))
            if wait_state == "waiting_for_merge":
                messagebox.showinfo(
                    "GitHubでのマージ待ち",
                    "PRは正常に作成済みです。GitHubでマージした後、もう一度確認してください。",
                    parent=root,
                )
                set_stage("open_pr")
                return
            if wait_state == "closed_unmerged":
                messagebox.showwarning(
                    "PRはマージされていません",
                    "PRはマージされずに閉じられています。GitHubでPRを再度開くか、状態を保持したまま終了してください。",
                    parent=root,
                )
                set_stage("open_pr")
                return
            set_stage("activate")
        except Exception as exc:
            handle_transaction_error(exc, "verify")

    def activate() -> None:
        nonlocal result
        try:
            if not messagebox.askyesno(
                "Skill Magnetへ反映",
                "検証済み版をSkill Magnetへ反映しますか？失敗時は直前版へ戻します。",
                parent=root,
            ):
                return

            def update(path: Path) -> Any:
                return menu_update(path, platform) if menu_update else None

            result = transaction().activate(
                config_path=config_path,
                confirmed=True,
                menu_update=update if menu_update else None,
            )
            set_text(preview_output, result)
            set_stage("complete")
            messagebox.showinfo("Skill Library Manager", "有効化が完了しました。", parent=root)
        except Exception as exc:
            handle_transaction_error(exc, "activate")

    def stage_for_status(status: str, fallback: str = "prepare") -> str:
        return {
            "prepared": "publish",
            "published_pending": "open_pr",
            "verified": "activate",
            "active": "complete",
        }.get(status, fallback)

    def open_pull_request() -> None:
        journal = transaction()._journal()
        url = str(journal.get("pr_url", ""))
        if not url:
            set_stage("verify")
            return
        if not webbrowser.open(url):
            messagebox.showwarning(
                "ブラウザを開けませんでした",
                f"次のURLをブラウザで開いてください。\n\n{url}",
                parent=root,
            )
        set_stage("verify")

    def set_stage(value: str) -> None:
        action_stage.set(value)
        action_button.configure(
            text=library_action_label(value),
            state="disabled" if value == "complete" or busy else "normal",
        )

    def run_current_action() -> None:
        nonlocal busy
        if busy:
            return
        actions = {
            "prepare": prepare,
            "publish": publish,
            "open_pr": open_pull_request,
            "verify": verify_merged,
            "activate": activate,
        }
        action = actions.get(action_stage.get())
        if action is not None:
            busy = True
            action_button.configure(state="disabled")
            try:
                action()
            finally:
                busy = False
                set_stage(action_stage.get())

    action_button = ttk.Button(
        publish_frame,
        text=library_action_label("prepare"),
        command=run_current_action,
    )
    action_button.grid(row=4, column=0, columnspan=3, sticky="e", pady=(8, 0))

    def offer_interrupted_transaction() -> None:
        try:
            candidates = [
                item
                for item in list_transactions(state_dir, config_path)["transactions"]
                if item["status"]
                in {"unpublished_edit", "published_pending", "published_but_inactive", "interrupted"}
            ]
            if not candidates:
                return
            latest = max(candidates, key=lambda item: str(item.get("updated_at", "")))
            transaction_id.set(str(latest["transaction_id"]))
            raw = transaction()._journal()
            if str(raw.get("status")) == "published_pending":
                set_text(preview_output, raw)
                set_stage("open_pr")
                messagebox.showinfo(
                    "GitHubでのマージ待ち",
                    "作成済みのPRがあります。新しいPRは作らず、この作業を続けます。",
                    parent=root,
                )
                return
            if str(raw.get("status")) == "verified":
                set_text(preview_output, raw)
                set_stage("activate")
                messagebox.showinfo(
                    "Skill Magnetへの反映待ち",
                    "GitHub上の内容は検証済みです。この作業を続けて反映できます。",
                    parent=root,
                )
                return
            if raw.get("commit") or raw.get("pr_url") or str(raw.get("status")) == "publishing":
                set_text(preview_output, raw)
                set_stage(stage_for_status(str(raw.get("status")), "publish"))
                retry = messagebox.askyesno(
                    "送信途中の作業があります",
                    "GitHubへ送信済みの可能性があります。作業は破棄せず、同じ状態から再試行しますか？",
                    parent=root,
                )
                if retry:
                    root.after(0, run_current_action)
                return
            choice = messagebox.askyesnocancel(
                "途中の作業があります",
                f"前回の作業（{latest['transaction_id']}）を再開できます。\n\n"
                "「はい」: 復旧して再開\n"
                "「いいえ」: ローカル作業を破棄して最初から\n"
                "「キャンセル」: 状態を残したまま閉じる",
                parent=root,
            )
            if choice is True:
                recovered = transaction().recover()
                set_text(preview_output, recovered)
                stage = stage_for_status(
                    str(recovered.get("status")), str(latest.get("resume_stage", "prepare"))
                )
                set_stage(stage)
            elif choice is False:
                abandon_current()
            else:
                root.destroy()
        except Exception as exc:
            show_error(exc)

    root.after(0, offer_interrupted_transaction)

    root.mainloop()
    return result
