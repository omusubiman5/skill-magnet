from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UI_DIAGNOSTIC_SCHEMA_VERSION = 1
UI_DIAGNOSTIC_MAX_BYTES = 256 * 1024
_sequence = 0
_sequence_lock = threading.Lock()


def _diagnostic_path() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "SkillMagnet" / "ContextMenu" / "ui-diagnostic.jsonl"


def _path_sha256(path: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_ui_publication_event(
    event: str,
    identity: Any,
    *,
    revision: int | None = None,
    attempt: int = 0,
    winerror: int | None = None,
) -> None:
    """Best-effort append-only telemetry without UI/request/path content."""

    allowed_events = {
        "retry_request",
        "retry_coalesce",
        "retry_attempt",
        "retry_error",
        "retry_scheduled",
        "retry_success",
        "retry_terminal",
        "retry_stopped",
    }
    if event not in allowed_events:
        return
    path = _diagnostic_path()
    if path is None:
        return
    try:
        global _sequence
        with _sequence_lock:
            _sequence += 1
            sequence = _sequence
            record = {
                "schema_version": UI_DIAGNOSTIC_SCHEMA_VERSION,
                "seq": sequence,
                "timestamp_utc": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "event": event,
                "pid": os.getpid(),
                "process_instance_id": str(identity.process_instance_id),
                "generation": str(identity.generation),
                "owner_path_sha256": _path_sha256(Path(identity.owner_path)),
                "phase": str(identity.phase),
                "revision": revision,
                "attempt": int(attempt),
                "winerror": winerror,
            }
            encoded = (
                json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            if len(encoded) > 1024:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            current_size = path.stat().st_size if path.exists() else 0
            if current_size + len(encoded) > UI_DIAGNOSTIC_MAX_BYTES:
                return
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                written = os.write(descriptor, encoded)
                if written == len(encoded):
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        # Diagnostics must never change the product result or recovery path.
        return
