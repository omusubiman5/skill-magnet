from __future__ import annotations

import hashlib
import json
import os
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode

from .activation import (
    ActivationEngine,
    LaunchContract,
    _AcceptanceFailed,
    _CleanupFailed,
    _LaunchFailed,
    _OutputFailed,
    _RuntimeFailed,
)
from .core import SkillMagnetError, normalize_display_text


_CONTEXT_UI_TEXT = {
    "ja": {
        "window_title": "Skill Magnet — 実行確認",
        "language": "言語",
        "project": "プロジェクト: {project}",
        "selection": "選択したスキルパック",
        "selection_skill": "{skill_name}",
        "selection_pack": "{skill_name}",
        "skill_pack": "スキルパック",
        "skill_purpose": "用途: {purpose}",
        "target_ai": "実行先AI",
        "actual_request": "依頼内容",
        "select_pack": "スキルパックを選択してください。",
        "select_runtime": "実行先AIとしてCodexまたはClaudeを選択してください。",
        "included_skills": "全スキル（{count}件）: {skills}",
        "repository": "リポジトリ: {repository}",
        "version": "バージョン: {version}",
        "approved": "承認: {approved_by} / {approved_at}",
        "verification": "パック全件を読み、INDEXで選んだ適用スキルと依頼内容を同じ実行として検証します。",
        "confirm_button": "依頼を実行",
        "cancel_button": "キャンセル",
        "details_show": "詳細を表示",
        "details_hide": "詳細を閉じる",
        "details_title": "検証情報",
        "internal_skill_id": "含まれるスキルID: {skill_id}",
        "pack_id": "パック: {pack_id}",
        "digests": "検証値: スキル一覧 {skill_ids_digest} / 指示 {instruction_digest} / 受入 {acceptance_digest}",
        "error_title": "Skill Magnet エラー",
        "empty_request": "実際の依頼を入力してください。空欄のままでは起動できません。",
        "confirmation_title": "Skill Magnet 起動確認",
        "confirmation_selection": "スキル: {selection}",
        "confirmation_repository": "リポジトリ: {repository}",
        "confirmation_commit": "コミット: {commit}",
        "confirmation_ai": "対象AI: {runtime}",
        "confirmation_project": "プロジェクト: {project}",
        "confirmation_request": "実際の依頼: {purpose}",
        "confirmation_question": "この内容で依頼を実行しますか？",
        "operation_failed": "処理を開始できませんでした。",
    },
    "en": {
        "window_title": "Skill Magnet — Launch confirmation",
        "language": "Language",
        "project": "Project: {project}",
        "selection": "Selected skill pack",
        "selection_skill": "{skill_name}",
        "selection_pack": "{skill_name}",
        "skill_pack": "Skill pack",
        "skill_purpose": "Purpose: {purpose}",
        "target_ai": "Target AI",
        "actual_request": "Actual request",
        "select_pack": "Select one skill pack explicitly.",
        "select_runtime": "Select Codex or Claude as the target AI.",
        "included_skills": "Included skills ({count}): {skills}",
        "repository": "Repository: {repository}",
        "version": "Version: {version}",
        "approved": "Approved: {approved_by} at {approved_at}",
        "verification": "The complete selected pack and request are verified as one execution.",
        "confirm_button": "Confirm and create launch",
        "cancel_button": "Cancel",
        "details_show": "Show details",
        "details_hide": "Hide details",
        "details_title": "Verification details",
        "internal_skill_id": "Internal skill ID: {skill_id}",
        "pack_id": "Pack: {pack_id}",
        "digests": "Digests: skills {skill_ids_digest} / instructions {instruction_digest} / acceptance {acceptance_digest}",
        "error_title": "Skill Magnet error",
        "empty_request": "Enter the actual request. Launch cannot continue while it is empty.",
        "confirmation_title": "Confirm Skill Magnet launch",
        "confirmation_selection": "Selection: {selection}",
        "confirmation_repository": "Repository: {repository}",
        "confirmation_commit": "Commit: {commit}",
        "confirmation_ai": "Target AI: {runtime}",
        "confirmation_project": "Project: {project}",
        "confirmation_request": "Actual request: {purpose}",
        "confirmation_question": "Run this request?",
        "operation_failed": "The operation could not be started.",
    },
}
_context_ui_language = "ja"


def context_ui_text(language: str, key: str, **values: object) -> str:
    """Return one localized UI string without changing internal contract values."""
    selected = language if language in _CONTEXT_UI_TEXT else "ja"
    rendered = _CONTEXT_UI_TEXT[selected][key].format(**values)
    return normalize_display_text(rendered)


def context_ui_confirmation(
    language: str, details: dict[str, object], purpose: str
) -> str:
    selection = context_ui_text(
        language,
        "selection_skill" if details["selection_kind"] == "skill" else "selection_pack",
        skill_name=details.get("skill_display_name", details.get("selected_skill_id", "")),
    )
    lines = (
        context_ui_text(language, "confirmation_selection", selection=selection),
        context_ui_text(
            language, "confirmation_ai", runtime=str(details["runtime"]).title()
        ),
        context_ui_text(language, "confirmation_project", project=details["project"]),
        context_ui_text(language, "confirmation_request", purpose=purpose),
    )
    return "\n".join((*lines, "", context_ui_text(language, "confirmation_question")))


def context_ui_request_error(language: str, purpose: str) -> str | None:
    """Validate only the UI input; the accepted value is passed through unchanged."""
    return None if purpose.strip() else context_ui_text(language, "empty_request")


def context_error_message(error: Exception | str, language: str | None = None) -> str:
    language = language or _context_ui_language
    message = str(error)
    if "Pack HEAD is not the pinned expected_commit" in message:
        if language == "en":
            message += (
                "\n\nUpdate safely: review and approve the new source commit; update the "
                "configured expected commit and skill digests; reinstall the Explorer "
                "menu; verify the selected leaf matches; then retry from a clean source HEAD."
            )
        else:
            message = (
                "Skillパックの現在のHEADが、承認済みコミットと一致しません。"
                "\n\n安全に更新するには、新しいsource commitを確認・承認し、設定済みの"
                "expected commitとSkill digestを更新してExplorerメニューを再インストールし、"
                "選択したleafが一致することを確認してから、cleanなsource HEADで再試行してください。"
            )
    elif language != "en":
        message = f"処理を開始できませんでした。\n\n{message}"
    return message


def context_result_surface(result: dict[str, object]) -> dict[str, str]:
    """Return only the verified, user-facing result fields."""
    if result.get("status") != "verified_completed":
        raise SkillMagnetError("A success result surface requires verified_completed")
    user_result = result.get("user_result")
    if not isinstance(user_result, dict):
        raise SkillMagnetError("Verified result has no user-facing summary")
    required = ("title", "executed_skill", "request", "result", "saved_or_changed")
    if any(
        not isinstance(user_result.get(key), str) or not user_result[key]
        for key in required
    ):
        raise SkillMagnetError("Verified result has an incomplete user-facing summary")
    details = user_result.get("details", {})
    if not isinstance(details, dict):
        details = {}
    detail_lines = ["検証状態: 完了"]
    evidence_file = details.get("evidence_file")
    if isinstance(evidence_file, str) and evidence_file:
        detail_lines.append(f"保存証拠: {evidence_file}")
    return {
        "title": str(user_result["title"]),
        "executed_skill": str(user_result["executed_skill"]),
        "request": str(user_result["request"]),
        "result": str(user_result["result"]),
        "saved_or_changed": str(user_result["saved_or_changed"]),
        "details": "\n".join(detail_lines),
    }


def context_failure_surface(error: Exception) -> dict[str, str]:
    """Map typed failures to a Japanese fail-closed result surface."""
    if isinstance(error, _LaunchFailed):
        return {
            "state": "failed",
            "title": "実行できませんでした",
            "cause": "選択したAIのverification processを開始できませんでした。",
            "not_completed": "依頼実行、スキル受入確認、結果保存は完了していません。",
            "next_action": "選択したAIのインストールと起動状態を確認してから再実行してください。",
        }
    if isinstance(error, _RuntimeFailed):
        return {
            "state": "failed",
            "title": "実行に失敗しました",
            "cause": "選択したAIのverification processが完了前に終了しました。",
            "not_completed": "依頼実行、スキル受入確認、結果保存は完了していません。",
            "next_action": "選択したAIの設定と保存証拠を確認してから再実行してください。",
        }
    if isinstance(error, _AcceptanceFailed):
        return {
            "state": "blocked",
            "title": "完了を確認できませんでした",
            "cause": "実行結果が選択スキル固有の受入条件を満たしませんでした。",
            "not_completed": "成功として表示していません。保存や変更が行われた範囲は確認できません。",
            "next_action": "保存証拠を確認し、依頼内容または実行環境を修正して再実行してください。",
        }
    if isinstance(error, _CleanupFailed):
        return {
            "state": "blocked",
            "title": "完了を確定できませんでした",
            "cause": "一時的なverification成果物の後始末を確認できませんでした。",
            "not_completed": "検証結果を成功として確定していません。",
            "next_action": "保存証拠の未解決成果物を確認し、安全に片付けてから再実行してください。",
        }
    if isinstance(error, _OutputFailed):
        return {
            "state": "blocked",
            "title": "完了を確認できませんでした",
            "cause": "AIの出力が検証可能な完了形式を満たしませんでした。",
            "not_completed": "成功として表示していません。保存や変更が行われた範囲は確認できません。",
            "next_action": "保存証拠を確認し、同じ依頼を再実行してください。",
        }
    return {
        "state": "blocked",
        "title": "実行を続けられません",
        "cause": "安全確認または起動前検証を満たせませんでした。",
        "not_completed": "依頼は完了扱いにしていません。",
        "next_action": "選択内容と保存証拠を確認し、原因を解消してから再実行してください。",
    }


def context_failure_message(error: Exception) -> str:
    surface = context_failure_surface(error)
    return "\n\n".join(
        (
            surface["title"],
            f"原因\n{surface['cause']}",
            f"未実行・未確認の範囲\n{surface['not_completed']}",
            f"次の操作\n{surface['next_action']}",
        )
    )


def show_context_error(message: str, language: str | None = None) -> None:
    """Show the only normal Explorer UI: one human-readable failure dialog."""
    language = language or _context_ui_language
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            context_ui_text(language, "error_title"),
            0x00000000 | 0x00000010 | 0x00010000,
        )
        return

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror(
            context_ui_text(language, "error_title"), message, parent=root
        )
    finally:
        root.quit()
    root.destroy()


def show_context_result(result: dict[str, object]) -> None:
    """Show a Japanese result summary; verification evidence stays in closed details."""
    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText
    from tkinter import ttk

    surface = context_result_surface(result)
    root = tk.Tk()
    root.title("Skill Magnet — 完了")
    root.minsize(680, 560)
    root.resizable(True, True)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(3, weight=1)

    ttk.Label(root, text=surface["title"], font=("Yu Gothic UI", 16, "bold")).grid(
        row=0, column=0, padx=16, pady=(16, 10), sticky="w"
    )
    rows = (
        ("実行したスキル", surface["executed_skill"]),
        ("依頼", surface["request"]),
        ("結果", surface["result"]),
        ("保存先/変更", surface["saved_or_changed"]),
    )
    for row_index, (label, value) in enumerate(rows, start=1):
        frame = ttk.LabelFrame(root, text=label)
        frame.grid(
            row=row_index,
            column=0,
            padx=16,
            pady=5,
            sticky="nsew" if label == "結果" else "ew",
        )
        frame.columnconfigure(0, weight=1)
        if label == "結果":
            frame.rowconfigure(0, weight=1)
            result_text = ScrolledText(
                frame,
                height=10,
                wrap=tk.WORD,
                font=("Yu Gothic UI", 10),
                padx=8,
                pady=8,
            )
            result_text.insert("1.0", value)
            result_text.configure(state="disabled")
            result_text.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")
        else:
            ttk.Label(frame, text=value, wraplength=620, justify="left").grid(
                row=0, column=0, padx=10, pady=8, sticky="w"
            )

    details = ttk.LabelFrame(root, text="詳細")
    ttk.Label(details, text=surface["details"], wraplength=560).grid(
        row=0, column=0, padx=10, pady=8, sticky="w"
    )
    details_visible = False
    details_button_text = tk.StringVar(value="詳細を表示")

    def toggle_details() -> None:
        nonlocal details_visible
        details_visible = not details_visible
        if details_visible:
            details.grid(row=6, column=0, padx=16, pady=5, sticky="ew")
        else:
            details.grid_remove()
        details_button_text.set("詳細を閉じる" if details_visible else "詳細を表示")

    ttk.Button(root, textvariable=details_button_text, command=toggle_details).grid(
        row=5, column=0, padx=16, pady=6, sticky="w"
    )
    ttk.Button(root, text="閉じる", command=root.destroy).grid(
        row=7, column=0, padx=16, pady=(8, 16), sticky="e"
    )
    root.mainloop()


_WEB_CLAUDE_DESTINATION = "https://claude.ai/new"
_WEB_CLAUDE_MAX_PROMPT_CHARS = 8_000
_WEB_CLAUDE_MAX_URL_CHARS = 16_384

_CODEX_DESKTOP_DESTINATION = "codex://threads/new"
_CODEX_DESKTOP_MAX_PROMPT_CHARS = 12_000
_CODEX_DESKTOP_MAX_URL_CHARS = 32_767


def codex_desktop_deep_link(prompt: str, project: str, destination: str) -> str:
    """Build the canonical new-task deep link with lossless URL encoding."""
    if destination != _CODEX_DESKTOP_DESTINATION:
        raise SkillMagnetError("Unexpected Codex Desktop destination")
    if not prompt:
        raise SkillMagnetError("Codex Desktop prompt is empty")
    if not project:
        raise SkillMagnetError("Codex Desktop project is empty")
    if len(prompt) > _CODEX_DESKTOP_MAX_PROMPT_CHARS:
        raise SkillMagnetError("Codex Desktop prompt exceeds the safe handoff limit")
    query = urlencode({"path": project, "prompt": prompt}, quote_via=quote)
    url = f"{destination}?{query}"
    if len(url) > _CODEX_DESKTOP_MAX_URL_CHARS:
        raise SkillMagnetError("Codex Desktop deep link exceeds the safe URL limit")
    return url


def deliver_codex_desktop_prompt(prompt: str, project: str, destination: str) -> None:
    """Ask Windows to open a brand-new Codex Desktop task without a console."""
    if os.name != "nt":
        raise SkillMagnetError("Codex Desktop Explorer handoff is only implemented on Windows")
    url = codex_desktop_deep_link(prompt, project, destination)
    try:
        os.startfile(url)  # type: ignore[attr-defined]
    except OSError as exc:
        raise SkillMagnetError("Codex Desktop could not be opened") from exc


def deliver_prepared_codex_handoff(
    engine: ActivationEngine,
    contract_id: str,
    *,
    delivery: Callable[[str, str, str], None] | None = None,
) -> dict[str, object]:
    """Deliver one prepared prompt and record only handoff readiness."""
    prepared = engine.prepare_codex_desktop_handoff(contract_id)
    opener = delivery or deliver_codex_desktop_prompt
    opener(
        str(prepared["prompt"]),
        str(prepared["project"]),
        str(prepared["destination"]),
    )
    return engine.record_codex_desktop_handoff(prepared)


def web_claude_prefill_url(prompt: str, destination: str) -> str:
    """Build the supported new-conversation URL without touching an existing draft."""
    if destination != _WEB_CLAUDE_DESTINATION:
        raise SkillMagnetError("Unexpected Web Claude destination")
    if not prompt:
        raise SkillMagnetError("Web Claude prompt is empty")
    if len(prompt) > _WEB_CLAUDE_MAX_PROMPT_CHARS:
        raise SkillMagnetError("Web Claude prompt exceeds the safe prefill limit")
    url = f"{destination}?{urlencode({'q': prompt})}"
    if len(url) > _WEB_CLAUDE_MAX_URL_CHARS:
        raise SkillMagnetError("Web Claude prefill URL exceeds the safe URL limit")
    return url


def deliver_web_claude_prompt(prompt: str, destination: str) -> None:
    """Open a new Web Claude conversation prefilled with the complete prompt."""
    if os.name != "nt":
        raise SkillMagnetError("Web Claude Explorer handoff is only implemented on Windows")
    url = web_claude_prefill_url(prompt, destination)
    try:
        if not webbrowser.open(url, new=2):
            raise SkillMagnetError("Web Claude could not be opened")
    except SkillMagnetError:
        raise
    except Exception as exc:
        raise SkillMagnetError("Web Claude could not be opened") from exc


def context_selection_details(
    engine: ActivationEngine,
    *,
    project: Path,
    pack_id: str,
    skill_id: str | None = None,
    runtime: str,
    menu_commit: str | None = None,
    menu_skill_digest: str | None = None,
    menu_instruction_digest: str | None = None,
    menu_acceptance_digest: str | None = None,
) -> dict[str, object]:
    if pack_id not in engine.config.packs:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_pack"
        )
        raise SkillMagnetError(f"Unknown skill pack: {pack_id}")
    if runtime not in {"codex", "claude"}:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_runtime"
        )
        raise SkillMagnetError(f"Unknown target AI: {runtime}")
    pack = engine.config.packs[pack_id]
    if skill_id is not None and skill_id not in pack.skills:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_skill"
        )
        raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
    skill_digest = hashlib.sha256(
        json.dumps(pack.skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if menu_commit is not None and menu_commit != pack.expected_commit:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_commit"
        )
        raise SkillMagnetError("Pack version changed after menu installation; reinstall required")
    if menu_skill_digest is not None and menu_skill_digest != skill_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_membership"
        )
        raise SkillMagnetError("Pack membership changed after menu installation; reinstall required")
    if pack.selection_kind == "package" and skill_id is not None:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="invalid_package_selection"
        )
        raise SkillMagnetError(f"Pack {pack_id} must be selected as a complete package")
    selected_skills = (skill_id,) if skill_id is not None else pack.skills

    def selection_digest(filename: str) -> str:
        if skill_id is not None:
            return engine.approved_blob_digest(pack, skill_id, filename)
        payload = {
            selected: engine.approved_blob_digest(pack, selected, filename)
            for selected in pack.skills
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    instruction_digest = selection_digest("SKILL.md")
    acceptance_digest = selection_digest("acceptance.json")
    if menu_instruction_digest is not None and menu_instruction_digest != instruction_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_instruction"
        )
        raise SkillMagnetError("Skill instructions changed after menu installation; reinstall required")
    if menu_acceptance_digest is not None and menu_acceptance_digest != acceptance_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_acceptance"
        )
        raise SkillMagnetError("Skill acceptance changed after menu installation; reinstall required")
    return {
        "selection_kind": "skill" if skill_id is not None else "pack",
        "selected_skill_id": skill_id,
        "project": str(project.resolve()),
        "pack_id": pack.pack_id,
        "skill_count": len(selected_skills),
        "skill_ids": selected_skills,
        "skill_ids_digest": skill_digest,
        "instruction_digest": instruction_digest,
        "acceptance_digest": acceptance_digest,
        "runtime": runtime,
        "repository_url": pack.repo_url,
        "expected_commit": pack.expected_commit,
        "approved_by": pack.approved_by,
        "approved_at": pack.approved_at,
        "purpose": pack.purpose,
        "skill_display_name": (
            pack.skill_display_name(skill_id) if skill_id is not None else pack.menu_label
        ),
        "skill_purpose": (
            pack.skill_purpose(skill_id) if skill_id is not None else pack.purpose
        ),
        "all_skill_ids": pack.skills,
        "verified_runtime": runtime in engine.SUPPORTED_RUNTIMES,
    }


def confirm_context_selection(
    engine: ActivationEngine,
    *,
    platform: str,
    details: dict[str, object],
    purpose: str,
    confirmed: bool,
) -> LaunchContract | None:
    """Create no state until the user has explicitly accepted the immutable selection."""
    if not confirmed:
        return None
    if not details["verified_runtime"]:
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="unsupported_runtime",
        )
        raise SkillMagnetError(
            f"{str(details['runtime']).title()} has no verified runtime adapter; launch blocked"
        )
    try:
        plan = engine.plan(
            platform=platform,
            project=Path(str(details["project"])),
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            purpose=purpose,
            skill_id=(
                str(details["selected_skill_id"])
                if details["selection_kind"] == "skill"
                else None
            ),
        )
    except SkillMagnetError:
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="preflight_validation_failed",
        )
        raise
    if tuple(plan["skill_ids"]) != tuple(details["skill_ids"]):
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="stale_menu_membership",
        )
        raise SkillMagnetError("Pack membership changed after menu selection; reinstall required")
    return engine.confirm(plan, confirmed=True)


def launch_context_leaf(
    engine: ActivationEngine,
    *,
    platform: str,
    project: Path,
    pack_id: str,
    skill_id: str,
    runtime: str,
    menu_commit: str,
    menu_skill_digest: str,
    menu_instruction_digest: str,
    menu_acceptance_digest: str,
    codex_executable: str | tuple[str, ...] = "codex",
    interactive_handoff: bool = False,
    destination: str = "verified_runtime",
    web_delivery: Callable[[str, str], None] | None = None,
    desktop_delivery: Callable[[str, str, str], None] | None = None,
    error_ui: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Execute one explicit leaf silently; the leaf selection is the consent event."""
    existing_rejections = set(engine.events_dir.glob("*-rejected.json"))
    try:
        details = context_selection_details(
            engine,
            project=project,
            pack_id=pack_id,
            skill_id=skill_id,
            runtime=runtime,
            menu_commit=menu_commit,
            menu_skill_digest=menu_skill_digest,
            menu_instruction_digest=menu_instruction_digest,
            menu_acceptance_digest=menu_acceptance_digest,
        )
        contract = confirm_context_selection(
            engine,
            platform=platform,
            details=details,
            purpose=str(details["purpose"]),
            confirmed=True,
        )
    except SkillMagnetError as exc:
        if set(engine.events_dir.glob("*-rejected.json")) == existing_rejections:
            engine.record_rejection(
                pack_id=pack_id,
                runtime=runtime,
                reason="preflight_validation_failed",
            )
        if error_ui is not None:
            error_ui(context_error_message(exc))
        raise
    if contract is None:  # Defensive: confirmed=True must always return a contract.
        raise SkillMagnetError("Explicit leaf did not create a launch contract")
    try:
        if runtime == "codex":
            return deliver_prepared_codex_handoff(
                engine,
                contract.contract_id,
                delivery=desktop_delivery,
            )
        if destination == "web":
            handoff = engine.prepare_web_handoff(contract.contract_id)
            delivery = web_delivery or deliver_web_claude_prompt
            delivery(str(handoff["prompt"]), str(handoff["destination"]))
            return {key: value for key, value in handoff.items() if key != "prompt"}
        return engine.execute(
            contract.contract_id,
            codex_executable=codex_executable,
            interactive_handoff=interactive_handoff,
        )
    except SkillMagnetError as exc:
        if error_ui is not None:
            error_ui(context_error_message(exc))
        raise


def show_context_selection(
    engine: ActivationEngine,
    *,
    platform: str,
    project: Path,
    pack_id: str | None = None,
    skill_id: str | None = None,
    runtime: str | None = None,
    menu_commit: str | None = None,
    menu_skill_digest: str | None = None,
    menu_instruction_digest: str | None = None,
    menu_acceptance_digest: str | None = None,
) -> LaunchContract | None:
    """Show one pack-first confirmation UI for both OS adapters."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.resizable(True, True)
    if platform == "windows" and any(
        value is None
        for value in (
            pack_id,
            menu_commit,
            menu_skill_digest,
            menu_instruction_digest,
            menu_acceptance_digest,
        )
    ):
        raise SkillMagnetError(
            "Windows context launch requires an explicit pack and installed menu version"
        )
    if (
        platform == "windows"
        and pack_id is not None
        and engine.config.packs[pack_id].selection_kind == "skill"
        and skill_id is None
    ):
        raise SkillMagnetError("Windows context launch requires an explicit skill")
    if platform == "windows":
        context_selection_details(
            engine,
            project=project,
            pack_id=pack_id,
            skill_id=skill_id,
            runtime=runtime or "codex",
            menu_commit=menu_commit,
            menu_skill_digest=menu_skill_digest,
            menu_instruction_digest=menu_instruction_digest,
            menu_acceptance_digest=menu_acceptance_digest,
        )
    selection_choices: dict[str, tuple[str, str | None]] = {}
    for candidate_pack in engine.config.packs.values():
        if candidate_pack.selection_kind == "package":
            label = candidate_pack.menu_label
            if label in selection_choices:
                label = f"{label} ({candidate_pack.pack_id})"
            selection_choices[label] = (candidate_pack.pack_id, None)
            continue
        for candidate_skill in candidate_pack.skills:
            label = candidate_pack.skill_display_name(candidate_skill)
            if label in selection_choices:
                label = f"{label} ({candidate_pack.menu_label})"
            selection_choices[label] = (candidate_pack.pack_id, candidate_skill)
    selected_pack = tk.StringVar(value=pack_id or "")
    selected_skill = tk.StringVar(value=skill_id or "")
    selected_skill_label = tk.StringVar()
    selected_runtime = tk.StringVar(value=runtime.title() if runtime else "")
    purpose = tk.StringVar()
    language_choice = tk.StringVar(value="日本語")
    language_label = tk.StringVar()
    project_label = tk.StringVar()
    selection_label = tk.StringVar()
    skill_purpose_label = tk.StringVar()
    runtime_label = tk.StringVar()
    request_label = tk.StringVar()
    verification_label = tk.StringVar()
    details_text = tk.StringVar()
    details_button_text = tk.StringVar()
    result: dict[str, LaunchContract] = {}
    details_visible = False

    def current_language() -> str:
        return "en" if language_choice.get() == "English" else "ja"

    ttk.Label(root, textvariable=project_label).grid(
        row=0, column=0, columnspan=2, padx=12, pady=8, sticky="w"
    )
    ttk.Label(root, textvariable=language_label).grid(row=0, column=2, padx=6, sticky="e")
    language_box = ttk.Combobox(
        root,
        textvariable=language_choice,
        values=("日本語", "English"),
        state="readonly",
        width=10,
    )
    language_box.grid(row=0, column=3, padx=12, pady=8, sticky="w")
    root.columnconfigure(1, weight=1)
    ttk.Label(root, textvariable=selection_label).grid(
        row=1, column=0, padx=12, sticky="w"
    )
    if pack_id is not None:
        pack = engine.config.packs[pack_id or ""]
        selected_skill_label.set(
            pack.skill_display_name(skill_id) if skill_id is not None else pack.menu_label
        )
        ttk.Label(root, textvariable=selected_skill_label).grid(
            row=1, column=1, columnspan=3, padx=12, sticky="w"
        )
    else:
        skill_box = ttk.Combobox(
            root,
            textvariable=selected_skill_label,
            values=tuple(selection_choices),
            state="readonly",
            width=42,
        )
        skill_box.grid(row=1, column=1, columnspan=3, padx=12, pady=4, sticky="ew")

        def choose_skill(_: object = None) -> None:
            selected = selection_choices.get(selected_skill_label.get())
            if selected is None:
                return
            selected_pack.set(selected[0])
            selected_skill.set(selected[1] or "")
            refresh_selection()

        skill_box.bind("<<ComboboxSelected>>", choose_skill)

    ttk.Label(root, textvariable=skill_purpose_label, wraplength=560).grid(
        row=2, column=0, columnspan=4, padx=12, pady=(4, 8), sticky="w"
    )
    ttk.Label(root, textvariable=runtime_label).grid(
        row=3, column=0, padx=12, sticky="w"
    )
    runtime_box = ttk.Combobox(
        root,
        textvariable=selected_runtime,
        values=("Codex", "Claude"),
        state="readonly",
    )
    runtime_box.grid(row=3, column=1, columnspan=3, padx=12, pady=4, sticky="w")
    ttk.Label(root, textvariable=request_label).grid(
        row=4, column=0, padx=12, sticky="w"
    )
    ttk.Entry(root, textvariable=purpose, width=48).grid(
        row=4, column=1, columnspan=3, padx=12, pady=4, sticky="w"
    )
    ttk.Label(root, textvariable=verification_label, wraplength=560).grid(
        row=5, column=0, columnspan=4, padx=12, pady=8, sticky="w"
    )

    details_frame = ttk.LabelFrame(root)
    ttk.Label(details_frame, textvariable=details_text, wraplength=560).grid(
        row=0, column=0, padx=8, pady=8, sticky="w"
    )

    def toggle_details() -> None:
        nonlocal details_visible
        details_visible = not details_visible
        if details_visible:
            details_frame.grid(row=7, column=0, columnspan=4, padx=12, pady=4, sticky="ew")
        else:
            details_frame.grid_remove()
        details_button_text.set(
            context_ui_text(
                current_language(), "details_hide" if details_visible else "details_show"
            )
        )

    details_button = ttk.Button(root, textvariable=details_button_text, command=toggle_details)
    details_button.grid(row=6, column=0, columnspan=4, padx=12, pady=4, sticky="w")

    confirm_button = ttk.Button(root)
    cancel_button = ttk.Button(root, command=root.destroy)

    def refresh_selection() -> None:
        if not selected_pack.get():
            skill_purpose_label.set(context_ui_text(current_language(), "select_pack"))
            details_text.set("")
            return
        pack = engine.config.packs[selected_pack.get()]
        skill = selected_skill.get() or None
        skill_purpose_label.set(
            context_ui_text(
                current_language(),
                "skill_purpose",
                purpose=pack.skill_purpose(skill) if skill is not None else pack.purpose,
            )
        )
        details_text.set(
            "\n".join(
                (
                    context_ui_text(
                        current_language(),
                        "internal_skill_id",
                        skill_id=", ".join((skill,)) if skill is not None else ", ".join(pack.skills),
                    ),
                    context_ui_text(current_language(), "pack_id", pack_id=pack.pack_id),
                    context_ui_text(
                        current_language(),
                        "included_skills",
                        count=len(pack.skills),
                        skills=", ".join(pack.skills),
                    ),
                    context_ui_text(current_language(), "repository", repository=pack.repo_url),
                    context_ui_text(current_language(), "version", version=pack.expected_commit),
                    context_ui_text(
                        current_language(),
                        "approved",
                        approved_by=pack.approved_by,
                        approved_at=pack.approved_at,
                    ),
                    context_ui_text(
                        current_language(),
                        "digests",
                        skill_ids_digest=menu_skill_digest or "-",
                        instruction_digest=menu_instruction_digest or "-",
                        acceptance_digest=menu_acceptance_digest or "-",
                    ),
                )
            )
        )

    def apply_language(_: object = None) -> None:
        global _context_ui_language
        language = current_language()
        _context_ui_language = language
        root.title(context_ui_text(language, "window_title"))
        language_label.set(context_ui_text(language, "language"))
        project_label.set(
            context_ui_text(language, "project", project=project.resolve())
        )
        selection_label.set(context_ui_text(language, "selection"))
        runtime_label.set(context_ui_text(language, "target_ai"))
        request_label.set(context_ui_text(language, "actual_request"))
        verification_label.set(context_ui_text(language, "verification"))
        confirm_button.configure(text=context_ui_text(language, "confirm_button"))
        cancel_button.configure(text=context_ui_text(language, "cancel_button"))
        details_frame.configure(text=context_ui_text(language, "details_title"))
        details_button_text.set(
            context_ui_text(language, "details_hide" if details_visible else "details_show")
        )
        refresh_selection()

    language_box.bind("<<ComboboxSelected>>", apply_language)

    def confirm() -> None:
        language = current_language()
        request_error = context_ui_request_error(language, purpose.get())
        if request_error is not None:
            messagebox.showerror(
                context_ui_text(language, "error_title"),
                request_error,
                parent=root,
            )
            return
        runtime_value = selected_runtime.get().casefold()
        if runtime_value not in {"codex", "claude"}:
            messagebox.showerror(
                context_ui_text(language, "error_title"),
                context_ui_text(language, "select_runtime"),
                parent=root,
            )
            return
        if not selected_pack.get():
            messagebox.showerror(
                context_ui_text(language, "error_title"),
                context_ui_text(language, "select_pack"),
                parent=root,
            )
            return
        try:
            details = context_selection_details(
                engine,
                project=project,
                pack_id=selected_pack.get(),
                skill_id=selected_skill.get() or None,
                runtime=runtime_value,
                menu_commit=menu_commit,
                menu_skill_digest=menu_skill_digest,
                menu_instruction_digest=menu_instruction_digest,
                menu_acceptance_digest=menu_acceptance_digest,
            )
        except Exception as exc:
            messagebox.showerror(
                context_ui_text(language, "error_title"),
                f"{context_ui_text(language, 'operation_failed')}\n\n{exc}",
                parent=root,
            )
            return
        detail = context_ui_confirmation(language, details, purpose.get())
        if not messagebox.askyesno(
            context_ui_text(language, "confirmation_title"), detail, parent=root
        ):
            return
        try:
            contract = confirm_context_selection(
                engine,
                platform=platform,
                details=details,
                purpose=purpose.get(),
                confirmed=True,
            )
        except Exception as exc:
            messagebox.showerror(
                context_ui_text(language, "error_title"),
                f"{context_ui_text(language, 'operation_failed')}\n\n{exc}",
                parent=root,
            )
            return
        if contract is not None:
            result["contract"] = contract
        root.destroy()

    confirm_button.configure(command=confirm)
    confirm_button.grid(row=8, column=0, columnspan=2, padx=12, pady=12)
    cancel_button.grid(row=8, column=2, columnspan=2, padx=12, pady=12)
    apply_language()
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return result.get("contract")
