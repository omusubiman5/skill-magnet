from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .activation import validate_product_state_directory
from .core import Config, SkillMagnetError, _is_link
from .library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LOCAL_MUTATION_FILENAME,
    TERMINAL_STATES,
    LibraryTransaction,
    delete_pack,
    delete_skill,
    discover_skill_sources,
    initialize_library,
    library_mutation_lock,
    find_resumable_transaction,
    list_transactions,
    library_inventory,
    recover_interrupted_library,
    upsert_skill_source,
    update_pack_source,
    update_skill_source,
    validate_library,
    _run as _run_external,
)
from .ui import (
    UI_OWNER_MAX_BYTES,
    UiSurfaceOwnerIdentity,
    UiWidgetSpec,
    _atomic_write_ui_owner_record,
    _is_current_ui_owner,
    _new_ui_owner_record,
    _owner_json_loads,
    _owner_timestamp,
    _path_identity_sha256,
    _read_ui_owner_record,
    _remove_owned_ui_owner_record,
    publish_tk_ui_surface,
    tk_top_level_window_handle,
    ui_surface_owner_identity,
)


LIBRARY_WIZARD_STEPS = (
    "Library Manager",
)

LIBRARY_ACTION_LABELS = {
    "sync": "GitHubへ反映",
    "waiting": "GitHubのマージ待ち",
    "prepare": "送信内容を確認する",
    "publish": "GitHubへ送る",
    "open_pr": "GitHubでPRを開く",
    "reopen_pr": "閉じたPRを再度開く",
    "verify": "GitHubのマージを確認する",
    "activate": "Skill Magnetへ反映",
    "complete": "完了",
}

MANAGED_WORKSPACE_MARKER = "managed-workspace.json"
MANAGED_WORKSPACE_INNER_MARKER = ".skill-magnet.workspace-owner.json"


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute path without following the leaf symlink/junction."""

    return Path(os.path.abspath(os.fspath(path)))


def _expected_managed_repository(state_dir: Path) -> Path:
    state_dir = validate_product_state_directory(state_dir)
    return _lexical_absolute(state_dir / "library" / DEFAULT_REPOSITORY_NAME)


def _require_managed_repository_boundary(
    state_dir: Path, repository: Path | None = None
) -> Path:
    """Bind the managed workspace to its lexical app-owned path.

    Resolving the final path before comparing it makes a junction target look
    like the expected workspace and can turn cleanup into an out-of-tree delete.
    Compare lexically first and reject every existing indirection below the
    already validated product-state root.
    """

    state_dir = validate_product_state_directory(state_dir)
    expected = _expected_managed_repository(state_dir)
    candidate = _lexical_absolute(repository if repository is not None else expected)
    if os.path.normcase(str(candidate)) != os.path.normcase(str(expected)):
        raise SkillMagnetError(
            f"製品管理外のフォルダーを一時ライブラリにはできません: {candidate}"
        )
    for entry in (expected.parent, expected):
        if os.path.lexists(entry) and _is_link(entry):
            raise SkillMagnetError(
                "Library Managerの一時領域がリンクまたはjunctionです。"
                f"外部フォルダーは使用・削除しません: {entry}"
            )
    return expected


@dataclass
class LibraryUiLease:
    path: Path
    acquired: bool
    owner: dict[str, Any]
    handle: Any | None = None
    owner_path: Path | None = None

    @property
    def same_request(self) -> bool:
        return bool(self.owner.get("same_request"))

    def publish_window(self, window_handle: int) -> None:
        """Retarget repeated direct launches to this live Manager window."""

        if not self.acquired or self.handle is None:
            raise SkillMagnetError("Library Manager lease is not owned by this process")
        if not isinstance(window_handle, int) or window_handle <= 0:
            raise SkillMagnetError("Library Manager window handle is unavailable")
        payload = dict(self.owner)
        if self.owner_path is not None and self.owner_path.exists():
            current = _read_ui_owner_record(self.owner_path)
            if not _is_current_ui_owner(current, self.owner, require_window=False):
                raise SkillMagnetError("Library Manager owner changed before publication")
            payload = current
        payload.pop("ui_surface", None)
        payload.update(
            phase="library_manager",
            window_handle=window_handle,
            revision=int(payload.get("revision", 0)) + 1,
            published_at_utc=_owner_timestamp(),
        )
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.handle.seek(1)
        self.handle.truncate()
        self.handle.write(encoded)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        if self.owner_path is not None:
            _atomic_write_ui_owner_record(self.owner_path, payload)
        self.owner = payload

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self.owner_path is not None:
                try:
                    _remove_owned_ui_owner_record(self.owner_path, self.owner)
                except (OSError, SkillMagnetError):
                    pass
        finally:
            try:
                if self.handle is not None:
                    try:
                        _unlock_library_ui_file(self.handle)
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


def start_library_background_operation(
    operation: Callable[[threading.Event], Any],
    *,
    name: str,
    cancel_event: threading.Event | None = None,
) -> tuple[threading.Event, threading.Thread, dict[str, Any]]:
    """Start a cancellable operation immediately without blocking Tk's loop."""
    event = cancel_event or threading.Event()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["value"] = operation(event)
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=work, name=name, daemon=True)
    worker.start()
    return event, worker, outcome


def _try_lock_library_ui_file(handle: Any) -> bool:
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


def _unlock_library_ui_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_library_ui_lease(
    state_dir: Path, selected_source: Path | None = None
) -> LibraryUiLease:
    """Allow one Library Manager process and recover a lock left by a crash."""
    if os.path.lexists(state_dir) and _is_link(state_dir):
        raise SkillMagnetError(f"Library Manager state directory is a link or junction: {state_dir}")
    state_dir = validate_product_state_directory(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "library-manager.lock"
    owner_path = state_dir / "library-manager.owner.json"
    if os.path.lexists(path) and _is_link(path):
        raise SkillMagnetError(f"Library Manager lock is a link or junction: {path}")
    # Do not inspect a potentially slow or unavailable Explorer selection before
    # the Manager window is visible.  Registration validates and resolves it in
    # the cancellable worker.
    selected = _lexical_absolute(selected_source) if selected_source is not None else state_dir
    payload = _new_ui_owner_record(
        owner_kind="library_manager",
        target_digest=_path_identity_sha256(selected),
        phase="library_manager_starting",
    )
    path.touch(exist_ok=True)
    handle = path.open("r+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    if _try_lock_library_ui_file(handle):
        try:
            handle.seek(1)
            handle.truncate()
            handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            _atomic_write_ui_owner_record(owner_path, payload)
        except Exception:
            _unlock_library_ui_file(handle)
            handle.close()
            raise
        return LibraryUiLease(path, True, payload, handle, owner_path)
    owner: dict[str, Any] = {}
    for _ in range(10):
        try:
            with path.open("rb") as reader:
                reader.seek(1)
                owner = _owner_json_loads(reader.read(UI_OWNER_MAX_BYTES + 1))
        except (OSError, SkillMagnetError):
            owner = {}
        if _try_lock_library_ui_file(handle):
            try:
                handle.seek(1)
                handle.truncate()
                handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                _atomic_write_ui_owner_record(owner_path, payload)
            except Exception:
                _unlock_library_ui_file(handle)
                handle.close()
                raise
            return LibraryUiLease(path, True, payload, handle, owner_path)
        time.sleep(0.02)
    handle.close()
    legacy_selected = owner.get("selected_source")
    owner["same_request"] = bool(
        payload["target_sha256"] == owner.get("target_sha256")
        or (
            isinstance(legacy_selected, str)
            and os.path.normcase(os.path.normpath(str(selected)))
            == os.path.normcase(os.path.normpath(legacy_selected))
        )
    )
    return LibraryUiLease(path, False, owner, owner_path=owner_path)


def focus_library_ui(owner: dict[str, Any]) -> bool:
    """Bring the existing Library Manager process to the foreground."""

    # The context launcher and Library Manager use the same PID-based Windows
    # foregrounding contract.  Import lazily so this module stays usable in
    # library-only environments where the context UI is never opened.
    from .ui import focus_context_ui

    return focus_context_ui(owner)


def library_wizard_steps() -> tuple[str, ...]:
    return LIBRARY_WIZARD_STEPS


def library_action_label(stage: str) -> str:
    """Return the only action exposed for the current transaction stage."""
    try:
        return LIBRARY_ACTION_LABELS[stage]
    except KeyError as exc:
        raise SkillMagnetError(f"Unknown library action stage: {stage}") from exc


def automatic_sync_next_stage(result: dict[str, Any]) -> str:
    """Map a durable transaction result without polling a closed PR forever."""

    status = str(result.get("status", ""))
    wait_state = str(result.get("wait_state", ""))
    if status == "published_pending" and wait_state == "closed_unmerged":
        return "reopen_pr"
    if status == "published_pending":
        return "waiting"
    if status == "active":
        return "complete"
    return "sync"


def managed_repository_path(state_dir: Path) -> Path:
    """Return the app-owned library workspace; users do not manage this path."""
    return _require_managed_repository_boundary(state_dir)


def _managed_workspace_marker_path(state_dir: Path) -> Path:
    return validate_product_state_directory(state_dir) / "library" / MANAGED_WORKSPACE_MARKER


def _atomic_marker_json(path: Path, value: dict[str, Any]) -> None:
    """Durably replace one ownership marker without exposing partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_ownership_marker(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or _is_link(path):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def mark_managed_repository_owned(
    state_dir: Path,
    repository: Path,
    *,
    remote: str = "",
    commit: str = "",
) -> Path:
    """Record that the exact app-state workspace is disposable product data."""

    state_dir = validate_product_state_directory(state_dir)
    expected = _require_managed_repository_boundary(state_dir, repository)
    if not expected.is_dir() or _is_link(expected):
        raise SkillMagnetError(
            f"所有を記録する一時ライブラリが通常のフォルダーではありません: {expected}"
        )
    marker = _managed_workspace_marker_path(state_dir)
    inner_marker = expected / MANAGED_WORKSPACE_INNER_MARKER
    nonce = uuid.uuid4().hex
    payload = {
        "schema_version": 2,
        "owner": "skill_magnet_product",
        "repository": str(expected),
        "remote": remote.strip(),
        "commit": commit.strip().lower(),
        "ownership_nonce": nonce,
    }
    # Publish the external half first.  A crash before the inner half is written
    # leaves an unowned workspace which cleanup preserves fail-closed.
    _atomic_marker_json(marker, payload)
    _atomic_marker_json(inner_marker, payload)
    return marker


def managed_repository_is_owned(state_dir: Path, repository: Path) -> bool:
    """Return true only for the exact workspace explicitly created by this product."""

    try:
        expected = _require_managed_repository_boundary(state_dir, repository)
    except (OSError, SkillMagnetError):
        return False
    if not expected.is_dir() or _is_link(expected):
        return False
    outer = _read_ownership_marker(_managed_workspace_marker_path(state_dir))
    inner = _read_ownership_marker(expected / MANAGED_WORKSPACE_INNER_MARKER)
    if outer is None or inner is None:
        return False
    nonce = outer.get("ownership_nonce")
    return bool(
        outer == inner
        and outer.get("schema_version") == 2
        and outer.get("owner") == "skill_magnet_product"
        and isinstance(nonce, str)
        and bool(re.fullmatch(r"[0-9a-f]{32}", nonce))
        and os.path.normcase(os.path.normpath(str(outer.get("repository", ""))))
        == os.path.normcase(os.path.normpath(str(expected)))
    )


def _remove_product_tree(path: Path) -> None:
    """Remove a product-owned clone whose Git objects may be read-only on Windows."""

    if _is_link(path):
        raise SkillMagnetError(
            f"リンクまたはjunctionは製品データとして削除しません: {path}"
        )

    def make_writable(function: Any, name: str, _: BaseException) -> None:
        os.chmod(name, 0o700)
        function(name)

    shutil.rmtree(path, onexc=make_writable)


def _local_mutation_requires_preservation(repository: Path) -> bool:
    """Inspect the CRUD checkpoint without invoking lock-taking recovery code."""

    marker = repository / LOCAL_MUTATION_FILENAME
    if not os.path.lexists(marker):
        return False
    if _is_link(marker) or not marker.is_file():
        return True
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return True
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return True
    if value.get("pending") is True:
        return True
    if value.get("pending") is not False:
        return True
    result_manifest = value.get("result_manifest")
    if not isinstance(result_manifest, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in result_manifest.items()
    ):
        return True
    try:
        return _logical_library_manifest(repository) != result_manifest
    except Exception:
        return True


def _transaction_journal_state(
    state_dir: Path,
    repository: Path,
    *,
    remote: str | None = None,
) -> tuple[bool, bool]:
    """Return (unfinished_for_draft, corrupt_or_unclassifiable_journal)."""

    transaction_root = (
        validate_product_state_directory(state_dir) / "library-transactions"
    )
    if not os.path.lexists(transaction_root):
        return False, False
    if _is_link(transaction_root) or not transaction_root.is_dir():
        return False, True
    wanted_draft = os.path.normcase(os.path.normpath(str(_lexical_absolute(repository))))
    wanted_remote = remote.strip().removesuffix("/") if remote is not None else None
    try:
        entries = sorted(transaction_root.iterdir(), key=lambda item: item.name)
    except OSError:
        return False, True
    for transaction_dir in entries:
        if transaction_dir.is_file() and re.fullmatch(
            r"\.[A-Za-z0-9_-]{8,64}\.operation\.lock", transaction_dir.name
        ):
            if _is_link(transaction_dir):
                return False, True
            # Transaction lock files intentionally remain after completion.
            # A currently-held one, including the pre-journal window of a new
            # operation, must block deletion of the only managed draft.
            try:
                handle = transaction_dir.open("r+b")
            except OSError:
                return False, True
            try:
                if not _try_lock_library_ui_file(handle):
                    return True, False
                _unlock_library_ui_file(handle)
            except OSError:
                return False, True
            finally:
                handle.close()
            continue
        if _is_link(transaction_dir) or not transaction_dir.is_dir():
            return False, True
        journal_path = transaction_dir / "journal.json"
        if _is_link(journal_path) or not journal_path.is_file():
            return False, True
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return False, True
        if not isinstance(journal, dict) or journal.get("schema_version") != 1:
            return False, True
        status = journal.get("status")
        if not isinstance(status, str) or not status:
            return False, True
        if status in TERMINAL_STATES:
            continue
        saved_draft = journal.get("draft")
        if not isinstance(saved_draft, str) or not saved_draft.strip():
            return False, True
        try:
            normalized_draft = os.path.normcase(
                os.path.normpath(str(_lexical_absolute(Path(saved_draft))))
            )
        except (OSError, TypeError, ValueError):
            return False, True
        if normalized_draft != wanted_draft:
            continue
        saved_remote = journal.get("remote")
        if wanted_remote is not None and (
            not isinstance(saved_remote, str)
            or saved_remote.strip().removesuffix("/") != wanted_remote
        ):
            continue
        return True, False
    return False, False


def _purge_managed_repository_locked(
    state_dir: Path, repository: Path
) -> dict[str, Any]:
    """Revalidate every deletion precondition while holding the CRUD lock."""

    marker = _managed_workspace_marker_path(state_dir)
    if not os.path.lexists(repository):
        # Only the external half exists, so no content can be authorized by it.
        # Removing this stale product-state record cannot remove user content.
        if marker.is_file() and not _is_link(marker):
            marker.unlink(missing_ok=True)
        return {"purged": True, "repository": str(repository)}
    if not repository.is_dir() or not managed_repository_is_owned(state_dir, repository):
        return {
            "purged": False,
            "reason": "unowned_workspace_preserved",
            "repository": str(repository),
        }
    unfinished, corrupt = _transaction_journal_state(state_dir, repository)
    if _local_mutation_requires_preservation(repository) or unfinished or corrupt:
        return {
            "purged": False,
            "reason": (
                "unreadable_transaction_state_preserved"
                if corrupt
                else "unfinished_transaction_preserved"
            ),
            "repository": str(repository),
            "recovery": (
                "Library Managerを再度開き、保存済み作業を再開するか、"
                "GitHubから復旧してください"
            ),
        }
    # Ownership can be invalidated by an interrupted marker update.  Check the
    # complete pair again immediately before destructive removal.
    if not managed_repository_is_owned(state_dir, repository):
        return {
            "purged": False,
            "reason": "ownership_changed_workspace_preserved",
            "repository": str(repository),
        }
    _remove_product_tree(repository)
    if marker.is_file() and not _is_link(marker):
        marker.unlink(missing_ok=True)
    return {"purged": True, "repository": str(repository)}


def purge_managed_repository(state_dir: Path, repository: Path) -> dict[str, Any]:
    """Remove only a nonce-proven product workspace under the CRUD exclusion lock."""

    state_dir = validate_product_state_directory(state_dir)
    repository = _require_managed_repository_boundary(state_dir, repository)
    with library_mutation_lock(repository):
        repository = _require_managed_repository_boundary(state_dir, repository)
        return _purge_managed_repository_locked(state_dir, repository)


def managed_repository_has_unfinished_transaction(
    state_dir: Path,
    repository: Path,
    *,
    remote: str | None = None,
) -> bool:
    """Check durable journals for this exact draft (and optionally this remote)."""

    # CRUD is checkpointed before a publish transaction exists. Treat that
    # narrow crash window as unfinished too. Malformed local/transaction state
    # is never skipped: cleanup and hydration must preserve it for recovery.
    if _local_mutation_requires_preservation(_lexical_absolute(repository)):
        return True
    unfinished, corrupt = _transaction_journal_state(
        state_dir, _lexical_absolute(repository), remote=remote
    )
    return unfinished or corrupt


def _require_readable_transaction_journals(
    state_dir: Path, repository: Path
) -> None:
    """Stop new edits/transactions when recovery state cannot be classified."""

    _, corrupt = _transaction_journal_state(state_dir, repository)
    if corrupt:
        raise SkillMagnetError(
            "保存済みtransactionの記録が壊れているか読み取れません。"
            "新しい作業を重ねると復旧不能になるため開始しません。"
            "Library Managerを閉じずに記録の権限・内容を確認するか、"
            "画面の『GitHubから復旧』で元フォルダーをバックアップしてから再実行してください。"
        )


def configured_repository_url(config_path: Path) -> str:
    """Return the existing repository URL when the active config has one clear choice."""
    if not config_path.is_file():
        return ""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return ""
    if not isinstance(config, dict):
        return ""
    packs = config.get("packs", [])
    if not isinstance(packs, list) or not all(isinstance(pack, dict) for pack in packs):
        return ""
    urls = {
        str(pack.get("repo_url", "")).strip()
        for pack in packs
        if str(pack.get("repo_url", "")).strip()
    }
    return next(iter(urls)) if len(urls) == 1 else ""


def configured_repository_reference(config_path: Path) -> tuple[str, str]:
    """Return one configured repository and its one pinned commit when unambiguous."""

    remote = configured_repository_url(config_path)
    if not remote or not config_path.is_file():
        return remote, ""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return remote, ""
    commits = {
        str(pack.get("expected_commit", "")).strip().lower()
        for pack in config.get("packs", [])
        if isinstance(pack, dict)
        and str(pack.get("repo_url", "")).strip() == remote
        and re.fullmatch(r"[0-9a-fA-F]{40}", str(pack.get("expected_commit", "")).strip())
    }
    return remote, next(iter(commits)) if len(commits) == 1 else ""


def configuration_repair_notice(config_path: Path) -> str | None:
    """Explain a corrupt active config without preventing Manager startup."""

    if not config_path.is_file():
        return "設定ファイルがありません。GitHub URLを確認して再作成してください。"
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return f"設定ファイルを読み取れません。Library Managerで修復してください: {exc}"
    if not isinstance(value, dict):
        return "設定ファイルの形式が不正です。Library Managerで修復してください。"
    try:
        Config.load(config_path)
    except SkillMagnetError as exc:
        return f"設定ファイルの内容が不正です。Library Managerで修復してください: {exc}"
    return None


def library_failure_message(error: Exception) -> str:
    """Return the observed cause together with a concrete user recovery step."""

    cause = str(error).strip() or error.__class__.__name__
    folded = cause.casefold()
    if "skill.md" in folded or "スキルフォルダー" in cause:
        next_action = (
            "SKILL.mdを直接選ぶのではなく、そのSKILL.mdを含むフォルダーを選び直して"
            "同じ操作を再実行してください。"
        )
    elif any(
        token in folded
        for token in ("github", "git ", "remote", "pull request", "pr ")
    ):
        next_action = (
            "GitHub URL、ネットワーク接続、GitHubへのログイン状態を確認し、"
            "同じ画面の操作を再実行してください。途中状態は破棄していません。"
        )
    elif any(token in folded for token in ("config", "json", "設定")):
        next_action = (
            "公開先のGitHub URLを確認し、「GitHubへ反映」を再実行して"
            "設定を検証済み内容から作り直してください。"
        )
    elif any(
        token in folded
        for token in ("permission", "access is denied", "winerror 5", "read-only")
    ) or "アクセスが拒否" in cause:
        next_action = (
            "表示されたパスを開いているアプリを閉じ、書き込み権限と空き容量を確認してから"
            "同じ操作を再実行してください。"
        )
    else:
        next_action = (
            "入力内容を直して同じ操作を再実行してください。途中のGitHub送信があり得る場合は、"
            "画面に表示される「復旧して再試行」を選んでください。"
        )
    return (
        "処理を完了できませんでした。\n\n"
        f"原因\n{cause}\n\n"
        f"次の操作\n{next_action}\n\n"
        "この操作は完了扱いにしていません。"
    )


def prepare_managed_repository(repository: Path) -> str | None:
    """Initialize only a fresh draft; return a recoverable catalog error otherwise."""

    catalog_path = repository / CATALOG_FILENAME
    if catalog_path.is_file():
        try:
            library_inventory(repository)
        except Exception as exc:
            return str(exc)
        return None

    if not repository.exists():
        try:
            initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        except Exception as exc:
            return str(exc)
        return None

    if not repository.is_dir():
        return f"管理対象のローカルライブラリがフォルダーではありません: {repository}"

    try:
        empty = not any(repository.iterdir())
    except OSError as exc:
        return f"管理対象のローカルライブラリを読み取れません: {exc}"
    if empty:
        try:
            initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        except Exception as exc:
            return str(exc)
        return None

    if catalog_path.exists():
        return f"{CATALOG_FILENAME} が通常のファイルではありません"
    return f"管理済みローカルライブラリに {CATALOG_FILENAME} が見つかりません"


def remote_restore_available(
    repository: Path,
    *,
    config_repair: str | None,
    catalog_error: str | None,
) -> bool:
    """Offer GitHub restore whenever local state cannot rebuild a broken config."""

    if catalog_error is not None:
        return True
    if config_repair is None:
        return False
    try:
        return int(library_inventory(repository)["pack_count"]) == 0
    except (OSError, ValueError, TypeError, SkillMagnetError):
        return True


def _restore_managed_repository_from_github_locked(
    repository: Path,
    remote: str,
    *,
    commit: str = "",
    run: object | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, str | None]:
    """Replace an unreadable app-owned draft with a validated GitHub copy.

    The original directory is retained as a sibling backup after success. A
    failed clone or validation restores it automatically.
    """

    repository = _lexical_absolute(repository)
    if (os.path.lexists(repository) and _is_link(repository)) or (
        os.path.lexists(repository.parent) and _is_link(repository.parent)
    ):
        raise SkillMagnetError(
            "リンクまたはjunctionをGitHub復旧先にはできません。"
            f"外部フォルダーは変更しません: {repository}"
        )
    if not remote.strip():
        raise SkillMagnetError("復旧元のGitHub URLを入力してください")
    parent = repository.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{repository.name}.restore-{token}"
    backup = parent / f"{repository.name}.recovery-backup-{token}"
    previous_exists = repository.exists()
    if staging.exists() or backup.exists():
        raise SkillMagnetError("復旧用の一時領域が既に存在します。もう一度実行してください")
    try:
        clone_command = [
            "git", "clone", "--depth", "1", "--", remote.strip(), str(staging)
        ]
        completed = (
            _run_external(
                clone_command,
                check=False,
                cancel_event=cancel_event,
            )
            if run is None
            else run(
                clone_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown git error").strip()
            raise SkillMagnetError(f"GitHubから復旧用コピーを取得できません: {detail}")
        if commit:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                raise SkillMagnetError("復旧対象のcommit SHAが不正です")
            checkout_command = [
                "git", "-C", str(staging), "checkout", "--detach", commit.lower()
            ]
            checked_out = (
                _run_external(
                    checkout_command,
                    check=False,
                    cancel_event=cancel_event,
                )
                if run is None
                else run(
                    checkout_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            )
            if checked_out.returncode != 0:
                detail = (
                    checked_out.stderr
                    or checked_out.stdout
                    or "unknown git checkout error"
                ).strip()
                raise SkillMagnetError(
                    f"設定済みcommitをGitHubから復旧できません: {detail}"
                )
        # These files are local lifecycle evidence, never repository content.
        # A remote copy must not be allowed to import a stale pending mutation
        # or an ownership nonce into a newly-created workspace.
        for local_state_name in (
            LOCAL_MUTATION_FILENAME,
            MANAGED_WORKSPACE_INNER_MARKER,
        ):
            local_state_path = staging / local_state_name
            if _is_link(local_state_path):
                raise SkillMagnetError(
                    f"GitHubコピーに不正なローカル状態リンクがあります: {local_state_name}"
                )
            if local_state_path.exists():
                if not local_state_path.is_file():
                    raise SkillMagnetError(
                        f"GitHubコピーのローカル状態パスが通常のファイルではありません: "
                        f"{local_state_name}"
                    )
                local_state_path.unlink()
        validate_library(staging)
        if previous_exists:
            os.replace(repository, backup)
        os.replace(staging, repository)
    except Exception:
        if staging.exists():
            _remove_product_tree(staging)
        if previous_exists and backup.exists() and not repository.exists():
            os.replace(backup, repository)
        raise
    return {
        "repository": str(repository),
        "backup": str(backup) if previous_exists else None,
    }


def restore_managed_repository_from_github(
    repository: Path,
    remote: str,
    *,
    commit: str = "",
    run: object | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, str | None]:
    """Restore under the same exclusion lock used by CRUD and cleanup."""

    repository = _lexical_absolute(repository)
    if (os.path.lexists(repository) and _is_link(repository)) or (
        os.path.lexists(repository.parent) and _is_link(repository.parent)
    ):
        raise SkillMagnetError(
            "リンクまたはjunctionをGitHub復旧先にはできません。"
            f"外部フォルダーは変更しません: {repository}"
        )
    with library_mutation_lock(repository):
        return _restore_managed_repository_from_github_locked(
            repository,
            remote,
            commit=commit,
            run=run,
            cancel_event=cancel_event,
        )


def hydrate_managed_repository(
    state_dir: Path,
    repository: Path,
    remote: str,
    *,
    commit: str = "",
    run: object | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Hydrate a disposable CRUD workspace from the configured GitHub source."""

    state_dir = validate_product_state_directory(state_dir)
    repository = _require_managed_repository_boundary(state_dir, repository)
    with library_mutation_lock(repository):
        repository = _require_managed_repository_boundary(state_dir, repository)
        if managed_repository_has_unfinished_transaction(
            state_dir, repository, remote=remote
        ):
            return {
                "hydrated": False,
                "reason": "unfinished_transaction_preserved",
                "repository": str(repository),
            }
        if os.path.lexists(repository):
            purged = _purge_managed_repository_locked(state_dir, repository)
            if not purged["purged"]:
                raise SkillMagnetError(
                    "既存のローカルフォルダーはSkill Magnetの所有を確認できないため、"
                    "自動削除・上書きしません。画面の『GitHubから復旧』を選ぶと、"
                    "元フォルダーをバックアップとして残して続行できます。"
                )
        restored = _restore_managed_repository_from_github_locked(
            repository,
            remote,
            commit=commit,
            run=run,
            cancel_event=cancel_event,
        )
        mark_managed_repository_owned(
            state_dir, repository, remote=remote, commit=commit
        )
    return {
        "hydrated": True,
        "repository": str(repository),
        "remote": remote,
        "commit": commit.lower(),
        "backup": restored.get("backup"),
    }


def _logical_library_manifest(repository: Path) -> dict[str, str]:
    """Hash managed library bytes with Git's cross-platform text normalization."""

    validation = validate_library(repository)
    logical: dict[str, str] = {}
    for relative in validation.manifest:
        data = repository.joinpath(*relative.split("/")).read_bytes()
        if relative.endswith((".md", ".json", ".yaml", ".yml", ".toml", ".py", ".txt")):
            data = data.replace(b"\r\n", b"\n")
        logical[relative] = hashlib.sha256(data).hexdigest()
    return logical


def migrate_legacy_managed_repository(
    state_dir: Path,
    repository: Path,
    remote: str,
    *,
    commit: str = "",
    run: object | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Preserve markerless legacy/user data until the user explicitly restores it."""

    del remote, commit, run, cancel_event
    state_dir = validate_product_state_directory(state_dir)
    repository = _require_managed_repository_boundary(state_dir, repository)
    if not os.path.lexists(repository) or not repository.is_dir():
        return {"migrated": False, "reason": "not_legacy", "repository": str(repository)}
    if managed_repository_is_owned(state_dir, repository):
        return {"migrated": False, "reason": "already_owned", "repository": str(repository)}
    raise SkillMagnetError(
        "既存のローカルフォルダーは双方向の所有証明がなく、Skill Magnetの旧作業領域か"
        "ユーザーが配置したGit cloneかを区別できません。自動所有化・自動削除は行いません。"
        "画面の『GitHubから復旧』を選ぶと、元フォルダーを別名バックアップとして残して"
        "新しい検証済みコピーを作成できます。"
    )


def require_registration_source(
    value: str, *, cancel_event: threading.Event | None = None
) -> Path:
    """Require a folder containing a skill, a pack, or multiple packs."""
    if not value.strip():
        raise SkillMagnetError("登録するスキルまたはスキルパックのフォルダーを選択してください")
    lexical_source = _lexical_absolute(Path(value))
    if os.path.lexists(lexical_source) and _is_link(lexical_source):
        raise SkillMagnetError(
            "登録元のフォルダーにリンクまたはjunctionは指定できません。"
            f"実体のフォルダーを選択してください: {lexical_source}"
        )
    source = lexical_source.resolve()
    discover_skill_sources(source, cancel_event=cancel_event)
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
    """Return true only when the complete generated registration is byte-equivalent.

    ID and membership equality is insufficient: a changed ``SKILL.md`` with the
    same IDs is an update.  Run the same atomic upsert against an isolated copy
    and compare managed manifests so this predicate can never mutate the live
    workspace.
    """

    repository = repository.resolve()
    source = source.resolve()
    catalog_path = repository / CATALOG_FILENAME
    if not catalog_path.is_file():
        return False
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog.get("packs"), list) or not catalog["packs"]:
        return False
    try:
        validate_library(repository)
    except SkillMagnetError as exc:
        raise SkillMagnetError(
            "同じパックIDの登録情報と保存ファイルが一致しません。"
            f"GitHubから復旧してから再実行してください: {exc}"
        ) from exc
    scratch = Path(
        tempfile.mkdtemp(prefix=f".{repository.name}-registration-check-", dir=repository.parent)
    )
    candidate = scratch / repository.name
    try:
        shutil.copytree(
            repository,
            candidate,
            ignore=shutil.ignore_patterns(".git"),
        )
        try:
            before = _logical_library_manifest(candidate)
        except SkillMagnetError as exc:
            raise SkillMagnetError(
                "登録情報と保存ファイルが一致しません。GitHubから復旧してから"
                f"登録をやり直してください: {exc}"
            ) from exc
        upsert_skill_source(candidate, source)
        after = _logical_library_manifest(candidate)
        return before == after
    finally:
        if scratch.exists():
            _remove_product_tree(scratch)


def register_skill_source(
    repository: Path,
    source: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Atomically add/update a complete source or return an exact no-op."""
    # The upsert owns the single CRUD lock and is the authority for whether the
    # operation changed anything.  A separate scratch check here created a
    # check/use race with CLI CRUD and could report an obsolete no-op.
    result = upsert_skill_source(
        repository, source, cancel_event=cancel_event
    )
    result["already_registered"] = bool(result.get("no_changes"))
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
    register_selected: bool = False,
    menu_update: Callable[[Path, str], Any] | None = None,
    window_ready: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Open the compact library manager and return its final status."""
    state_dir = validate_product_state_directory(state_dir)
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise SkillMagnetError("Tk is required for the Library Manager UI") from exc

    lease = acquire_library_ui_lease(state_dir, initial_repository if register_selected else None)
    if not lease.acquired:
        if lease.same_request and focus_library_ui(lease.owner):
            return {"status": "already_running", "same_request": True}
        notice = tk.Tk()
        notice.withdraw()
        detail = (
            "同じフォルダーの登録を処理中です。\n"
            "重複する処理は開始しません。開いているLibrary Managerで進行状況を確認してください。"
            if lease.same_request
            else "Library Managerで別の処理を実行中です。\n"
            "並行処理は開始しません。開いている画面の完了後にもう一度実行してください。"
        )
        messagebox.showinfo("Library Managerは処理中です", detail, parent=notice)
        notice.destroy()
        return {"status": "already_running", "same_request": lease.same_request}

    root = tk.Tk()
    root.title("Library Manager")
    root.geometry("920x680")
    root.minsize(760, 560)
    surface_identities: list[UiSurfaceOwnerIdentity] = []
    try:
        root.update_idletasks()
        manager_window_handle = tk_top_level_window_handle(root)
        lease.publish_window(manager_window_handle)
        surface_identities.append(
            ui_surface_owner_identity(
                state_dir / "library-manager.owner.json",
                phase="library_manager",
                window_handle=manager_window_handle,
            )
        )
        if window_ready is not None:
            window_ready(manager_window_handle)
            context_owner = state_dir / "context-launcher.owner.json"
            if context_owner.exists():
                surface_identities.append(
                    ui_surface_owner_identity(
                        context_owner,
                        phase="library_manager",
                        window_handle=manager_window_handle,
                    )
                )
    except Exception:
        root.destroy()
        lease.release()
        raise
    page = ttk.Frame(root, padding=12)
    page.pack(fill="both", expand=True)
    page.columnconfigure(0, weight=1)
    page.rowconfigure(3, weight=1)
    # Configuration, recovery and repository validation can all touch a slow
    # disk (or a stale network-backed path).  Populate them after mainloop has
    # rendered the window; doing this work here recreated the reported blank
    # Library Manager window during startup.
    repair_notice: str | None = None
    processing_status = tk.StringVar(value="起動状態を確認しています…")
    status_label = ttk.Label(
        page, textvariable=processing_status, anchor="w", padding=(8, 6)
    )
    status_label.grid(
        row=0, column=0, sticky="ew", pady=(0, 8)
    )
    controls: list[Any] = []

    repository_path = managed_repository_path(state_dir)
    recovery: dict[str, Any] = {"recovered": False}
    catalog_error: str | None = None
    offer_remote_restore = False
    repository = tk.StringVar(value=str(repository_path))
    configured_remote = ""
    configured_commit = ""
    remote = tk.StringVar(value="")
    import_source = tk.StringVar(
        value=str(_lexical_absolute(initial_repository)) if initial_repository is not None else ""
    )
    transaction_id = tk.StringVar()
    action_stage = tk.StringVar(value="sync")
    platform = "windows" if os.name == "nt" else "macos"
    result: dict[str, Any] = {"status": "closed_without_activation"}
    busy = False
    legacy_migration_pending = False
    recovery_notice_shown = False
    closing = False
    active_worker: threading.Thread | None = None
    active_cancel_event: threading.Event | None = None
    active_transaction: LibraryTransaction | None = None
    manager_surface_ready = False

    def set_busy(value: bool, label: str = "") -> None:
        nonlocal busy
        busy = value
        # Keep the stable window identity while the status row carries the
        # processing state.  Focus/recovery and UI evidence bind this exact
        # title to the live top-level HWND.
        root.title("Library Manager")
        if value:
            processing_status.set(f"処理中：{label}")
            root.configure(cursor="wait")
        else:
            root.configure(cursor="")
            if action_stage.get() == "complete":
                processing_status.set("完了")
            elif action_stage.get() == "waiting":
                processing_status.set("処理中：GitHubのマージ完了を待っています…")
            else:
                processing_status.set("待機中")
        for control in controls:
            try:
                control.configure(state="disabled" if value else "normal")
            except Exception:
                pass
        if not value:
            action_button.configure(
                state=(
                    "disabled"
                    if action_stage.get() in {"complete", "waiting"}
                    else "normal"
                )
            )
        root.update_idletasks()
        publish_manager_surface()

    def run_auxiliary_in_background(
        label: str,
        operation: Callable[[threading.Event], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None] | None = None,
        *,
        allow_while_busy: bool = False,
    ) -> None:
        """Keep clone/restore operations off Tk's main event thread."""
        nonlocal active_worker, active_cancel_event, active_transaction
        if busy and not allow_while_busy:
            return
        cancel_event = threading.Event()
        active_cancel_event = cancel_event
        active_transaction = None
        set_busy(True, label)
        _, worker, outcome = start_library_background_operation(
            operation,
            name="skill-magnet-library-auxiliary",
            cancel_event=cancel_event,
        )
        active_worker = worker

        def poll() -> None:
            nonlocal active_worker, active_cancel_event
            if worker.is_alive():
                root.after(50, poll)
                return
            active_worker = None
            active_cancel_event = None
            if closing:
                close_manager(force=True)
                return
            set_busy(False)
            error = outcome.get("error")
            if isinstance(error, BaseException):
                converted = error if isinstance(error, Exception) else SkillMagnetError(str(error))
                (on_error or show_error)(converted)
                return
            on_success(outcome.get("value"))

        root.after(50, poll)

    def row(
        page: Any,
        number: int,
        label: str,
        variable: Any,
        browse: Callable[[], None] | None = None,
    ) -> tuple[Any, Any | None]:
        ttk.Label(page, text=label).grid(row=number, column=0, sticky="w", padx=4, pady=5)
        entry = ttk.Entry(page, textvariable=variable, width=74)
        entry.grid(
            row=number, column=1, sticky="ew", padx=4, pady=5
        )
        controls.append(entry)
        if browse is not None:
            browse_button = ttk.Button(page, text="Browse", command=browse)
            browse_button.grid(row=number, column=2, padx=4)
            controls.append(browse_button)
        else:
            browse_button = None
        page.columnconfigure(1, weight=1)
        return entry, browse_button

    def select_import() -> None:
        value = filedialog.askdirectory(title="Select skill directory")
        if value:
            # Walking a large pack here freezes Tk immediately after the
            # native folder dialog closes.  The Register worker performs the
            # complete link/SKILL.md validation before any managed write.
            import_source.set(str(_lexical_absolute(Path(value))))

    def show_error(exc: Exception) -> None:
        # Import lazily: ui.py also routes into this module for Library Manager.
        from .ui import context_failure_message

        messagebox.showerror(
            "Library Manager",
            context_failure_message(
                exc,
                config_path=config_path,
                state_dir=state_dir,
                platform=platform,
            ),
            parent=root,
        )

    def ensure_managed_workspace_ready(
        *,
        selected_remote: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Rehydrate lazily after success/cancel/abandon removed skill content."""

        if os.path.lexists(repository_path):
            if not managed_repository_is_owned(state_dir, repository_path):
                raise SkillMagnetError(
                    "既存のローカルフォルダーはLibrary Managerの所有を確認できないため、"
                    "自動編集・削除しません。公開先のGitHub URLを確認し、"
                    "画面の『GitHubから復旧』を押してください。元フォルダーは"
                    "別名バックアップとして残ります。"
                )
            validate_library(repository_path)
            return
        remote_value = (
            remote.get().strip() if selected_remote is None else selected_remote.strip()
        )
        if remote_value:
            hydrate_managed_repository(
                state_dir,
                repository_path,
                remote_value,
                commit=(
                    configured_commit
                    if remote_value == configured_remote
                    else ""
                ),
                cancel_event=cancel_event,
            )
            return
        with library_mutation_lock(repository_path):
            if os.path.lexists(repository_path):
                if not managed_repository_is_owned(state_dir, repository_path):
                    raise SkillMagnetError(
                        "登録準備中に所有不明のローカルフォルダーが作成されました。"
                        "自動編集・削除せず保持します。"
                    )
                validate_library(repository_path)
                return
            initialize_library(repository_path, DEFAULT_REPOSITORY_NAME)
            mark_managed_repository_owned(state_dir, repository_path)

    def abandon_current() -> None:
        if not transaction_id.get().strip():
            return
        abandoned = transaction().abandon(confirmed=True)
        set_text(preview_output, abandoned)
        transaction_id.set("")
        set_stage("prepare")
        purge_managed_repository(state_dir, repository_path)
        inventory_tree.delete(*inventory_tree.get_children())
        inventory_summary.set(
            "ローカル作業を破棄しました。次の操作時にGitHubから再読込します"
        )

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
                set_stage("sync")
                root.after(0, run_current_action)
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
            set_stage("sync")
            root.after(0, run_current_action)
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

    initial_registration: dict[str, Any] | None = None

    inventory_frame = ttk.LabelFrame(page, text="登録済みのスキル", padding=10)
    inventory_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
    inventory_frame.columnconfigure(0, weight=1)
    inventory_frame.rowconfigure(0, weight=1)
    inventory_tree = ttk.Treeview(
        inventory_frame,
        columns=("kind", "identifier", "purpose"),
        show="tree headings",
        height=8,
        selectmode="browse",
    )
    inventory_tree.heading("#0", text="名前")
    inventory_tree.heading("kind", text="種類")
    inventory_tree.heading("identifier", text="内部ID")
    inventory_tree.heading("purpose", text="説明")
    inventory_tree.column("#0", width=220)
    inventory_tree.column("kind", width=75, anchor="center")
    inventory_tree.column("identifier", width=180)
    inventory_tree.column("purpose", width=330)
    inventory_tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
    inventory_scroll = ttk.Scrollbar(
        inventory_frame, orient="vertical", command=inventory_tree.yview
    )
    inventory_scroll.grid(row=0, column=4, sticky="ns")
    inventory_tree.configure(yscrollcommand=inventory_scroll.set)
    inventory_summary = tk.StringVar()
    ttk.Label(inventory_frame, textvariable=inventory_summary).grid(
        row=1, column=0, columnspan=4, sticky="w", pady=(6, 0)
    )

    def selected_inventory_item() -> tuple[str, str]:
        selected = inventory_tree.selection()
        if not selected:
            raise SkillMagnetError("更新または削除するパック／スキルを一覧から選択してください")
        parts = selected[0].split(":", 2)
        return parts[0], parts[-1]

    def refresh_inventory() -> None:
        inventory_tree.delete(*inventory_tree.get_children())
        inventory = library_inventory(repository_path)
        for pack in inventory["packs"]:
            pack_node = f"pack:{pack['id']}"
            inventory_tree.insert(
                "",
                "end",
                iid=pack_node,
                text=pack["display_name"],
                values=("パック", pack["id"], pack["purpose"]),
                open=True,
            )
            for skill in pack["skills"]:
                inventory_tree.insert(
                    pack_node,
                    "end",
                    iid=f"skill:{pack['id']}:{skill['id']}",
                    text=skill["display_name"],
                    values=("スキル", skill["id"], skill["purpose"]),
                )
        inventory_summary.set(
            f"{inventory['pack_count']}パック／{inventory['skill_count']}スキルを登録済み"
        )
        publish_manager_surface()

    def ensure_editable_library(
        transaction_value: str,
        remote_value: str,
        cancel_event: threading.Event,
    ) -> bool:
        """Prepare transaction state for CRUD without touching Tk from the worker."""

        _require_readable_transaction_journals(state_dir, repository_path)
        if cancel_event.is_set():
            raise SkillMagnetError(
                "終了操作を受け付けたため、保存済み作業の確認前に中止しました"
            )
        current: LibraryTransaction | None = None
        if transaction_value:
            current = LibraryTransaction(
                state_dir, transaction_value, cancel_event=cancel_event
            )
        elif remote_value:
            current = find_resumable_transaction(
                state_dir,
                draft=repository_path,
                remote=remote_value,
            )
        if current is None:
            return False
        journal = current._journal()
        status = str(journal.get("status", "draft"))
        if status in {"draft", "prepared", "no_changes", "abandoned", "active"}:
            if status in {"draft", "prepared"}:
                current.abandon(confirmed=True)
            return True
        raise SkillMagnetError(
            "GitHub送信中またはマージ待ちの作業があります。先にその作業を完了してください"
        )

    def apply_editable_transaction_reset(reset: bool) -> None:
        if reset:
            transaction_id.set("")
            set_stage("prepare")

    registration = ttk.LabelFrame(page, text="作成済みスキルを登録", padding=10)
    registration.grid(row=2, column=0, sticky="ew", pady=(0, 10))
    ttk.Label(registration, text="スキル、スキルパック、または複数パックを含むフォルダーを登録します。").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    registration_source_entry, registration_browse_button = row(
        registration,
        1,
        "スキル／スキルパックのフォルダー",
        import_source,
        select_import,
    )

    def add() -> None:
        if busy:
            return
        try:
            source_value = import_source.get()
            if not source_value.strip():
                raise SkillMagnetError(
                    "登録するスキルまたはスキルパックのフォルダーを選択してください"
                )
            repository_root = require_repository()
            remote_value = remote.get().strip()
            transaction_value = transaction_id.get().strip()
        except Exception as exc:
            show_error(exc)
            return

        def work(cancel_event: threading.Event) -> dict[str, Any]:
            source = require_registration_source(
                source_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、登録の書き込み前に中止しました"
                )
            reset_transaction = ensure_editable_library(
                transaction_value, remote_value, cancel_event
            )
            ensure_managed_workspace_ready(
                selected_remote=remote_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、登録の書き込み前に中止しました"
                )
            return {
                "result": register_skill_source(
                    repository_root, source, cancel_event=cancel_event
                ),
                "reset_transaction": reset_transaction,
            }

        def completed(value: Any) -> None:
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                show_error(SkillMagnetError("登録結果を読み取れません。もう一度実行してください"))
                return
            imported = value["result"]
            apply_editable_transaction_reset(bool(value.get("reset_transaction")))
            if imported["already_registered"]:
                messagebox.showinfo(
                    "スキルを登録",
                    "選択したスキルまたはスキルパックは登録済みです。",
                    parent=root,
                )
                registration.grid_remove()
                refresh_inventory()
                root.after(0, run_current_action)
                return
            messagebox.showinfo(
                "Skill",
                f"{len(imported['imported_pack_ids'])}パック、"
                f"{len(imported['imported_skill_ids'])}スキルを登録しました。",
                parent=root,
            )
            registration.grid_remove()
            refresh_inventory()
            root.after(0, run_current_action)

        run_auxiliary_in_background(
            "選択したフォルダーを検証・登録しています…", work, completed
        )

    register_button = ttk.Button(registration, text="登録", command=add)
    register_button.grid(
        row=2, column=1, sticky="e", pady=(8, 0)
    )
    controls.append(register_button)

    def create_selected() -> None:
        if busy:
            return
        try:
            value = filedialog.askdirectory(title="登録するスキルまたはパックを選択")
        except Exception as exc:
            show_error(exc)
            return
        if not value:
            return
        import_source.set(value)
        add()

    def update_selected() -> None:
        if busy:
            return
        try:
            kind, identifier = selected_inventory_item()
            value = filedialog.askdirectory(title=f"{identifier}の更新元フォルダーを選択")
            if not value:
                return
            source_value = value
            remote_value = remote.get().strip()
            transaction_value = transaction_id.get().strip()
        except Exception as exc:
            show_error(exc)
            return

        def work(cancel_event: threading.Event) -> dict[str, Any]:
            source = require_registration_source(
                source_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、更新の書き込み前に中止しました"
                )
            reset_transaction = ensure_editable_library(
                transaction_value, remote_value, cancel_event
            )
            ensure_managed_workspace_ready(
                selected_remote=remote_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、更新の書き込み前に中止しました"
                )
            if kind == "pack":
                updated = update_pack_source(
                    repository_path, identifier, source, cancel_event=cancel_event
                )
            else:
                updated = update_skill_source(
                    repository_path, identifier, source, cancel_event=cancel_event
                )
            return {
                "result": updated,
                "reset_transaction": reset_transaction,
            }

        def completed(value: Any) -> None:
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                show_error(SkillMagnetError("更新結果を読み取れません。もう一度実行してください"))
                return
            updated = value["result"]
            apply_editable_transaction_reset(bool(value.get("reset_transaction")))
            set_text(preview_output, updated)
            refresh_inventory()
            root.after(0, run_current_action)

        run_auxiliary_in_background(
            "選択した登録内容を更新しています…", work, completed
        )

    def delete_selected_item() -> None:
        if busy:
            return
        try:
            kind, identifier = selected_inventory_item()
            label = "パック" if kind == "pack" else "スキル"
            if not messagebox.askyesno(
                f"{label}を削除",
                f"{identifier}をライブラリから削除しますか？\nGitHubへは確認後にPRとして送ります。",
                parent=root,
            ):
                return
            remote_value = remote.get().strip()
            transaction_value = transaction_id.get().strip()
        except Exception as exc:
            show_error(exc)
            return

        def work(cancel_event: threading.Event) -> dict[str, Any]:
            reset_transaction = ensure_editable_library(
                transaction_value, remote_value, cancel_event
            )
            ensure_managed_workspace_ready(
                selected_remote=remote_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、削除の書き込み前に中止しました"
                )
            if kind == "pack":
                deleted = delete_pack(
                    repository_path,
                    identifier,
                    confirmed=True,
                    cancel_event=cancel_event,
                )
            else:
                deleted = delete_skill(
                    repository_path,
                    identifier,
                    confirmed=True,
                    cancel_event=cancel_event,
                )
            return {
                "result": deleted,
                "reset_transaction": reset_transaction,
            }

        def completed(value: Any) -> None:
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                show_error(SkillMagnetError("削除結果を読み取れません。もう一度実行してください"))
                return
            deleted = value["result"]
            apply_editable_transaction_reset(bool(value.get("reset_transaction")))
            set_text(preview_output, deleted)
            refresh_inventory()
            root.after(0, run_current_action)

        run_auxiliary_in_background(
            f"選択した{label}を削除しています…", work, completed
        )

    def reload_inventory() -> None:
        if busy:
            return
        set_busy(True, "登録済みスキルを再読込しています…")
        try:
            refresh_inventory()
        except Exception as exc:
            show_error(exc)
        finally:
            set_busy(False)

    inventory_buttons = ttk.Frame(inventory_frame)
    inventory_buttons.grid(row=2, column=0, columnspan=4, sticky="e", pady=(8, 0))
    inventory_action_buttons: dict[str, Any] = {}
    for identifier, label, command in (
        ("new_registration", "新規登録", create_selected),
        ("update", "選択項目を更新", update_selected),
        ("delete", "選択項目を削除", delete_selected_item),
        ("reload", "再読込", reload_inventory),
    ):
        button = ttk.Button(inventory_buttons, text=label, command=command)
        button.pack(side="left", padx=3)
        controls.append(button)
        inventory_action_buttons[identifier] = button

    recovery_button: Any | None = None

    def recover_managed_catalog() -> None:
        nonlocal catalog_error, recovery_button
        if not remote.get().strip():
            show_error(SkillMagnetError("復旧元のGitHub URLを入力してください"))
            return
        if not messagebox.askyesno(
            "ローカルライブラリを復旧",
            "設定またはローカルのスキル一覧を復旧する必要があります。\n"
            "現在のフォルダーをバックアップとして残し、入力したGitHubから復旧しますか？",
            parent=root,
        ):
            return
        remote_value = remote.get().strip()
        commit_value = configured_commit if remote_value == configured_remote else ""

        def restore(cancel_event: threading.Event) -> dict[str, str | None]:
            # Keep replacement and both ownership markers inside one CRUD
            # exclusion window.  Otherwise a CLI writer can mutate the new
            # clone before it is marked, after which cleanup could mistake
            # those user changes for disposable product data.
            with library_mutation_lock(repository_path):
                restored = _restore_managed_repository_from_github_locked(
                    repository_path,
                    remote_value,
                    commit=commit_value,
                    cancel_event=cancel_event,
                )
                mark_managed_repository_owned(
                    state_dir,
                    repository_path,
                    remote=remote_value,
                    commit=commit_value,
                )
                return restored

        def restored_success(restored: Any) -> None:
            nonlocal catalog_error, recovery_button
            catalog_error = None
            refresh_inventory()
            if recovery_button is not None:
                recovery_button.pack_forget()
            processing_status.set("復旧完了。元のフォルダーはバックアップとして保持しました。")
            messagebox.showinfo(
                "復旧完了",
                "GitHubの検証済み構成から復旧しました。\n"
                f"元のローカルデータ: {restored.get('backup') or 'なし'}",
                parent=root,
            )

        run_auxiliary_in_background(
            "GitHubからローカルライブラリを復旧しています…",
            restore,
            restored_success,
        )

    recovery_button = ttk.Button(
        inventory_buttons,
        text="GitHubから復旧",
        command=recover_managed_catalog,
    )
    if offer_remote_restore:
        recovery_button.pack(side="left", padx=3)
    controls.append(recovery_button)

    def set_text(widget: Any, value: Any) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2))
        widget.configure(state="disabled")

    publish_frame = ttk.LabelFrame(page, text="GitHubへ送る", padding=10)
    publish_frame.grid(row=3, column=0, sticky="nsew")
    ttk.Label(
        publish_frame,
        text=(
            "公開先のGitHub URLを入力し、送信予定のファイルを確認してからPRを作成します。"
            "URL未入力、ファイル構成不正、検査エラーがあれば送信せずエラーを表示します。"
        ),
        wraplength=820,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
    configured_remote_entry, _ = row(
        publish_frame, 1, "公開先のGitHub URL", remote
    )
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
            _require_readable_transaction_journals(state_dir, require_repository())
            resumable = find_resumable_transaction(
                state_dir,
                draft=require_repository(),
                remote=remote.get().strip(),
            )
            if resumable is None:
                unfinished, _ = _transaction_journal_state(
                    state_dir, require_repository()
                )
                if unfinished:
                    raise SkillMagnetError(
                        "別の未完了transactionがあります。保存済み作業を再開または破棄してから"
                        "新しいGitHub送信を開始してください。"
                    )
            current = resumable or LibraryTransaction(state_dir)
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
                    "GitHubへの変更はありません",
                    "GitHub上の内容は同じです。検証済み内容をSkill Magnetへ反映できます。",
                    parent=root,
                )
                set_stage("activate")
            else:
                set_stage("publish")
        except Exception as exc:
            if current is not None and current.journal_path.is_file():
                handle_transaction_error(exc, "prepare")
            else:
                transaction_id.set("")
                show_error(exc)

    def transaction(
        *, cancel_event: threading.Event | None = None
    ) -> LibraryTransaction:
        if not transaction_id.get().strip():
            raise SkillMagnetError("先に送信内容を確認してください")
        return LibraryTransaction(
            state_dir,
            transaction_id.get().strip(),
            cancel_event=cancel_event,
        )

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
                    "PRはマージされずに閉じられています。『閉じたPRを再度開く』から"
                    "同じPRを再利用するか、状態を保持したまま終了してください。",
                    parent=root,
                )
                set_stage("reopen_pr")
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
            purge_managed_repository(state_dir, repository_path)
            messagebox.showinfo("Library Manager", "有効化が完了しました。", parent=root)
        except Exception as exc:
            handle_transaction_error(exc, "activate")

    def automatic_sync() -> None:
        """Complete the user-requested library change without manual stage buttons."""
        nonlocal result
        current: LibraryTransaction | None = None
        try:
            if not remote.get().strip():
                raise SkillMagnetError("公開先のGitHub URLを入力してください")
            if transaction_id.get().strip():
                current = transaction()
            else:
                _require_readable_transaction_journals(state_dir, require_repository())
                resumable = find_resumable_transaction(
                    state_dir,
                    draft=require_repository(),
                    remote=remote.get().strip(),
                )
                if resumable is None:
                    unfinished, _ = _transaction_journal_state(
                        state_dir, require_repository()
                    )
                    if unfinished:
                        raise SkillMagnetError(
                            "別の未完了transactionがあります。保存済み作業を再開または破棄してから"
                            "新しいGitHub送信を開始してください。"
                        )
                current = resumable or LibraryTransaction(state_dir)
                transaction_id.set(current.transaction_id)

            def update(path: Path) -> Any:
                return menu_update(path, platform) if menu_update else None

            result = current.complete_automatically(
                draft=require_repository(),
                remote=remote.get().strip(),
                config_path=config_path,
                confirmed=True,
                menu_update=update if menu_update else None,
            )
            set_text(preview_output, result)
            if str(result.get("status")) == "published_pending":
                next_stage = automatic_sync_next_stage(result)
                set_stage(next_stage)
                if next_stage == "reopen_pr":
                    messagebox.showwarning(
                        "PRがマージされずに閉じられています",
                        "自動監視を停止しました。『閉じたPRを再度開く』を押すと、"
                        "同じPRとtransactionを使って処理を再開できます。",
                        parent=root,
                    )
                    return

                def poll_merge() -> None:
                    if not root.winfo_exists():
                        return
                    set_stage("sync")
                    run_current_action()

                root.after(15_000, poll_merge)
                return
            set_stage("complete")
            purge_result = purge_managed_repository(state_dir, repository_path)
            if not purge_result["purged"]:
                raise SkillMagnetError(
                    "GitHub反映は完了しましたが、所有を確認できないローカルコピーを"
                    "自動削除しませんでした。画面の案内から復旧してください"
                )
            messagebox.showinfo(
                "Library Manager",
                "GitHubへの送信・マージ・Skill Magnetへの反映が完了しました。",
                parent=root,
            )
        except Exception as exc:
            if current is not None and current.journal_path.is_file():
                handle_transaction_error(exc, "sync")
            else:
                transaction_id.set("")
                show_error(exc)

    def stage_for_status(status: str, fallback: str = "prepare") -> str:
        return {
            "prepared": "sync",
            "published_pending": "sync",
            "verified": "sync",
            "activating": "sync",
            "menu_pending": "sync",
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

    def reopen_pull_request() -> None:
        try:
            if not messagebox.askyesno(
                "閉じたPRを再度開く",
                "マージされずに閉じられた同じPRを再度開き、同じtransactionで再開しますか？",
                parent=root,
            ):
                return
            reopened = transaction().reopen_pull_request(confirmed=True)
            set_text(preview_output, reopened)
            set_stage("sync")
            root.after(0, run_current_action)
        except Exception as exc:
            handle_transaction_error(exc, "reopen_pr")

    def set_stage(value: str) -> None:
        action_stage.set(value)
        action_button.configure(
            text=library_action_label(value),
            state="disabled" if value in {"complete", "waiting"} or busy else "normal",
        )
        publish_manager_surface()

    def run_current_action() -> None:
        """Run every git/GitHub transition outside Tk's event thread."""
        nonlocal result, active_worker, active_cancel_event, active_transaction
        if busy:
            return
        stage = action_stage.get()
        if stage == "open_pr":
            open_pull_request()
            return
        if stage not in {"sync", "prepare", "publish", "verify", "reopen_pr", "activate"}:
            return
        if stage == "publish" and not messagebox.askyesno(
            "GitHubへ送る",
            "表示された公開先とファイルを確認しましたか？\n"
            "専用branchへcommit・pushしてPRを作成します。",
            parent=root,
        ):
            return
        if stage == "reopen_pr" and not messagebox.askyesno(
            "閉じたPRを再度開く",
            "マージされずに閉じられた同じPRを再度開き、同じtransactionで再開しますか？",
            parent=root,
        ):
            return
        if stage == "activate" and not messagebox.askyesno(
            "Skill Magnetへ反映",
            "検証済み版をSkill Magnetへ反映しますか？失敗時は直前版へ戻します。",
            parent=root,
        ):
            return

        remote_value = remote.get().strip()
        repository_value = require_repository()
        if stage in {"sync", "prepare"} and not remote_value:
            show_error(SkillMagnetError("公開先のGitHub URLを入力してください"))
            return
        cancel_event = threading.Event()
        if transaction_id.get().strip():
            current = transaction(cancel_event=cancel_event)
        elif stage in {"sync", "prepare"}:
            try:
                _require_readable_transaction_journals(state_dir, repository_value)
            except Exception as exc:
                show_error(exc)
                return
            resumable = find_resumable_transaction(
                state_dir,
                draft=repository_value,
                remote=remote_value,
            )
            if resumable is None:
                unfinished, _ = _transaction_journal_state(
                    state_dir, repository_value
                )
                if unfinished:
                    show_error(
                        SkillMagnetError(
                            "別の未完了transactionがあります。保存済み作業を再開または破棄してから"
                            "新しいGitHub送信を開始してください。"
                        )
                    )
                    return
            current = LibraryTransaction(
                state_dir,
                resumable.transaction_id if resumable is not None else None,
                cancel_event=cancel_event,
            )
            transaction_id.set(current.transaction_id)
        else:
            show_error(SkillMagnetError("先に送信内容を確認してください"))
            return

        active_cancel_event = cancel_event
        active_transaction = current
        labels = {
            "sync": "GitHubへの送信・マージ確認・Skill Magnetへの反映を実行しています…",
            "prepare": "GitHubへ送る内容を検証しています…",
            "publish": "GitHubへ送信しています…",
            "verify": "GitHubのマージ結果を確認しています…",
            "reopen_pr": "閉じたGitHub PRを再度開いています…",
            "activate": "Skill Magnetへ反映しています…",
        }
        set_busy(True, labels[stage])

        def update_menu(path: Path) -> Any:
            return menu_update(path, platform) if menu_update else None

        def work(_: threading.Event) -> Any:
            try:
                if stage == "sync":
                    value = current.complete_automatically(
                        draft=repository_value,
                        remote=remote_value,
                        config_path=config_path,
                        confirmed=True,
                        menu_update=update_menu if menu_update else None,
                    )
                elif stage == "prepare":
                    existing = current._journal()
                    if str(existing.get("status")) != "draft":
                        current.recover()
                        value = current._journal()
                    else:
                        validate_library(repository_value)
                        value = current.prepare(
                            draft=repository_value,
                            remote=remote_value,
                        )
                elif stage == "publish":
                    value = current.publish(confirmed=True)
                elif stage == "verify":
                    value = current.mark_merged()
                elif stage == "reopen_pr":
                    value = current.reopen_pull_request(confirmed=True)
                else:
                    value = current.activate(
                        config_path=config_path,
                        confirmed=True,
                        menu_update=update_menu if menu_update else None,
                    )
            except BaseException as exc:
                raise exc
            return value

        _, worker, outcome = start_library_background_operation(
            work,
            name=f"skill-magnet-library-{stage}",
            cancel_event=cancel_event,
        )
        active_worker = worker

        def poll_worker() -> None:
            nonlocal result, active_worker, active_cancel_event, active_transaction
            if worker.is_alive():
                root.after(50, poll_worker)
                return
            if closing:
                journal = current._journal()
                journal.update(
                    ui_close_completed_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    ui_close_stage=stage,
                    ui_recovery_action="reopen_library_manager",
                )
                current._write_journal(journal)
                result = {
                    "status": "closed_during_processing",
                    "transaction_id": current.transaction_id,
                }
                active_worker = None
                active_cancel_event = None
                active_transaction = None
                close_manager(force=True)
                return
            active_worker = None
            active_cancel_event = None
            active_transaction = None
            set_busy(False)
            error = outcome.get("error")
            if isinstance(error, BaseException):
                if current.journal_path.is_file():
                    handle_transaction_error(
                        error if isinstance(error, Exception) else SkillMagnetError(str(error)),
                        stage,
                    )
                else:
                    transaction_id.set("")
                    show_error(
                        error if isinstance(error, Exception) else SkillMagnetError(str(error))
                    )
                set_stage(action_stage.get())
                return

            value = outcome.get("value")
            if not isinstance(value, dict):
                show_error(SkillMagnetError("処理結果を読み取れません。保存済み作業を再試行してください"))
                set_stage(stage)
                return
            result = value
            set_text(preview_output, value)
            if stage == "prepare":
                if value.get("no_changes"):
                    messagebox.showinfo(
                        "GitHubへの変更はありません",
                        "GitHub上の内容は同じです。検証済み内容をSkill Magnetへ反映できます。",
                        parent=root,
                    )
                    set_stage("activate")
                else:
                    set_stage(stage_for_status(str(current._journal().get("status")), "publish"))
                return
            if stage == "publish":
                set_stage("open_pr" if value.get("pr_url") else "verify")
                return
            if stage == "verify":
                next_stage = automatic_sync_next_stage(value)
                set_stage("activate" if str(value.get("status")) == "verified" else next_stage)
                return
            if stage == "reopen_pr":
                if str(value.get("status")) == "verified":
                    set_stage("activate")
                else:
                    set_stage("sync")
                    root.after(0, run_current_action)
                return
            if str(value.get("status")) == "published_pending":
                next_stage = automatic_sync_next_stage(value)
                set_stage(next_stage)
                if next_stage == "reopen_pr":
                    messagebox.showwarning(
                        "PRがマージされずに閉じられています",
                        "自動監視を停止しました。『閉じたPRを再度開く』を押すと、"
                        "同じPRとtransactionを使って処理を再開できます。",
                        parent=root,
                    )
                    return

                def poll_merge() -> None:
                    if root.winfo_exists() and not closing:
                        set_stage("sync")
                        run_current_action()

                root.after(15_000, poll_merge)
                return
            set_stage("complete")
            purge_result = purge_managed_repository(state_dir, repository_path)
            if not purge_result["purged"]:
                show_error(
                    SkillMagnetError(
                        "GitHub反映は完了しましたが、所有を確認できないローカルコピーを"
                        "自動削除しませんでした。画面の案内から復旧してください"
                    )
                )
                return
            messagebox.showinfo(
                "Library Manager",
                "GitHubへの送信・マージ・Skill Magnetへの反映が完了しました。",
                parent=root,
            )

        root.after(50, poll_worker)

    action_button = ttk.Button(
        publish_frame,
        text=library_action_label("sync"),
        command=run_current_action,
    )
    action_button.grid(row=4, column=0, columnspan=3, sticky="e", pady=(8, 0))
    controls.append(action_button)

    manager_surface_widgets = (
        UiWidgetSpec(
            "configured_remote",
            configured_remote_entry,
            "entry",
            value=lambda: remote.get(),
        ),
        UiWidgetSpec(
            "inventory", inventory_tree, "tree"
        ),
        UiWidgetSpec(
            "inventory_status",
            inventory_frame,
            "status",
            text=lambda: inventory_summary.get(),
        ),
        UiWidgetSpec(
            "new_registration",
            inventory_action_buttons["new_registration"],
            "button",
            text="新規登録",
        ),
        UiWidgetSpec(
            "update",
            inventory_action_buttons["update"],
            "button",
            text="選択項目を更新",
        ),
        UiWidgetSpec(
            "delete",
            inventory_action_buttons["delete"],
            "button",
            text="選択項目を削除",
        ),
        UiWidgetSpec(
            "reload",
            inventory_action_buttons["reload"],
            "button",
            text="再読込",
        ),
        UiWidgetSpec(
            "registration_source",
            registration_source_entry,
            "entry",
            value=lambda: import_source.get(),
        ),
        UiWidgetSpec(
            "registration_browse",
            registration_browse_button,
            "button",
            text="Browse",
        ),
        UiWidgetSpec("register", register_button, "button", text="登録"),
        UiWidgetSpec(
            "preview", preview_output, "text"
        ),
        UiWidgetSpec(
            "sync",
            action_button,
            "button",
            text=lambda: str(action_button.cget("text")),
        ),
        UiWidgetSpec(
            "recovery",
            recovery_button,
            "button",
            text="GitHubから復旧",
        ),
        UiWidgetSpec(
            "status", status_label, "status", text=lambda: processing_status.get()
        ),
    )

    def publish_manager_surface() -> None:
        if closing or not manager_surface_ready:
            return
        state = {
            "processing": busy,
            "stage": action_stage.get(),
            "register_selected": register_selected,
        }
        for identity in tuple(surface_identities):
            try:
                publish_tk_ui_surface(
                    identity,
                    root,
                    widgets=manager_surface_widgets,
                    state=state,
                )
            except Exception:
                # A context handoff may release one owner before this manager
                # closes.  A platform-specific widget query can also fail while
                # Tk is relaying a close event.  Receipt publication is
                # observational and must not abort the user's recoverable work.
                continue

    remote.trace_add("write", lambda *_: publish_manager_surface())
    import_source.trace_add("write", lambda *_: publish_manager_surface())
    inventory_tree.bind("<<TreeviewSelect>>", lambda _: publish_manager_surface())

    def run_initial_registration() -> None:
        nonlocal initial_registration
        if busy:
            return
        try:
            if initial_repository is None:
                raise SkillMagnetError("右クリックしたフォルダーを取得できませんでした")
            source_value = str(initial_repository)
            remote_value = remote.get().strip()
            transaction_value = transaction_id.get().strip()
        except Exception as exc:
            show_error(exc)
            return

        def work(cancel_event: threading.Event) -> dict[str, Any]:
            source = require_registration_source(
                source_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、右クリック登録の書き込み前に中止しました"
                )
            reset_transaction = ensure_editable_library(
                transaction_value, remote_value, cancel_event
            )
            ensure_managed_workspace_ready(
                selected_remote=remote_value, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise SkillMagnetError(
                    "終了操作を受け付けたため、右クリック登録の書き込み前に中止しました"
                )
            return {
                "result": register_skill_source(
                    repository_path, source, cancel_event=cancel_event
                ),
                "reset_transaction": reset_transaction,
            }

        def completed(value: Any) -> None:
            nonlocal initial_registration
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                show_error(
                    SkillMagnetError("右クリック登録の結果を読み取れません。もう一度実行してください")
                )
                return
            initial_registration = value["result"]
            apply_editable_transaction_reset(bool(value.get("reset_transaction")))
            registration.grid_remove()
            refresh_inventory()
            root.after(0, run_current_action)

        run_auxiliary_in_background(
            "右クリックしたフォルダーを検証・登録しています…", work, completed
        )

    def offer_interrupted_transaction() -> None:
        try:
            if not remote.get().strip():
                return
            current = find_resumable_transaction(
                state_dir,
                draft=repository_path,
                remote=remote.get().strip(),
            )
            if current is None:
                return
            transaction_id.set(current.transaction_id)
            raw = current._journal()
            # Startup recovery routing is local-only; GitHub is checked by the
            # background synchronization worker after the user resumes.
            latest = current.status(config_path, check_remote=False)
            if str(raw.get("status")) == "published_pending":
                set_text(preview_output, raw)
                set_stage("sync")
                root.after(0, run_current_action)
                return
            if str(raw.get("status")) == "verified":
                set_text(preview_output, raw)
                set_stage("sync")
                root.after(0, run_current_action)
                return
            if str(raw.get("status")) in {"activating", "menu_pending"}:
                set_text(preview_output, raw)
                set_stage("sync")
                retry = messagebox.askyesno(
                    "Skill Magnetへの反映を再開できます",
                    "設定または右クリックメニューの反映途中で処理が止まりました。\n"
                    "保存済みの直前状態から同じ反映処理を再試行しますか？",
                    parent=root,
                )
                if retry:
                    root.after(0, run_current_action)
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
                set_stage("sync")
                root.after(0, run_current_action)
            elif choice is False:
                abandon_current()
            else:
                root.destroy()
        except Exception as exc:
            show_error(exc)

    def finish_window_initialization() -> None:
        nonlocal recovery_notice_shown
        if recovery.get("recovered") and not recovery_notice_shown:
            recovery_notice_shown = True
            messagebox.showinfo(
                "前回の登録を復旧しました",
                "アプリ終了前のスキルライブラリを復旧しました。もう一度登録できます。",
                parent=root,
            )
        if repair_notice is not None:
            processing_status.set("設定の復旧が必要です")
            repair_action = (
                "公開先のGitHub URLを確認し、「GitHubから復旧」を押した後、"
                "「GitHubへ反映」を実行してください。"
                if offer_remote_restore
                else "公開先のGitHub URLを確認し、「GitHubへ反映」を実行してください。"
            )
            messagebox.showwarning(
                "Skill Magnet設定の復旧",
                f"原因: {repair_notice}\n\n"
                f"次の操作: {repair_action}",
                parent=root,
            )
        if catalog_error is not None:
            processing_status.set(
                "復旧が必要です。GitHub URLを確認し『GitHubから復旧』を押してください。"
            )
            messagebox.showerror(
                "ローカルライブラリを読み取れません",
                f"原因: {catalog_error}\n\n"
                "次の操作: 公開先のGitHub URLを確認し、"
                "『GitHubから復旧』を押してください。元データはバックアップとして残ります。",
                parent=root,
            )
            return
        if legacy_migration_pending:
            set_busy(True, "旧ローカル作業を表示しています…")
            try:
                refresh_inventory()
            finally:
                set_busy(False)
            root.after(0, offer_interrupted_transaction)
            return
        if register_selected:
            run_initial_registration()
            return
        set_busy(True, "登録済みスキルを読み込んでいます…")
        try:
            refresh_inventory()
        finally:
            set_busy(False)
        root.after(0, offer_interrupted_transaction)

    def continue_after_startup_inspection() -> None:
        nonlocal catalog_error, offer_remote_restore, legacy_migration_pending
        legacy_needed = bool(
            configured_remote
            and os.path.lexists(repository_path)
            and not managed_repository_is_owned(state_dir, repository_path)
        )
        hydration_needed = bool(
            configured_remote
            and not managed_repository_has_unfinished_transaction(
                state_dir, repository_path, remote=configured_remote
            )
        )
        if not legacy_needed and not hydration_needed:
            finish_window_initialization()
            return

        def initialize_from_github(cancel_event: threading.Event) -> dict[str, Any]:
            outcome: dict[str, Any] = {
                "catalog_error": catalog_error,
                "offer_remote_restore": offer_remote_restore,
                "legacy_migration_pending": False,
            }
            hydration_allowed = True
            if legacy_needed:
                try:
                    migration = migrate_legacy_managed_repository(
                        state_dir,
                        repository_path,
                        configured_remote,
                        commit=configured_commit,
                        cancel_event=cancel_event,
                    )
                    outcome["legacy_migration_pending"] = (
                        migration.get("status") == "unpublished_edit"
                    )
                    outcome["catalog_error"] = None
                except Exception as exc:
                    outcome["catalog_error"] = str(exc)
                    outcome["offer_remote_restore"] = True
                    hydration_allowed = False
            if (
                hydration_needed
                and hydration_allowed
                and not managed_repository_has_unfinished_transaction(
                    state_dir, repository_path, remote=configured_remote
                )
            ):
                try:
                    hydrate_managed_repository(
                        state_dir,
                        repository_path,
                        configured_remote,
                        commit=configured_commit,
                        cancel_event=cancel_event,
                    )
                    outcome["catalog_error"] = None
                    outcome["offer_remote_restore"] = False
                except Exception as exc:
                    outcome["catalog_error"] = str(exc)
                    outcome["offer_remote_restore"] = True
            return outcome

        def initialized(value: Any) -> None:
            nonlocal catalog_error, offer_remote_restore, legacy_migration_pending
            data = value if isinstance(value, dict) else {}
            catalog_error = data.get("catalog_error")
            offer_remote_restore = bool(data.get("offer_remote_restore"))
            legacy_migration_pending = bool(data.get("legacy_migration_pending"))
            if recovery_button is not None:
                if offer_remote_restore:
                    recovery_button.pack(side="left", padx=3)
                else:
                    recovery_button.pack_forget()
            finish_window_initialization()

        def initialization_failed(exc: Exception) -> None:
            nonlocal catalog_error, offer_remote_restore
            catalog_error = str(exc)
            offer_remote_restore = True
            if recovery_button is not None:
                recovery_button.pack(side="left", padx=3)
            finish_window_initialization()

        run_auxiliary_in_background(
            "設定済みGitHubから編集用データを読み込んでいます…",
            initialize_from_github,
            initialized,
            initialization_failed,
        )

    def begin_after_window_is_visible() -> None:
        """Inspect and recover local state only after Tk has painted the window."""

        def inspect_startup(cancel_event: threading.Event) -> dict[str, Any]:
            if cancel_event.is_set():
                raise SkillMagnetError("終了操作を受け付けたため、起動確認を中止しました")
            next_repair_notice = configuration_repair_notice(config_path)
            next_remote, next_commit = configured_repository_reference(config_path)
            if cancel_event.is_set():
                raise SkillMagnetError("終了操作を受け付けたため、起動確認を中止しました")

            next_recovery: dict[str, Any] = {"recovered": False}
            next_catalog_error: str | None
            if os.path.lexists(repository_path):
                if not managed_repository_is_owned(state_dir, repository_path):
                    # A markerless directory at the historical fixed path may
                    # be a user's own clone.  Never initialize, recover, edit,
                    # adopt, or delete it implicitly.
                    next_catalog_error = (
                        "既存のローカルフォルダーにはLibrary Managerの双方向の所有証明が"
                        "ありません。ユーザーのフォルダーとして保持し、自動編集・削除しません。"
                    )
                else:
                    next_recovery = recover_interrupted_library(repository_path)
                    if cancel_event.is_set():
                        raise SkillMagnetError(
                            "終了操作を受け付けたため、復旧状態を保存して起動確認を中止しました"
                        )
                    next_catalog_error = prepare_managed_repository(repository_path)
            else:
                # Recheck under the same process-wide lock used by CLI CRUD.
                # If another process creates a markerless directory in this
                # window, preserve it instead of claiming it as product data.
                with library_mutation_lock(repository_path):
                    if os.path.lexists(repository_path):
                        if managed_repository_is_owned(state_dir, repository_path):
                            next_catalog_error = prepare_managed_repository(repository_path)
                        else:
                            next_catalog_error = (
                                "起動確認中に所有不明のローカルフォルダーが作成されました。"
                                "自動編集・削除せず保持します。"
                            )
                    else:
                        next_catalog_error = prepare_managed_repository(repository_path)
                        if next_catalog_error is None:
                            mark_managed_repository_owned(
                                state_dir,
                                repository_path,
                                remote=next_remote,
                                commit=next_commit,
                            )
            if cancel_event.is_set():
                raise SkillMagnetError("終了操作を受け付けたため、起動確認を中止しました")
            next_offer_restore = remote_restore_available(
                repository_path,
                config_repair=next_repair_notice,
                catalog_error=next_catalog_error,
            )
            return {
                "repair_notice": next_repair_notice,
                "configured_remote": next_remote,
                "configured_commit": next_commit,
                "recovery": next_recovery,
                "catalog_error": next_catalog_error,
                "offer_remote_restore": next_offer_restore,
            }

        def inspected(value: Any) -> None:
            nonlocal manager_surface_ready
            nonlocal repair_notice, configured_remote, configured_commit
            nonlocal recovery, catalog_error, offer_remote_restore
            data = value if isinstance(value, dict) else {}
            repair_notice = data.get("repair_notice")
            configured_remote = str(data.get("configured_remote", ""))
            configured_commit = str(data.get("configured_commit", ""))
            recovery = data.get("recovery") if isinstance(data.get("recovery"), dict) else {
                "recovered": False
            }
            catalog_error = data.get("catalog_error")
            offer_remote_restore = bool(data.get("offer_remote_restore"))
            remote.set(configured_remote)
            if recovery_button is not None:
                if offer_remote_restore:
                    recovery_button.pack(side="left", padx=3)
                else:
                    recovery_button.pack_forget()
            # Do not publish a semantically actionable surface before the
            # configured remote and recovery controls have been loaded.  A
            # receipt with temporary startup values can otherwise be consumed
            # between first paint and startup inspection.
            manager_surface_ready = True
            publish_manager_surface()
            continue_after_startup_inspection()

        def inspection_failed(exc: Exception) -> None:
            nonlocal manager_surface_ready
            nonlocal catalog_error, offer_remote_restore
            catalog_error = str(exc)
            offer_remote_restore = True
            if recovery_button is not None:
                recovery_button.pack(side="left", padx=3)
            manager_surface_ready = True
            publish_manager_surface()
            finish_window_initialization()

        run_auxiliary_in_background(
            "設定・保存済み作業・ローカルライブラリを確認しています…",
            inspect_startup,
            inspected,
            inspection_failed,
            allow_while_busy=True,
        )

    processing_status.set(
        "受付完了：右クリックしたフォルダーの登録を開始します…"
        if register_selected
        else "受付完了：Library Managerを読み込んでいます…"
    )
    set_busy(True, "起動状態を確認しています…")
    root.after(50, begin_after_window_is_visible)

    def close_manager(*, force: bool = False) -> None:
        nonlocal closing, result
        if busy and active_worker is not None and active_worker.is_alive() and not force:
            # Do not destroy Tk while a worker may still report a result.  Ask
            # the bounded subprocess runner to terminate its child; its
            # cancellation error checkpoints the exact transaction for reopen.
            closing = True
            processing_status.set(
                "終了要求を保存しています。外部処理を中止後、この画面を閉じます…"
            )
            root.title("Library Manager — 終了処理中")
            if active_cancel_event is not None:
                active_cancel_event.set()
            return
        cleanup_problem = ""
        try:
            if not managed_repository_has_unfinished_transaction(
                state_dir, repository_path
            ):
                try:
                    purged = purge_managed_repository(state_dir, repository_path)
                    if not purged["purged"]:
                        cleanup_problem = (
                            "所有を確認できないローカルフォルダーは削除せず残しました。"
                            "次回Library Managerで内容を確認し、GitHubへ送るか明示的に破棄してください。"
                        )
                except Exception as exc:
                    # Closing the window must remain possible even if Windows has
                    # a transient file handle on the disposable clone.  The marker
                    # is kept, so the next launch can retry the same bounded purge.
                    cleanup_problem = (
                        "一時コピーを削除できなかったため、そのまま残しました。"
                        "次回Library Managerで自動復旧できます。原因: " + str(exc)
                    )
            if cleanup_problem:
                result = {
                    "status": "closed_cleanup_pending",
                    "repository": str(repository_path),
                    "recovery": "reopen_library_manager",
                    "detail": cleanup_problem,
                }
                try:
                    messagebox.showwarning(
                        "終了後に復旧できます", cleanup_problem, parent=root
                    )
                except Exception:
                    pass
            root.destroy()
        finally:
            # Keep the single-flight lease until the original window has
            # actually gone; otherwise a repeated right-click can briefly
            # create a second manager while this one is still visible.
            lease.release()

    root.protocol("WM_DELETE_WINDOW", close_manager)
    try:
        root.mainloop()
    finally:
        lease.release()
    return result
