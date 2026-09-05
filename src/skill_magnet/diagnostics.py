from __future__ import annotations

import hashlib
import json
import os
import queue
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UI_DIAGNOSTIC_SCHEMA_VERSION = 1
UI_DIAGNOSTIC_MAX_BYTES = 256 * 1024
UI_DIAGNOSTIC_QUEUE_SIZE = 1024
_writers: dict[tuple[str, str, str], "_UiDiagnosticWriter"] = {}
_writers_lock = threading.Lock()


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _safe_diagnostic_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("LOCALAPPDATA is unavailable")
    trusted = Path(local_app_data)
    if not trusted.is_absolute() or _is_reparse_or_link(trusted):
        raise OSError("diagnostic root is not a trusted regular directory")
    current = trusted
    for name in ("SkillMagnet", "ContextMenu", "diagnostics"):
        current = current / name
        try:
            current.mkdir()
        except FileExistsError:
            pass
        if not current.is_dir() or _is_reparse_or_link(current):
            raise OSError("diagnostic directory is not a regular directory")
    return current


def _path_sha256(path: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class _UiDiagnosticWriter:
    def __init__(self, identity: Any) -> None:
        self.process_instance_id = str(identity.process_instance_id)
        self.generation = str(identity.generation)
        self.owner_path_sha256 = _path_sha256(Path(identity.owner_path))
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=UI_DIAGNOSTIC_QUEUE_SIZE
        )
        self.path: Path | None = None
        self.error: str | None = None
        self.stop_requested = threading.Event()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def enqueue(self, record: dict[str, Any]) -> bool:
        if self.closed.is_set() or self.stop_requested.is_set():
            return False
        try:
            self.messages.put_nowait(record)
        except queue.Full:
            return False
        return True

    def close(self, timeout: float) -> None:
        self.stop_requested.set()
        try:
            self.messages.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(max(0.0, timeout))

    def _run(self) -> None:
        descriptor: int | None = None
        try:
            root = _safe_diagnostic_root()
            filename = (
                f"ui-{os.getpid()}-{self.process_instance_id}-{self.generation}-"
                f"{self.owner_path_sha256[:16]}-{uuid.uuid4().hex}.jsonl"
            )
            self.path = root / filename
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            sequence = 0
            written_total = 0
            while True:
                try:
                    message = self.messages.get(timeout=0.05)
                except queue.Empty:
                    if self.stop_requested.is_set():
                        break
                    continue
                if message is None:
                    break
                sequence += 1
                message["seq"] = sequence
                encoded = (
                    json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                if len(encoded) > 1024 or written_total + len(encoded) > UI_DIAGNOSTIC_MAX_BYTES:
                    continue
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("short diagnostic append")
                written_total += written
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            if descriptor is not None:
                try:
                    os.fsync(descriptor)
                except OSError:
                    pass
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            self.closed.set()


def _writer_key(identity: Any) -> tuple[str, str, str]:
    return (
        str(identity.process_instance_id),
        str(identity.generation),
        _path_sha256(Path(identity.owner_path)),
    )


def record_ui_publication_event(
    event: str,
    identity: Any,
    *,
    revision: int | None = None,
    attempt: int = 0,
    winerror: int | None = None,
) -> bool:
    """Enqueue non-secret telemetry without performing filesystem I/O."""

    allowed_events = {
        "retry_request", "retry_coalesce", "retry_attempt", "retry_error",
        "retry_scheduled", "retry_expired", "retry_success", "retry_terminal",
        "retry_stopped",
    }
    if event not in allowed_events:
        return False
    try:
        key = _writer_key(identity)
        with _writers_lock:
            writer = _writers.get(key)
            if writer is None or writer.closed.is_set():
                writer = _UiDiagnosticWriter(identity)
                _writers[key] = writer
        return writer.enqueue(
            {
                "schema_version": UI_DIAGNOSTIC_SCHEMA_VERSION,
                "seq": 0,
                "timestamp_utc": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "event": event,
                "pid": os.getpid(),
                "process_instance_id": key[0],
                "generation": key[1],
                "owner_path_sha256": key[2],
                "phase": str(identity.phase),
                "revision": revision,
                "attempt": int(attempt),
                "winerror": winerror,
            }
        )
    except Exception:
        return False


def close_ui_publication_diagnostics(identity: Any, timeout: float = 0.25) -> None:
    """Bounded flush/close for one publication identity."""

    try:
        key = _writer_key(identity)
    except Exception:
        return
    with _writers_lock:
        writer = _writers.pop(key, None)
    if writer is not None:
        writer.close(timeout)
