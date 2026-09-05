from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

from .activation import (
    ActivationEngine,
    LaunchContract,
    _AcceptanceFailed,
    _CleanupFailed,
    _LaunchFailed,
    _OutputFailed,
    _RuntimeFailed,
    validate_task_workspace,
)
from .core import SkillMagnetError, normalize_display_text


def start_context_background_operation(
    operation: Callable[[threading.Event], Any],
    *,
    name: str,
    cancel_event: threading.Event | None = None,
) -> tuple[threading.Event, threading.Thread, dict[str, Any]]:
    """Run context validation without blocking Tk's event thread.

    The event is deliberately exposed to the window lifecycle even though the
    current GitHub archive reader can only observe cancellation between bounded
    network calls.  A daemon worker therefore never keeps a closed Explorer
    launcher alive, and no Tk object is accessed from the worker thread.
    """

    event = cancel_event or threading.Event()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            if event.is_set():
                raise SkillMagnetError("操作は開始前に取り消されました")
            value = operation(event)
            if event.is_set():
                raise SkillMagnetError("操作は取り消されました")
            outcome["value"] = value
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=work, name=name, daemon=True)
    worker.start()
    return event, worker, outcome


@dataclass
class ContextUiLease:
    """Process-wide lease for the Explorer launcher UI.

    Explorer can dispatch the same command repeatedly while Python is still
    starting. The OS file lock is released automatically after a crash, so a
    stale owner record can never make the UI permanently unavailable.
    """

    path: Path
    acquired: bool
    owner: dict[str, Any]
    handle: Any | None = None
    owner_path: Path | None = None

    def publish_window(self, *, phase: str, window_handle: int) -> None:
        """Atomically retarget duplicate launches to the currently visible UI.

        The unified chooser and Library Manager run sequentially in one process
        while this lease remains held.  Keeping the destroyed chooser's HWND in
        the owner record makes a repeated Explorer click look unrecoverable even
        though Library Manager is alive.  Publish every phase transition through
        the locked record that competing processes already read.
        """

        if not self.acquired or self.handle is None:
            raise SkillMagnetError("Context UI lease is not owned by this process")
        if phase not in {"context_selection", "library_manager"}:
            raise SkillMagnetError(f"Unknown context UI lease phase: {phase}")
        if not isinstance(window_handle, int) or window_handle <= 0:
            raise SkillMagnetError("Visible UI window handle is unavailable")
        payload = dict(self.owner)
        payload.update(phase=phase, window_handle=window_handle)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.handle.seek(1)
        self.handle.truncate()
        self.handle.write(encoded)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        if self.owner_path is not None:
            temporary = self.owner_path.with_name(
                f".{self.owner_path.name}.{payload['generation']}.tmp"
            )
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.owner_path)
            finally:
                temporary.unlink(missing_ok=True)
        self.owner = payload

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self.owner_path is not None:
                try:
                    self.owner_path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            try:
                if self.handle is not None:
                    try:
                        _unlock_context_ui_file(self.handle)
                    except OSError:
                        pass
            finally:
                if self.handle is not None:
                    try:
                        self.handle.close()
                    except OSError:
                        pass
                self.handle = None
                self.acquired = False


def _try_lock_context_ui_file(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


@dataclass(frozen=True)
class ContextUiAction:
    name: str


def _unlock_context_ui_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_context_ui_lease(state_dir: Path, project: Path) -> ContextUiLease:
    """Allow one root-launcher process and recover automatically after exit."""

    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "context-launcher.lock"
    owner_path = state_dir / "context-launcher.owner.json"
    payload = {
        "pid": os.getpid(),
        "project": str(project.resolve()),
        "generation": os.urandom(16).hex(),
        "phase": "context_starting",
        "window_handle": 0,
    }
    path.touch(exist_ok=True)
    handle = path.open("r+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    if _try_lock_context_ui_file(handle):
        handle.seek(1)
        handle.truncate()
        handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        owner_path.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return ContextUiLease(path, True, payload, handle, owner_path)
    owner: dict[str, Any] = {}
    for _ in range(10):
        try:
            with path.open("rb") as reader:
                reader.seek(1)
                owner = json.loads(reader.read().decode("utf-8"))
        except (OSError, ValueError):
            owner = {}
        if _try_lock_context_ui_file(handle):
            handle.seek(1)
            handle.truncate()
            handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            owner_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return ContextUiLease(path, True, payload, handle, owner_path)
        time.sleep(0.02)
    owner["same_request"] = bool(
        os.path.normcase(os.path.normpath(str(payload["project"])))
        == os.path.normcase(os.path.normpath(str(owner.get("project", ""))))
    )
    handle.close()
    return ContextUiLease(path, False, owner, owner_path=owner_path)


def focus_context_ui(owner: dict[str, Any]) -> bool:
    """Bring the already-running launcher window forward on Windows."""

    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        pid = int(owner.get("pid", 0))
        if pid <= 0:
            return False
        user32 = ctypes.windll.user32
        candidates: list[tuple[int, str]] = []

        def top_level(hwnd: int) -> int:
            try:
                root_hwnd = int(user32.GetAncestor(hwnd, 2))  # GA_ROOT
            except (AttributeError, TypeError, ValueError):
                root_hwnd = 0
            return root_hwnd or hwnd

        def owned_visible(hwnd: int) -> bool:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            return process_id.value == pid and bool(user32.IsWindowVisible(hwnd))

        def title(hwnd: int) -> str:
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return ""
            value = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, value, length + 1)
            return value.value

        preferred = owner.get("window_handle")
        if isinstance(preferred, int) and preferred > 0:
            preferred = top_level(preferred)
            if owned_visible(preferred):
                candidates.append((preferred, title(preferred)))
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        @callback_type
        def collect(hwnd: int, _: int) -> bool:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            candidate = int(hwnd)
            if process_id.value == pid and user32.IsWindowVisible(hwnd) and all(
                existing[0] != candidate for existing in candidates
            ):
                candidates.append((candidate, title(candidate)))
            return True

        user32.EnumWindows(collect, 0)
        if not candidates:
            return False
        phase = str(owner.get("phase", ""))
        expected_title = "Library Manager" if phase == "library_manager" else "Skill Magnet"
        candidates.sort(
            key=lambda candidate: (
                expected_title.casefold() not in candidate[1].casefold(),
                candidate[0] != preferred,
            )
        )
        hwnd = candidates[0][0]
        user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        # Windows can reject foreground transfer for focus-stealing policy even
        # after locating and restoring the correct live window.  The duplicate
        # still must not advise killing that live owner process.
        return bool(user32.IsWindowVisible(hwnd))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


_CONTEXT_UI_TEXT = {
    "ja": {
        "window_title": "Skill Magnet — 実行確認",
        "language": "言語",
        "project": "作業対象フォルダー: {project}",
        "projectless": "指定なし（デスクトップアプリが新規タスク用領域を自動作成）",
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
        "verification": "全スキルを読み、trigger/boundaryと存在する場合のINDEX関係から選んだスキルを、依頼の実作業へ適用します。",
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
        "confirmation_project": "作業対象フォルダー: {project}",
        "confirmation_request": "実際の依頼: {purpose}",
        "confirmation_question": "この内容で依頼を実行しますか？",
        "operation_failed": "処理を開始できませんでした。",
    },
    "en": {
        "window_title": "Skill Magnet — Launch confirmation",
        "language": "Language",
        "project": "Task workspace: {project}",
        "projectless": "None (the Desktop app creates a new task workspace)",
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
        "verification": "Read every skill, use INDEX relations when present, and apply the selected rules to the actual work.",
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
        "confirmation_project": "Task workspace: {project}",
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
    project = details["project"]
    project_display = (
        project
        if isinstance(project, str)
        else context_ui_text(language, "projectless")
    )
    lines = (
        context_ui_text(language, "confirmation_selection", selection=selection),
        context_ui_text(
            language, "confirmation_ai", runtime=str(details["runtime"]).title()
        ),
        context_ui_text(language, "confirmation_project", project=project_display),
        context_ui_text(language, "confirmation_request", purpose=purpose),
    )
    return "\n".join((*lines, "", context_ui_text(language, "confirmation_question")))


def context_ui_request_error(language: str, purpose: str) -> str | None:
    """Validate only the UI input; the accepted value is passed through unchanged."""
    return None if purpose.strip() else context_ui_text(language, "empty_request")


def context_selection_choice_map(
    engine: ActivationEngine,
) -> dict[str, tuple[str, str | None]]:
    """Build unique user labels without leaking internal IDs into the selector."""

    candidates: list[tuple[str, str, str | None]] = []
    for pack in engine.config.packs.values():
        if pack.selection_kind == "package":
            candidates.append(
                (f"Skill Pack: {normalize_display_text(pack.menu_label)}", pack.pack_id, None)
            )
        else:
            candidates.extend(
                (
                    f"Skill: {normalize_display_text(pack.skill_display_name(skill))}",
                    pack.pack_id,
                    skill,
                )
                for skill in pack.skills
            )
    counts: dict[str, int] = {}
    for base, _, _ in candidates:
        counts[base] = counts.get(base, 0) + 1
    ordinals: dict[str, int] = {}
    choices: dict[str, tuple[str, str | None]] = {}
    reserved = set(counts)
    for base, candidate_pack, candidate_skill in candidates:
        ordinals[base] = ordinals.get(base, 0) + 1
        if counts[base] == 1:
            label = base
        else:
            label = f"{base} （同名 {ordinals[base]}）"
            collision = 1
            while label in reserved or label in choices:
                label = f"{base} （同名 {ordinals[base]}・候補 {collision}）"
                collision += 1
        choices[label] = (candidate_pack, candidate_skill)
    return choices


def context_ui_details(language: str, details: dict[str, object]) -> str:
    """Format verified selection details; placeholders are never release evidence."""

    skill_ids = tuple(str(item) for item in details["skill_ids"])
    return "\n".join(
        (
            context_ui_text(
                language, "internal_skill_id", skill_id=", ".join(skill_ids)
            ),
            context_ui_text(language, "pack_id", pack_id=details["pack_id"]),
            context_ui_text(
                language,
                "included_skills",
                count=details["skill_count"],
                skills=", ".join(skill_ids),
            ),
            context_ui_text(
                language, "repository", repository=details["repository_url"]
            ),
            context_ui_text(language, "version", version=details["expected_commit"]),
            context_ui_text(
                language,
                "approved",
                approved_by=details["approved_by"],
                approved_at=details["approved_at"],
            ),
            context_ui_text(
                language,
                "digests",
                skill_ids_digest=details["skill_ids_digest"],
                instruction_digest=details["instruction_digest"],
                acceptance_digest=details["acceptance_digest"],
            ),
        )
    )


def context_error_message(error: Exception | str, language: str | None = None) -> str:
    language = language or _context_ui_language
    message = str(error)
    if "Pack HEAD is not the pinned expected_commit" in message:
        if language == "en":
            message += (
                "\n\nUpdate safely: review and approve the new source commit; update the "
                "configured expected commit and skill digests in Library Manager; close and "
                "reopen the Skill Magnet selection screen; then retry from a clean source HEAD."
            )
        else:
            message = (
                "Skillパックの現在のHEADが、承認済みコミットと一致しません。"
                "\n\n安全に更新するには、新しいsource commitを確認・承認し、設定済みの"
                "expected commitとSkill digestをLibrary Managerで更新してください。"
                "その後、Skill Magnetの選択画面を閉じて開き直し、cleanなsource HEADで"
                "再試行してください。packやskillの内容変更だけでは右クリックメニューの"
                "再インストールは不要です。"
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


def context_failure_surface(
    error: Exception,
    *,
    config_path: Path | None = None,
    state_dir: Path | None = None,
    platform: str | None = None,
) -> dict[str, str]:
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
    message = str(error).strip() or error.__class__.__name__
    folded = message.casefold()
    repair_argv = [sys.executable, "-I", "-m", "skill_magnet"]
    if config_path is not None:
        repair_argv.extend(("--config", str(config_path.resolve())))
        if state_dir is not None:
            repair_argv.extend(("--state-dir", str(state_dir.resolve())))
    repair_argv.extend(("library", "ui"))
    repair_command = subprocess.list2cmdline(repair_argv)
    menu_repair_argv = [sys.executable, "-I", "-m", "skill_magnet"]
    if config_path is not None:
        menu_repair_argv.extend(("--config", str(config_path.resolve())))
    if state_dir is not None:
        menu_repair_argv.extend(("--state-dir", str(state_dir.resolve())))
    repair_platform = platform or ("windows" if os.name == "nt" else "macos")
    menu_repair_argv.extend(
        ("install-context-menu", "--platform", repair_platform, "--confirm")
    )
    menu_repair_command = subprocess.list2cmdline(menu_repair_argv)
    terminal_name = "Windows Terminal" if repair_platform == "windows" else "Terminal"
    if "after menu installation" in folded or "reinstall required" in folded:
        next_action = (
            f"{terminal_name}で「{menu_repair_command}」を一度実行し、"
            "完了後に同じ右クリック操作を再試行してください。"
            "Library Managerも同じSkill Magnet画面から開けます。"
        )
    elif "selection screen" in folded or "while confirming" in folded:
        next_action = (
            "現在のSkill Magnet画面を閉じ、対象フォルダーを右クリックして"
            "「Skill Magnet」をもう一度開いてください。現在の設定から選択肢を読み直します。"
            "packやskillの内容変更だけでは右クリックメニューの再インストールは不要です。"
        )
    elif "config" in folded or "json" in folded or "設定" in message:
        next_action = (
            f"{terminal_name}で「{repair_command}」を実行して"
            "Library Managerを開き、GitHub URLと登録内容を修復してから再実行してください。"
        )
    elif "library manager" in folded:
        next_action = (
            f"{terminal_name}で「{repair_command}」を再実行してください。"
            "同じ原因が表示される場合は、表示されたパスの書き込み権限または空き容量を"
            "修復してから再実行してください。"
        )
    elif "workspace" in folded or "folder" in folded or "directory" in folded:
        next_action = (
            "対象フォルダーそのものを右クリックするか、そのフォルダーを開いた状態で"
            "余白を右クリックして再実行してください。"
        )
    elif "interrupted" in folded or "transaction" in folded or "attempt" in folded:
        next_action = (
            "右クリックの「Skill Magnet」を押し、開いた画面の「Library Manager」で"
            "表示された中断処理を「続きから再開」または「最初からやり直す」で復旧してください。"
        )
    else:
        next_action = (
            f"{terminal_name}で「{repair_command}」を実行し、"
            "画面の復旧操作を実行してください。解消しない場合は、この原因文を"
            "そのまま対応報告へ添付してください。"
        )
    return {
        "state": "blocked",
        "title": "実行を続けられません",
        "cause": message,
        "not_completed": "依頼は完了扱いにしていません。",
        "next_action": next_action,
    }


def context_failure_message(
    error: Exception,
    *,
    config_path: Path | None = None,
    state_dir: Path | None = None,
    platform: str | None = None,
) -> str:
    surface = context_failure_surface(
        error, config_path=config_path, state_dir=state_dir, platform=platform
    )
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


_CLAUDE_DESKTOP_DESTINATION = "claude://code/new"
_CLAUDE_DESKTOP_MAX_PROMPT_CHARS = 12_000
_CLAUDE_DESKTOP_MAX_URL_CHARS = 32_767

_CODEX_DESKTOP_DESTINATION = "codex://threads/new"
_CODEX_DESKTOP_MAX_PROMPT_CHARS = 12_000
_CODEX_DESKTOP_MAX_URL_CHARS = 32_767


def codex_desktop_deep_link(
    prompt: str, project: str | None, destination: str
) -> str:
    """Build the canonical new-task deep link with lossless URL encoding."""
    if destination != _CODEX_DESKTOP_DESTINATION:
        raise SkillMagnetError("Unexpected Codex Desktop destination")
    if not prompt:
        raise SkillMagnetError("Codex Desktop prompt is empty")
    if len(prompt) > _CODEX_DESKTOP_MAX_PROMPT_CHARS:
        raise SkillMagnetError("Codex Desktop prompt exceeds the safe handoff limit")
    values = (
        {"path": project, "prompt": prompt}
        if project is not None
        else {"prompt": prompt}
    )
    query = urlencode(values, quote_via=quote)
    url = f"{destination}?{query}"
    if len(url) > _CODEX_DESKTOP_MAX_URL_CHARS:
        raise SkillMagnetError("Codex Desktop deep link exceeds the safe URL limit")
    return url


def deliver_codex_desktop_prompt(
    prompt: str, project: str | None, destination: str
) -> None:
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
    delivery: Callable[[str, str | None, str], None] | None = None,
) -> dict[str, object]:
    """Deliver one prepared prompt and record only handoff readiness."""
    prepared = engine.prepare_codex_desktop_handoff(contract_id)
    opener = delivery or deliver_codex_desktop_prompt
    try:
        opener(
            str(prepared["prompt"]),
            prepared["project"] if isinstance(prepared["project"], str) else None,
            str(prepared["destination"]),
        )
    except Exception:
        engine.record_desktop_launch_failure(prepared)
        raise
    return engine.record_desktop_handoff(prepared)


def claude_desktop_deep_link(
    prompt: str, project: str | None, destination: str
) -> str:
    """Build a new Claude Code Desktop session deep link."""
    if destination != _CLAUDE_DESKTOP_DESTINATION:
        raise SkillMagnetError("Unexpected Claude Desktop destination")
    if not prompt:
        raise SkillMagnetError("Claude Desktop prompt is empty")
    if len(prompt) > _CLAUDE_DESKTOP_MAX_PROMPT_CHARS:
        raise SkillMagnetError("Claude Desktop prompt exceeds the safe handoff limit")
    values = {"q": prompt}
    if project is not None:
        values["folder"] = project
    query = urlencode(values, quote_via=quote)
    url = f"{destination}?{query}"
    if len(url) > _CLAUDE_DESKTOP_MAX_URL_CHARS:
        raise SkillMagnetError("Claude Desktop deep link exceeds the safe URL limit")
    return url


def deliver_claude_desktop_prompt(
    prompt: str, project: str | None, destination: str
) -> None:
    """Open Claude Code in Claude Desktop with the prompt and project prefilled."""
    url = claude_desktop_deep_link(prompt, project, destination)
    try:
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
        elif not webbrowser.open(url, new=2):
            raise SkillMagnetError("Claude Desktop could not be opened")
    except SkillMagnetError:
        raise
    except Exception as exc:
        raise SkillMagnetError("Claude Desktop could not be opened") from exc


def deliver_prepared_claude_handoff(
    engine: ActivationEngine,
    contract_id: str,
    *,
    delivery: Callable[[str, str | None, str], None] | None = None,
) -> dict[str, object]:
    """Deliver one Claude Code Desktop prompt and record handoff readiness."""
    prepared = engine.prepare_claude_desktop_handoff(contract_id)
    opener = delivery or deliver_claude_desktop_prompt
    try:
        opener(
            str(prepared["prompt"]),
            prepared["project"] if isinstance(prepared["project"], str) else None,
            str(prepared["destination"]),
        )
    except Exception:
        engine.record_desktop_launch_failure(prepared)
        raise
    return engine.record_desktop_handoff(prepared)


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
    record_rejections: bool = True,
) -> dict[str, object]:
    project = validate_task_workspace(project)

    def reject(reason: str) -> None:
        if record_rejections:
            engine.record_rejection(pack_id=pack_id, runtime=runtime, reason=reason)

    if pack_id not in engine.config.packs:
        reject("unknown_pack")
        raise SkillMagnetError(f"Unknown skill pack: {pack_id}")
    if runtime not in {"codex", "claude"}:
        reject("unknown_runtime")
        raise SkillMagnetError(f"Unknown target AI: {runtime}")
    pack = engine.config.packs[pack_id]
    if skill_id is not None and skill_id not in pack.skills:
        reject("unknown_skill")
        raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
    pack_membership_digest = hashlib.sha256(
        json.dumps(pack.skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if menu_commit is not None and menu_commit != pack.expected_commit:
        reject("stale_menu_commit")
        raise SkillMagnetError(
            "Pack version changed after the selection screen opened; close and reopen Skill Magnet"
        )
    if menu_skill_digest is not None and menu_skill_digest != pack_membership_digest:
        reject("stale_menu_membership")
        raise SkillMagnetError(
            "Pack membership changed after the selection screen opened; close and reopen Skill Magnet"
        )
    if pack.selection_kind == "package" and skill_id is not None:
        reject("invalid_package_selection")
        raise SkillMagnetError(f"Pack {pack_id} must be selected as a complete package")
    selected_skills = (skill_id,) if skill_id is not None else pack.skills
    selected_skills_digest = hashlib.sha256(
        json.dumps(selected_skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()

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
        reject("stale_menu_instruction")
        raise SkillMagnetError(
            "Skill instructions changed after the selection screen opened; close and reopen Skill Magnet"
        )
    if menu_acceptance_digest is not None and menu_acceptance_digest != acceptance_digest:
        reject("stale_menu_acceptance")
        raise SkillMagnetError(
            "Skill acceptance changed after the selection screen opened; close and reopen Skill Magnet"
        )
    return {
        "selection_kind": "skill" if skill_id is not None else "pack",
        "selected_skill_id": skill_id,
        "project": str(project) if project is not None else None,
        "pack_id": pack.pack_id,
        "skill_count": len(selected_skills),
        "skill_ids": selected_skills,
        "skill_ids_digest": selected_skills_digest,
        "pack_membership_digest": pack_membership_digest,
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
    persist: bool = True,
    record_rejections: bool = True,
) -> LaunchContract | None:
    """Create no state until the user has explicitly accepted the immutable selection."""
    if not confirmed:
        return None
    if not details["verified_runtime"]:
        if record_rejections:
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
            project=(
                Path(str(details["project"]))
                if details["project"] is not None
                else None
            ),
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
        if record_rejections:
            engine.record_rejection(
                pack_id=str(details["pack_id"]),
                runtime=str(details["runtime"]),
                reason="preflight_validation_failed",
            )
        raise
    if tuple(plan["skill_ids"]) != tuple(details["skill_ids"]):
        if record_rejections:
            engine.record_rejection(
                pack_id=str(details["pack_id"]),
                runtime=str(details["runtime"]),
                reason="stale_menu_membership",
            )
        raise SkillMagnetError(
            "Pack membership changed while confirming the selection; close and reopen Skill Magnet"
        )
    if persist:
        return engine.confirm(plan, confirmed=True)
    return engine.prepare_confirmation(plan, confirmed=True)


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
    claude_desktop_delivery: Callable[[str, str | None, str], None] | None = None,
    desktop_delivery: Callable[[str, str | None, str], None] | None = None,
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
        if destination == "desktop":
            return deliver_prepared_claude_handoff(
                engine,
                contract.contract_id,
                delivery=claude_desktop_delivery,
            )
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
    allow_dynamic_selection: bool = False,
    library_manager: Callable[[Path], None] | None = None,
    register_selected: Callable[[Path], None] | None = None,
    window_ready: Callable[[int], None] | None = None,
) -> LaunchContract | ContextUiAction | None:
    """Show one pack-first confirmation UI for both OS adapters."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    normalized_project = validate_task_workspace(project)
    if platform == "windows" and not allow_dynamic_selection and any(
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
        and not allow_dynamic_selection
        and pack_id is not None
        and engine.config.packs[pack_id].selection_kind == "skill"
        and skill_id is None
    ):
        raise SkillMagnetError("Windows context launch requires an explicit skill")
    if platform == "windows" and not allow_dynamic_selection:
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
    root = tk.Tk()
    root.resizable(True, True)
    selection_choices = context_selection_choice_map(engine)
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
    processing_status = tk.StringVar(value="待機中")
    result: dict[str, LaunchContract | ContextUiAction] = {}
    details_visible = False
    verified_details: dict[str, object] | None = None
    active_context_worker: threading.Thread | None = None
    active_context_cancel: threading.Event | None = None
    closing = False

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
            nonlocal verified_details, details_visible
            selected = selection_choices.get(selected_skill_label.get())
            if selected is None:
                return
            selected_pack.set(selected[0])
            selected_skill.set(selected[1] or "")
            verified_details = None
            if details_visible:
                details_visible = False
                details_frame.grid_remove()
                details_button_text.set(
                    context_ui_text(current_language(), "details_show")
                )
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
    request_entry = ttk.Entry(root, textvariable=purpose, width=48)
    request_entry.grid(
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
        nonlocal details_visible, verified_details
        opening = not details_visible
        if opening:
            if not selected_pack.get():
                messagebox.showerror(
                    context_ui_text(current_language(), "error_title"),
                    context_ui_text(current_language(), "select_pack"),
                    parent=root,
                )
                return
            runtime_value = selected_runtime.get().casefold()
            if runtime_value not in {"codex", "claude"}:
                runtime_value = "codex"
            selection = {
                "pack_id": selected_pack.get(),
                "skill_id": selected_skill.get() or None,
                "runtime": runtime_value,
            }

            def load_details(_: threading.Event) -> dict[str, object]:
                return context_selection_details(
                    engine,
                    project=project,
                    pack_id=str(selection["pack_id"]),
                    skill_id=(
                        str(selection["skill_id"])
                        if selection["skill_id"] is not None
                        else None
                    ),
                    runtime=str(selection["runtime"]),
                    menu_commit=menu_commit,
                    menu_skill_digest=menu_skill_digest,
                    menu_instruction_digest=menu_instruction_digest,
                    menu_acceptance_digest=menu_acceptance_digest,
                    record_rejections=False,
                )

            def details_loaded(value: Any) -> None:
                nonlocal details_visible, verified_details
                if not isinstance(value, dict):
                    raise SkillMagnetError("検証結果を読み取れません")
                verified_details = value
                details_text.set(context_ui_details(current_language(), verified_details))
                details_visible = True
                details_frame.grid(
                    row=7, column=0, columnspan=4, padx=12, pady=4, sticky="ew"
                )
                details_button_text.set(
                    context_ui_text(current_language(), "details_hide")
                )

            run_context_background(
                "検証情報を取得しています…",
                load_details,
                details_loaded,
                name="skill-magnet-context-details",
            )
        else:
            details_visible = False
            details_frame.grid_remove()
            details_button_text.set(
                context_ui_text(current_language(), "details_show")
            )

    details_button = ttk.Button(root, textvariable=details_button_text, command=toggle_details)
    details_button.grid(row=6, column=0, columnspan=4, padx=12, pady=4, sticky="w")

    confirm_button = ttk.Button(root)
    cancel_button = ttk.Button(root)
    manager_button = ttk.Button(root, text="Library Manager")
    register_button = ttk.Button(root, text="このフォルダーのスキルを登録")

    controls = [
        language_box,
        runtime_box,
        request_entry,
        details_button,
        confirm_button,
        cancel_button,
        manager_button,
        register_button,
    ]
    if pack_id is None:
        controls.append(skill_box)

    def set_processing(label: str | None) -> None:
        busy = label is not None
        processing_status.set(f"処理中：{label}" if busy else "待機中")
        for control in controls:
            try:
                control.configure(
                    state=(
                        "normal"
                        if busy and control is cancel_button
                        else "disabled"
                        if busy
                        else "normal"
                    )
                )
            except tk.TclError:
                continue
        if not busy:
            language_box.configure(state="readonly")
            runtime_box.configure(state="readonly")
            if pack_id is None:
                skill_box.configure(state="readonly")
        root.update_idletasks()

    def run_context_background(
        label: str,
        operation: Callable[[threading.Event], Any],
        on_success: Callable[[Any], None],
        *,
        name: str,
    ) -> None:
        """Keep archive validation and contract preparation off Tk's main thread."""

        nonlocal active_context_worker, active_context_cancel
        if active_context_worker is not None and active_context_worker.is_alive():
            return
        cancel_event, worker, outcome = start_context_background_operation(
            operation, name=name
        )
        active_context_cancel = cancel_event
        active_context_worker = worker
        set_processing(label)

        def poll() -> None:
            nonlocal active_context_worker, active_context_cancel
            if worker.is_alive():
                if not closing:
                    root.after(50, poll)
                return
            active_context_worker = None
            active_context_cancel = None
            if closing:
                return
            set_processing(None)
            error = outcome.get("error")
            if isinstance(error, BaseException):
                messagebox.showerror(
                    context_ui_text(current_language(), "error_title"),
                    f"{context_ui_text(current_language(), 'operation_failed')}\n\n{error}",
                    parent=root,
                )
                return
            try:
                on_success(outcome.get("value"))
            except Exception as exc:
                messagebox.showerror(
                    context_ui_text(current_language(), "error_title"),
                    f"{context_ui_text(current_language(), 'operation_failed')}\n\n{exc}",
                    parent=root,
                )

        root.after(50, poll)

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
            context_ui_details(current_language(), verified_details)
            if verified_details is not None
            else context_ui_text(current_language(), "details_show")
        )

    def apply_language(_: object = None) -> None:
        global _context_ui_language
        language = current_language()
        _context_ui_language = language
        root.title(context_ui_text(language, "window_title"))
        language_label.set(context_ui_text(language, "language"))
        project_display = (
            normalized_project
            if normalized_project is not None
            else context_ui_text(language, "projectless")
        )
        project_label.set(context_ui_text(language, "project", project=project_display))
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
        request_value = purpose.get()
        request_error = context_ui_request_error(language, request_value)
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
        selection = {
            "language": language,
            "request": request_value,
            "runtime": runtime_value,
            "pack_id": selected_pack.get(),
            "skill_id": selected_skill.get() or None,
        }

        def validate_selection(_: threading.Event) -> dict[str, object]:
            return context_selection_details(
                engine,
                project=project,
                pack_id=str(selection["pack_id"]),
                skill_id=(
                    str(selection["skill_id"])
                    if selection["skill_id"] is not None
                    else None
                ),
                runtime=str(selection["runtime"]),
                menu_commit=menu_commit,
                menu_skill_digest=menu_skill_digest,
                menu_instruction_digest=menu_instruction_digest,
                menu_acceptance_digest=menu_acceptance_digest,
                record_rejections=False,
            )

        def selection_validated(value: Any) -> None:
            if not isinstance(value, dict):
                raise SkillMagnetError("選択内容の検証結果を読み取れません")
            detail = context_ui_confirmation(
                str(selection["language"]), value, str(selection["request"])
            )
            if not messagebox.askyesno(
                context_ui_text(str(selection["language"]), "confirmation_title"),
                detail,
                parent=root,
            ):
                return

            def create_contract(_: threading.Event) -> LaunchContract | None:
                return confirm_context_selection(
                    engine,
                    platform=platform,
                    details=value,
                    purpose=str(selection["request"]),
                    confirmed=True,
                    persist=False,
                    record_rejections=False,
                )

            def contract_created(contract: Any) -> None:
                if not isinstance(contract, LaunchContract):
                    raise SkillMagnetError("依頼の実行契約を作成できませんでした")
                # This is the only state-changing commit in the confirmation
                # flow.  It runs on Tk's main thread, so a close event cannot
                # interleave between the final cancellation check and write.
                result["contract"] = engine.persist_confirmation(contract)
                root.destroy()

            run_context_background(
                "依頼を安全に準備しています…",
                create_contract,
                contract_created,
                name="skill-magnet-context-contract",
            )

        run_context_background(
            "選択内容を検証しています…",
            validate_selection,
            selection_validated,
            name="skill-magnet-context-validation",
        )

    confirm_button.configure(command=confirm)
    if library_manager is not None:
        def open_library_manager() -> None:
            set_processing("Library Managerを開いています…")
            result["action"] = ContextUiAction("library_manager")
            root.destroy()

        manager_button.configure(command=open_library_manager)
        manager_button.grid(row=8, column=0, columnspan=2, padx=12, pady=(8, 0))
    if register_selected is not None:
        def open_registration() -> None:
            set_processing("選択フォルダーを確認しています…")
            result["action"] = ContextUiAction("register_selected")
            root.destroy()

        register_button.configure(command=open_registration)
        register_button.grid(row=8, column=2, columnspan=2, padx=12, pady=(8, 0))
    confirm_button.grid(row=9, column=0, columnspan=2, padx=12, pady=12)
    cancel_button.grid(row=9, column=2, columnspan=2, padx=12, pady=12)
    ttk.Label(root, textvariable=processing_status, anchor="w").grid(
        row=10, column=0, columnspan=4, padx=12, pady=(0, 8), sticky="ew"
    )
    def close_context_window() -> None:
        nonlocal closing
        closing = True
        if active_context_cancel is not None:
            active_context_cancel.set()
        root.destroy()

    cancel_button.configure(command=close_context_window)
    apply_language()
    root.protocol("WM_DELETE_WINDOW", close_context_window)
    if window_ready is not None:
        try:
            root.update_idletasks()
            window_ready(int(root.winfo_id()))
        except Exception:
            root.destroy()
            raise
    root.mainloop()
    return result.get("contract") or result.get("action")
