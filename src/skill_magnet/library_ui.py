from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .core import SkillMagnetError
from .library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LibraryTransaction,
    add_skill,
    initialize_library,
    validate_library,
)


LIBRARY_WIZARD_STEPS = (
    "Skill Library Manager",
)


def library_wizard_steps() -> tuple[str, ...]:
    return LIBRARY_WIZARD_STEPS


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
    """Require an already-created standard skill folder for manual registration."""
    if not value.strip():
        raise SkillMagnetError("登録する作成済みスキルのフォルダーを選択してください")
    source = Path(value).resolve()
    if not (source / "SKILL.md").is_file():
        raise SkillMagnetError("選択したフォルダーにSKILL.mdがありません")
    if not (source / "acceptance.json").is_file():
        raise SkillMagnetError("選択したフォルダーにacceptance.jsonがありません")
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


def import_selected_skill(repository: Path, source: Path | None) -> bool:
    """Import a standard skill folder and use the generic custom-skills pack."""
    if source is None or not (source / "SKILL.md").is_file():
        return False
    if not (source / "acceptance.json").is_file():
        raise SkillMagnetError("SKILL.mdと同じフォルダーにacceptance.jsonが必要です")
    skill_id, display_name, purpose = skill_registration_metadata(source)
    if (repository / skill_id).is_dir():
        return True
    add_skill(
        repository,
        skill_id=skill_id,
        display_name=display_name,
        purpose=purpose,
        pack_id="custom-skills",
        pack_display_name="Custom skills",
        skill_source=source,
    )
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
    root.title("Skill Magnet — Skill Library Manager")
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
            and (initial_repository / "SKILL.md").is_file()
            else ""
        )
    )
    transaction_id = tk.StringVar()
    platform = "windows" if os.name == "nt" else "macos"
    publish_confirmed = tk.BooleanVar(value=False)
    result: dict[str, Any] = {"status": "closed_without_activation"}

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
    ttk.Label(registration, text="作成済みスキルのSKILL.mdとacceptance.jsonを登録します。").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    row(registration, 1, "作成済みスキルのフォルダー", import_source, select_import)

    def add() -> None:
        try:
            source = require_registration_source(import_source.get())
            import_selected_skill(require_repository(), source)
            messagebox.showinfo(
                "Skill",
                "スキルを登録し、パック一覧とINDEXを自動更新しました。",
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
        try:
            if not remote.get().strip():
                raise SkillMagnetError("公開先のGitHub URLを入力してください")
            transaction = LibraryTransaction(state_dir)
            validate_library(require_repository())
            preview = transaction.prepare(
                draft=require_repository(), remote=remote.get().strip()
            )
            transaction_id.set(transaction.transaction_id)
            set_text(preview_output, preview)
        except Exception as exc:
            show_error(exc)

    ttk.Button(publish_frame, text="送信内容を確認する", command=prepare).grid(
        row=4, column=0, sticky="w", pady=(8, 4)
    )
    ttk.Checkbutton(
        publish_frame,
        text="表示された公開先とファイルを確認しました",
        variable=publish_confirmed,
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=4)

    def transaction() -> LibraryTransaction:
        if not transaction_id.get().strip():
            raise SkillMagnetError("Transaction ID is required")
        return LibraryTransaction(state_dir, transaction_id.get().strip())

    def publish() -> None:
        try:
            if not publish_confirmed.get() or not messagebox.askyesno(
                "Publish", "専用branchへcommit・pushしてPRを作成しますか？", parent=root
            ):
                raise SkillMagnetError("Publish was not explicitly confirmed")
            published = transaction().publish(confirmed=True)
            set_text(preview_output, published)
        except Exception as exc:
            show_error(exc)

    def verify_merged() -> None:
        try:
            verified = transaction().mark_merged()
            set_text(preview_output, verified)
        except Exception as exc:
            show_error(exc)

    def activate() -> None:
        nonlocal result
        try:
            if not messagebox.askyesno(
                "Skill Magnetへ反映",
                "検証済み版をSkill Magnetへ反映しますか？失敗時は直前版へ戻します。",
                parent=root,
            ):
                raise SkillMagnetError("Activation was not explicitly confirmed")

            def update(path: Path) -> Any:
                return menu_update(path, platform) if menu_update else None

            result = transaction().activate(
                config_path=config_path,
                confirmed=True,
                menu_update=update if menu_update else None,
            )
            set_text(preview_output, result)
            messagebox.showinfo("Skill Library Manager", "有効化が完了しました。", parent=root)
        except Exception as exc:
            show_error(exc)

    buttons = ttk.Frame(publish_frame)
    buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(4, 0))
    ttk.Button(buttons, text="Publish PR", command=publish).pack(side="left", padx=4)
    ttk.Button(buttons, text="Verify merged remote", command=verify_merged).pack(side="left", padx=4)
    ttk.Button(buttons, text="Skill Magnetへ反映", command=activate).pack(side="left", padx=4)

    root.mainloop()
    return result
