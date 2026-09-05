from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import os
import io
import json
import re
import stat
import subprocess
import sys
import hashlib
import struct
import tempfile
import time
import tomllib
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

LEDGER_START = "<!-- explorer-results-ledger:start"
LEDGER_END = "explorer-results-ledger:end -->"

_UI_OWNER_KEYS = {
    "schema_version", "owner_kind", "pid", "process_instance_id",
    "process_started_at_unix_ns", "target_sha256", "generation", "phase",
    "window_handle", "revision", "published_at_utc",
}
_UI_SURFACE_KEYS = {
    "schema_version", "generation", "pid", "phase", "window", "state",
    "widgets", "revision", "published_at_utc",
}
_UI_RECT_KEYS = {"x", "y", "width", "height"}
_UI_WIDGET_REQUIRED_KEYS = {
    "id", "role", "state", "viewable", "hwnd", "client", "screen",
}
_UI_WIDGET_OPTIONAL_KEYS = {"text_sha256", "value_sha256", "values_sha256"}


def _validate_ui_owner_receipt_schema(value: object) -> None:
    """Reject any receipt shape not emitted by the v2/v1 field producer."""

    def exact_object(item: object, keys: set[str], label: str) -> dict[str, object]:
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError(f"{label} keys do not match the exact receipt schema")
        return item

    def exact_int(item: object, label: str, *, positive: bool = False) -> int:
        if type(item) is not int or (positive and item <= 0):
            raise ValueError(f"{label} must be an exact integer")
        return item

    def exact_bool(item: object, label: str) -> bool:
        if type(item) is not bool:
            raise ValueError(f"{label} must be an exact boolean")
        return item

    def text(item: object, label: str, pattern: str | None = None) -> str:
        if not isinstance(item, str) or (pattern and not re.fullmatch(pattern, item)):
            raise ValueError(f"{label} must be a valid string")
        return item

    def rectangle(item: object, label: str) -> None:
        rect = exact_object(item, _UI_RECT_KEYS, label)
        for key in _UI_RECT_KEYS:
            exact_int(rect[key], f"{label}.{key}")

    if not isinstance(value, dict):
        raise ValueError("owner must be an object")
    allowed_owner_keys = _UI_OWNER_KEYS | ({"ui_surface"} if "ui_surface" in value else set())
    owner = exact_object(value, allowed_owner_keys, "owner")
    if exact_int(owner["schema_version"], "owner.schema_version") != 2:
        raise ValueError("owner.schema_version must be 2")
    if text(owner["owner_kind"], "owner.owner_kind") != "context_launcher":
        raise ValueError("owner.owner_kind must be context_launcher")
    exact_int(owner["pid"], "owner.pid", positive=True)
    text(owner["process_instance_id"], "owner.process_instance_id", r"[0-9a-f]{32}")
    exact_int(
        owner["process_started_at_unix_ns"],
        "owner.process_started_at_unix_ns",
        positive=True,
    )
    text(owner["target_sha256"], "owner.target_sha256", r"[0-9a-f]{64}")
    text(owner["generation"], "owner.generation", r"[0-9a-f]{32}")
    phase = text(owner["phase"], "owner.phase")
    if phase not in {"context_starting", "context_selection", "library_manager"}:
        raise ValueError("owner.phase is not supported by the field receipt schema")
    exact_int(owner["window_handle"], "owner.window_handle")
    exact_int(owner["revision"], "owner.revision", positive=True)
    text(owner["published_at_utc"], "owner.published_at_utc")
    if "ui_surface" not in owner:
        if phase != "context_starting":
            raise ValueError(f"{phase} owner must contain ui_surface")
        return
    if phase not in {"context_selection", "library_manager"}:
        raise ValueError("starting owner must not contain ui_surface")

    surface = exact_object(owner["ui_surface"], _UI_SURFACE_KEYS, "ui_surface")
    if exact_int(surface["schema_version"], "ui_surface.schema_version") != 1:
        raise ValueError("ui_surface.schema_version must be 1")
    if surface["generation"] != owner["generation"]:
        raise ValueError("ui_surface.generation does not match owner")
    if surface["pid"] != owner["pid"] or type(surface["pid"]) is not int:
        raise ValueError("ui_surface.pid does not match owner")
    if surface["phase"] != phase or not isinstance(surface["phase"], str):
        raise ValueError("ui_surface.phase does not match owner")
    if surface["revision"] != owner["revision"] or type(surface["revision"]) is not int:
        raise ValueError("ui_surface.revision does not match owner")
    if surface["published_at_utc"] != owner["published_at_utc"] or not isinstance(
        surface["published_at_utc"], str
    ):
        raise ValueError("ui_surface publication does not match owner")
    window = exact_object(
        surface["window"], {"hwnd", "title_sha256", "client", "screen"}, "window"
    )
    if window["hwnd"] != owner["window_handle"] or type(window["hwnd"]) is not int:
        raise ValueError("window.hwnd does not match owner")
    text(window["title_sha256"], "window.title_sha256", r"[0-9a-f]{64}")
    rectangle(window["client"], "window.client")
    rectangle(window["screen"], "window.screen")

    if not isinstance(surface["state"], dict):
        raise ValueError("state must be an object")
    state_keys = set(surface["state"])
    if phase == "context_selection":
        state = exact_object(
            surface["state"],
            {"language_sha256", "selection_mode_sha256", "processing", "details_visible"},
            "state",
        )
        text(state["language_sha256"], "state.language_sha256", r"[0-9a-f]{64}")
        text(
            state["selection_mode_sha256"],
            "state.selection_mode_sha256",
            r"[0-9a-f]{64}",
        )
        exact_bool(state["processing"], "state.processing")
        exact_bool(state["details_visible"], "state.details_visible")
    else:
        if state_keys not in (
            {"processing", "register_selected"},
            {"processing", "register_selected", "stage_sha256"},
        ):
            raise ValueError("state keys do not match the library_manager schema")
        state = surface["state"]
        exact_bool(state["processing"], "state.processing")
        exact_bool(state["register_selected"], "state.register_selected")
        if "stage_sha256" in state:
            text(state["stage_sha256"], "state.stage_sha256", r"[0-9a-f]{64}")

    widgets = surface["widgets"]
    if not isinstance(widgets, list):
        raise ValueError("widgets must be an array")
    for index, candidate in enumerate(widgets):
        label = f"widgets[{index}]"
        if not isinstance(candidate, dict):
            raise ValueError(f"{label} must be an object")
        keys = set(candidate)
        if not (_UI_WIDGET_REQUIRED_KEYS <= keys <= _UI_WIDGET_REQUIRED_KEYS | _UI_WIDGET_OPTIONAL_KEYS):
            raise ValueError(f"{label} keys do not match the exact widget schema")
        text(candidate["id"], f"{label}.id")
        text(candidate["role"], f"{label}.role")
        exact_bool(candidate["viewable"], f"{label}.viewable")
        exact_int(candidate["hwnd"], f"{label}.hwnd", positive=True)
        rectangle(candidate["client"], f"{label}.client")
        rectangle(candidate["screen"], f"{label}.screen")
        widget_state = exact_object(
            candidate["state"], {"configured", "enabled"}, f"{label}.state"
        )
        text(widget_state["configured"], f"{label}.state.configured")
        exact_bool(widget_state["enabled"], f"{label}.state.enabled")
        if candidate["id"] == "request" and keys & _UI_WIDGET_OPTIONAL_KEYS:
            raise ValueError("request widget must not contain content digests")
        for key in keys & _UI_WIDGET_OPTIONAL_KEYS:
            text(candidate[key], f"{label}.{key}", r"[0-9a-f]{64}")


def parse_ledger(text: str) -> dict[str, object]:
    start, end = text.find(LEDGER_START), text.find(LEDGER_END)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("results ledger markers are missing or out of order")
    return json.loads(text[start + len(LEDGER_START) : end].strip())


def validate_consistency(text: str, *, observed_test_count: int,
                         observed_leaf_count: int,
                         observed_selection_kinds: list[str],
                         observed_pack_skill_counts: list[int],
                         observed_version: str | None = None) -> list[str]:
    ledger = parse_ledger(text)
    errors: list[str] = []
    expected = {"full_test_count": observed_test_count,
                "menu_leaf_count": 0,
                "menu_action_count": 1,
                "root_launcher_entry_count": 1,
                "configured_selection_count": observed_leaf_count,
                "library_manager_entry_count": 0,
                "register_folder_entry_count": 0,
                "selection_kinds": observed_selection_kinds,
                "pack_skill_counts": observed_pack_skill_counts}
    for key, actual in expected.items():
        if ledger.get(key) != actual:
            errors.append(f"{key} mismatch: ledger={ledger.get(key)!r}, observed={actual!r}")
    summary = re.search(r"統合テスト: .*?— (\d+) tests PASS", text)
    if summary is None or int(summary.group(1)) != observed_test_count:
        errors.append("human-readable test count mismatch")
    if ledger.get("release_scope") != "direct-root-unified-selector":
        errors.append("release_scope must be direct-root-unified-selector")
    if observed_version is not None:
        required_release_state = {
            "release_version": observed_version,
            "distribution_scope": "local-self-signed",
            "automated_status": f"LOCAL_RELEASE_GATE_PASS_{observed_test_count}",
            "windows_explorer_field_status": (
                "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_"
                + observed_version.replace(".", "_")
            ),
            "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
            "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED",
        }
        for key, expected_value in required_release_state.items():
            if ledger.get(key) != expected_value:
                errors.append(
                    f"{key} mismatch: ledger={ledger.get(key)!r}, "
                    f"observed={expected_value!r}"
                )
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            str(ledger.get("windows_explorer_field_invoke_log_sha256", "")),
        ):
            errors.append(
                "windows_explorer_field_invoke_log_sha256 must be current 64-hex evidence"
            )
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            str(ledger.get("windows_explorer_field_bundle_sha256", "")),
        ):
            errors.append(
                "windows_explorer_field_bundle_sha256 must be current 64-hex evidence"
            )
        if not re.fullmatch(
            r"[0-9a-f]{40}",
            str(ledger.get("windows_explorer_field_signer_thumbprint", "")),
        ):
            errors.append(
                "windows_explorer_field_signer_thumbprint must pin the field signer"
            )
    if not re.fullmatch(r"[0-9a-f]{40}", str(ledger.get("release_code_sha", ""))):
        errors.append("release_code_sha must be a lowercase 40-hex commit")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(ledger.get("wheel_payload_sha256", ""))
    ):
        errors.append("wheel_payload_sha256 must be a lowercase 64-hex digest")
    stale = (r"18\s*(?:個別|immediate)\s*(?:leaf|leaves)",
             r"固定9\s*skills\s*[×x]\s*(?:Codex|Claude)",
             r"保管庫の固定commitから個別skillを選び")
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in stale):
        errors.append("stale individual-skill menu claim remains")
    return errors


def wheel_payload_sha256(wheel: Path) -> str:
    """Hash logical wheel payload, excluding RECORD and normalizing text EOLs."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(wheel) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                continue
            content = archive.read(name)
            if b"\0" not in content:
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    content = content.replace(b"\r\n", b"\n")
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
    return digest.hexdigest()


def _normalized_text_bytes(payload: bytes) -> bytes:
    """Normalize only checkout-dependent CRLF without hiding other byte changes."""
    return payload.replace(b"\r\n", b"\n")


def _normalized_windows_powershell_bytes(payload: bytes) -> bytes:
    """Normalize the UTF-8 BOM required by Windows PowerShell 5.1.

    The collector contains Japanese UI assertions.  Windows PowerShell 5.1
    reads a BOM-free ``-File`` as the active ANSI code page, so the BOM is an
    execution marker rather than release content.  No other leading bytes are
    ignored.
    """

    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    return _normalized_text_bytes(payload)


def _logical_runtime_digest(entries: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(entries):
        content = entries[name]
        if b"\0" not in content:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                content = _normalized_text_bytes(content)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


_RUNTIME_TREE_MAX_ENTRIES = 4096
_RUNTIME_TREE_MAX_FILE_BYTES = 32 * 1024 * 1024
_RUNTIME_TREE_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_RUNTIME_TREE_MAX_SECONDS = 15.0
_RUNTIME_TREE_READ_CHUNK_BYTES = 1024 * 1024
_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x400


def _runtime_tree_failure(label: str, detail: str) -> ValueError:
    return ValueError(
        f"release runtime safety scan rejected {label}: {detail}. "
        "Restore a stable regular-file checkout, rebuild/reinstall the same "
        "release, and rerun the field gate"
    )


def _runtime_path_is_reparse(path: Path, metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    if int(getattr(metadata, "st_file_attributes", 0)) & _WINDOWS_REPARSE_POINT_ATTRIBUTE:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _runtime_metadata_identity(metadata: os.stat_result, *, directory: bool) -> tuple[int, ...]:
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
        int(getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000))),
    )
    if directory:
        return identity
    return identity + (int(metadata.st_size),)


def _runtime_entry_fingerprint(
    metadata: os.stat_result, *, directory: bool
) -> tuple[int, ...]:
    fingerprint = (
        int(stat.S_IFMT(metadata.st_mode)),
        int(getattr(metadata, "st_file_attributes", 0)),
        int(getattr(metadata, "st_reparse_tag", 0)),
    )
    if directory:
        return fingerprint
    return fingerprint + (
        int(getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000))),
        int(metadata.st_size),
    )


def _runtime_checked_metadata(
    path: Path,
    *,
    label: str,
    directory: bool,
    expected: tuple[int, ...] | None = None,
) -> tuple[os.stat_result, tuple[int, ...]]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise _runtime_tree_failure(label, f"lstat failed ({error.__class__.__name__})") from error
    if _runtime_path_is_reparse(path, metadata):
        raise _runtime_tree_failure(label, "links, junctions, and reparse points are forbidden")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        kind = "directory" if directory else "regular file"
        raise _runtime_tree_failure(label, f"expected a {kind}")
    identity = _runtime_metadata_identity(metadata, directory=directory)
    if expected is not None and identity != expected:
        raise _runtime_tree_failure(label, "identity or metadata changed during verification")
    return metadata, identity


def _runtime_check_budget(budget: dict[str, int | float], *, label: str) -> None:
    if time.monotonic() > float(budget["deadline"]):
        raise _runtime_tree_failure(label, "the bounded scan deadline expired")


def _runtime_note_entry(budget: dict[str, int | float], *, label: str) -> None:
    _runtime_check_budget(budget, label=label)
    budget["entries"] = int(budget["entries"]) + 1
    if int(budget["entries"]) > _RUNTIME_TREE_MAX_ENTRIES:
        raise _runtime_tree_failure(
            label, f"entry count exceeds {_RUNTIME_TREE_MAX_ENTRIES}"
        )


def _runtime_stable_read(
    path: Path,
    *,
    label: str,
    metadata: os.stat_result,
    identity: tuple[int, ...],
    parent: Path,
    parent_identity: tuple[int, ...],
    budget: dict[str, int | float],
) -> bytes:
    size = int(metadata.st_size)
    if size > _RUNTIME_TREE_MAX_FILE_BYTES:
        raise _runtime_tree_failure(
            label, f"file size {size} exceeds {_RUNTIME_TREE_MAX_FILE_BYTES} bytes"
        )
    if int(budget["bytes"]) + size > _RUNTIME_TREE_MAX_TOTAL_BYTES:
        raise _runtime_tree_failure(
            label, f"total bytes exceed {_RUNTIME_TREE_MAX_TOTAL_BYTES}"
        )
    _runtime_check_budget(budget, label=label)
    _runtime_checked_metadata(
        parent, label=f"parent of {label}", directory=True, expected=parent_identity
    )
    _runtime_checked_metadata(
        path, label=label, directory=False, expected=identity
    )
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(
        getattr(os, "O_NOINHERIT", 0)
    )
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _runtime_metadata_identity(opened, directory=False) != identity:
            raise _runtime_tree_failure(label, "opened file identity differs from lstat")
        _runtime_checked_metadata(
            path, label=label, directory=False, expected=identity
        )
        _runtime_checked_metadata(
            parent, label=f"parent of {label}", directory=True, expected=parent_identity
        )
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            _runtime_check_budget(budget, label=label)
            chunk = os.read(descriptor, min(_RUNTIME_TREE_READ_CHUNK_BYTES, remaining))
            _runtime_check_budget(budget, label=label)
            if not chunk:
                raise _runtime_tree_failure(label, "file ended before its verified size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise _runtime_tree_failure(label, "file grew while it was being read")
        _runtime_check_budget(budget, label=label)
        payload = b"".join(chunks)
        if len(payload) != size:
            raise _runtime_tree_failure(label, "read size differs from verified size")
        after_open = os.fstat(descriptor)
        if _runtime_metadata_identity(after_open, directory=False) != identity:
            raise _runtime_tree_failure(label, "opened file changed while it was read")
        _runtime_checked_metadata(
            path, label=label, directory=False, expected=identity
        )
        _runtime_checked_metadata(
            parent, label=f"parent of {label}", directory=True, expected=parent_identity
        )
    except OSError as error:
        raise _runtime_tree_failure(label, f"bounded read failed ({error.__class__.__name__})") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    budget["bytes"] = int(budget["bytes"]) + size
    return payload


def _runtime_collect_tree(
    root: Path,
    *,
    prefix: str,
    label: str,
    budget: dict[str, int | float],
    include_file: Callable[[Path], bool],
    skip_directory: Callable[[Path], bool],
    reject_directory: Callable[[Path], bool] | None = None,
) -> dict[str, bytes]:
    _, root_identity = _runtime_checked_metadata(root, label=label, directory=True)
    pending: list[tuple[Path, Path, tuple[int, ...]]] = [
        (root, Path(), root_identity)
    ]
    collected: dict[str, bytes] = {}
    while pending:
        directory, relative_directory, expected_directory_identity = pending.pop()
        _, directory_identity = _runtime_checked_metadata(
            directory,
            label=(label if not relative_directory.parts else f"{label}/{relative_directory.as_posix()}"),
            directory=True,
            expected=expected_directory_identity,
        )
        directory_label = (
            label if not relative_directory.parts else f"{label}/{relative_directory.as_posix()}"
        )
        _runtime_check_budget(budget, label=directory_label)
        try:
            scanner = os.scandir(directory)
        except OSError as error:
            raise _runtime_tree_failure(
                directory_label, f"directory open failed ({error.__class__.__name__})"
            ) from error
        try:
            _runtime_checked_metadata(
                directory,
                label=directory_label,
                directory=True,
                expected=directory_identity,
            )
            discovered: list[tuple[Path, Path, os.stat_result, tuple[int, ...], bool]] = []
            for entry in scanner:
                child = directory / entry.name
                relative = relative_directory / entry.name
                child_label = f"{label}/{relative.as_posix()}"
                _runtime_note_entry(budget, label=child_label)
                try:
                    entry_metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise _runtime_tree_failure(
                        child_label, f"entry stat failed ({error.__class__.__name__})"
                    ) from error
                if _runtime_path_is_reparse(child, entry_metadata):
                    raise _runtime_tree_failure(
                        child_label, "links, junctions, and reparse points are forbidden"
                    )
                is_directory = stat.S_ISDIR(entry_metadata.st_mode)
                if not is_directory and not stat.S_ISREG(entry_metadata.st_mode):
                    raise _runtime_tree_failure(child_label, "unsupported filesystem entry type")
                current_metadata, current_identity = _runtime_checked_metadata(
                    child,
                    label=child_label,
                    directory=is_directory,
                )
                if _runtime_entry_fingerprint(
                    entry_metadata, directory=is_directory
                ) != _runtime_entry_fingerprint(
                    current_metadata, directory=is_directory
                ):
                    raise _runtime_tree_failure(
                        child_label, "entry metadata changed between stat and lstat"
                    )
                _runtime_checked_metadata(
                    directory,
                    label=directory_label,
                    directory=True,
                    expected=directory_identity,
                )
                discovered.append(
                    (child, relative, current_metadata, current_identity, is_directory)
                )
        finally:
            scanner.close()
        _runtime_check_budget(budget, label=directory_label)
        _runtime_checked_metadata(
            directory,
            label=directory_label,
            directory=True,
            expected=directory_identity,
        )
        for child, relative, metadata, identity, is_directory in sorted(
            discovered, key=lambda item: item[1].as_posix()
        ):
            child_label = f"{label}/{relative.as_posix()}"
            if is_directory:
                if reject_directory is not None and reject_directory(relative):
                    raise _runtime_tree_failure(
                        child_label, "this generated/residue directory is not allowed here"
                    )
                if not skip_directory(relative):
                    pending.append((child, relative, identity))
                continue
            if not include_file(relative):
                continue
            payload = _runtime_stable_read(
                child,
                label=child_label,
                metadata=metadata,
                identity=identity,
                parent=directory,
                parent_identity=directory_identity,
                budget=budget,
            )
            collected[prefix + relative.as_posix()] = payload
        _runtime_checked_metadata(
            directory,
            label=directory_label,
            directory=True,
            expected=directory_identity,
        )
    return collected


def _release_runtime_payload_sha256(repository: Path) -> str:
    """Hash release inputs with a bounded, non-following, stable tree walk."""
    repository = Path(os.path.abspath(repository))
    _, repository_identity = _runtime_checked_metadata(
        repository, label="repository root", directory=True
    )
    budget: dict[str, int | float] = {
        "deadline": time.monotonic() + _RUNTIME_TREE_MAX_SECONDS,
        "entries": 0,
        "bytes": 0,
    }
    package_source = repository / "src" / "skill_magnet"
    native_source = repository / "native" / "windows-modern-context-menu"
    blocked_names = {".git", "out", "__pycache__"}
    blocked_suffixes = {".obj", ".lib", ".exp", ".pyc"}
    entries = _runtime_collect_tree(
        package_source,
        prefix="skill_magnet/",
        label="package source",
        budget=budget,
        include_file=lambda relative: relative.suffix.lower() == ".py",
        skip_directory=lambda relative: relative.name == "__pycache__",
    )
    entries.update(
        _runtime_collect_tree(
            native_source,
            prefix="skill_magnet/_native/windows-modern-context-menu/",
            label="native source",
            budget=budget,
            include_file=lambda relative: relative.suffix.lower() not in blocked_suffixes,
            skip_directory=lambda relative: relative.name in blocked_names,
        )
    )
    config = repository / "skill-magnet.json"
    _runtime_note_entry(budget, label="release config")
    config_metadata, config_identity = _runtime_checked_metadata(
        config, label="release config", directory=False
    )
    entries["skill_magnet/skill-magnet.json"] = _runtime_stable_read(
        config,
        label="release config",
        metadata=config_metadata,
        identity=config_identity,
        parent=repository,
        parent_identity=repository_identity,
        budget=budget,
    )
    _runtime_checked_metadata(
        repository,
        label="repository root",
        directory=True,
        expected=repository_identity,
    )
    return _logical_runtime_digest(entries)


def validate_release_provenance(
    repository: Path, ledger: dict[str, object], wheel: Path | None
) -> list[str]:
    errors: list[str] = []
    release_sha = str(ledger.get("release_code_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        return errors
    commit = subprocess.run(
        ["git", "cat-file", "-e", f"{release_sha}^{{commit}}"],
        cwd=repository,
        capture_output=True,
    )
    if commit.returncode != 0:
        errors.append("release_code_sha does not identify a repository commit")
        return errors
    artifact_inputs = [
        ".github/workflows",
        "integration",
        "README.md",
        "docs/mvp-redesign.md",
        "docs/images",
        "docs/skill-library-management-requirements.md",
        "docs/skill-library-manager-implementation-report-2026-09-02.md",
        "docs/windows-modern-context-menu.md",
        "docs/root-cause-windows-context-root-launch-2026-09-05.md",
        "docs/fix-report-windows-context-root-launch-2026-09-05.md",
        "src",
        "native",
        "policy",
        "tests",
        "setup.py",
        "pyproject.toml",
        "skill-magnet.json",
        ".approved-snapshots",
    ]
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{release_sha}..HEAD", "--", *artifact_inputs],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if changed:
        errors.append("artifact inputs changed after release_code_sha: " + changed)
    worktree_changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *artifact_inputs],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if worktree_changed:
        errors.append("uncommitted artifact inputs remain: " + worktree_changed)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *artifact_inputs],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if untracked:
        errors.append("untracked artifact inputs remain: " + untracked)
    if wheel is not None:
        actual = wheel_payload_sha256(wheel)
        if actual != ledger.get("wheel_payload_sha256"):
            errors.append(
                "wheel_payload_sha256 mismatch: "
                f"ledger={ledger.get('wheel_payload_sha256')!r}, observed={actual!r}"
            )
    return errors


_FIELD_SOURCES = ("selected_item", "background_site")
_NATIVE_EVENTS = (
    "invoke_enter",
    "selection_succeeded",
    "create_process_succeeded",
    "child_running",
)
_NATIVE_SEQUENCE_ROLES = (
    ("selected_item", "selected_item", ("child_running",)),
    ("manager_same_folder", "selected_item", ("child_running", "child_exited")),
    ("manager_different_folder", "background_site", ("child_running",)),
    ("background_site", "background_site", ("child_running",)),
    ("same_folder_repeat", "background_site", ("child_running", "child_exited")),
    ("different_folder_busy", "background_site", ("child_running",)),
    ("closed_window_relaunch", "background_site", ("child_running",)),
    ("missing_skill_registration", "selected_item", ("child_running",)),
    ("runtime_skill_projectless", "selected_item", ("child_running",)),
)
_TRANSCRIPT_EVENTS = (
    "context_menu_root_observed",
    "root_invoke_dispatched",
    "unified_gui_observed",
    "native_sequence_bound",
)
_RECOVERY_TRANSCRIPT_EVENTS = (
    ("same_folder_repeat_observed", "same_folder_repeat"),
    ("different_folder_busy_observed", "different_folder_busy"),
    ("closed_window_relaunch_observed", "closed_window_relaunch"),
)
_WORKFLOW_TRANSCRIPT_EVENTS = (
    ("library_manager_flow_observed", "library_manager_flow"),
    ("missing_skill_registration_observed", "missing_skill_registration"),
    ("runtime_skill_projectless_observed", "runtime_skill_projectless"),
)
_FIELD_HASH_KEYS = (
    "appx_manifest_sha256",
    "menu_manifest_sha256",
    "dll_sha256",
    "identity_sha256",
    "native_source_manifest_sha256",
    "external_dll_sha256",
    "external_identity_sha256",
    "external_native_source_manifest_sha256",
    "signed_msix_sha256",
    "contract_probe_output_sha256",
    "config_sha256",
    "config_path_sha256",
    "invoke_log_sha256",
    "uia_transcript_sha256",
)

_FIELD_BUNDLE_MAX_BYTES = 64 * 1024 * 1024
_FIELD_INVOKE_LOG_MAX_BYTES = 1024 * 1024
_FIELD_UIA_TRANSCRIPT_MAX_BYTES = 2 * 1024 * 1024
_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x400


def _strict_json_loads(payload: bytes | str, *, label: str) -> object:
    """Decode duplicate-free UTF-8 JSON.

    Evidence is a security boundary, so Python's default last-key-wins parsing
    is not acceptable: two reviewers could otherwise validate different
    meanings for the same signed byte stream.
    """

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    return json.loads(text, object_pairs_hook=unique_object)


def _is_reparse_or_link(path: Path) -> bool:
    """Return true for symlinks and Windows junction/reparse entries."""

    try:
        metadata = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT_ATTRIBUTE
    )

_WINDOWS_NATIVE_SOURCE_CONTRACT = "skill-magnet-native-source-v1"
_WINDOWS_NATIVE_SOURCE_INPUTS = (
    "AppxManifest.xml",
    "ContractTest.cpp",
    "RecoveryMessages.h",
    "SkillMagnetCommand.cpp",
    "SkillMagnetCommand.def",
    "SkillMagnetIdentity.cpp",
    "SkillMagnetMenu.tsv",
    "build-package.ps1",
    "build.ps1",
    "certificate-state.ps1",
    "contract_test.py",
    "package.ps1",
)


def _native_source_manifest_from_repository(repository: Path) -> dict[str, object]:
    """Recompute the native source identity without trusting installed code."""

    native_root = repository / "native" / "windows-modern-context-menu"
    combined = hashlib.sha256()
    inputs: list[dict[str, object]] = []
    for relative in _WINDOWS_NATIVE_SOURCE_INPUTS:
        raw = (native_root / relative).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        normalized = raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        combined.update(relative.encode("utf-8"))
        combined.update(b"\0")
        combined.update(normalized)
        combined.update(b"\0")
        inputs.append(
            {
                "path": relative,
                "normalized_size": len(normalized),
                "sha256": hashlib.sha256(normalized).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "contract": _WINDOWS_NATIVE_SOURCE_CONTRACT,
        "source_tree_sha256": combined.hexdigest(),
        "inputs": inputs,
    }


def _parse_utc(value: object, *, milliseconds_only: bool = False) -> dt.datetime | None:
    pattern = (
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
        if milliseconds_only
        else r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3,6})?Z"
    )
    if not re.fullmatch(pattern, str(value)):
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _native_sequence_bytes(records: list[dict[str, object]]) -> bytes:
    return (("\r\n".join(str(record["_line"]) for record in records)) + "\r\n").encode(
        "utf-16-le"
    )


def _parse_field_evidence(
    ledger: dict[str, object], invoke_log: Path
) -> tuple[
    list[str], dict[str, dict[str, object]], dict[str, dict[str, object]]
]:
    errors: list[str] = []
    if not invoke_log.is_file():
        return [f"Windows Explorer field evidence is missing: {invoke_log}"], {}, {}
    if _is_reparse_or_link(invoke_log):
        return ["Windows Explorer field evidence must not be a link or reparse point"], {}, {}
    payload = invoke_log.read_bytes()
    if not payload or len(payload) > _FIELD_INVOKE_LOG_MAX_BYTES:
        return ["Windows Explorer field evidence size is outside the accepted range"], {}, {}
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != ledger.get("windows_explorer_field_invoke_log_sha256"):
        errors.append(
            "windows_explorer_field_invoke_log_sha256 mismatch: "
            f"ledger={ledger.get('windows_explorer_field_invoke_log_sha256')!r}, "
            f"observed={actual_hash!r}"
        )
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        errors.append("field evidence must be the BOM-free native UTF-16LE record slice")
    if len(payload) % 2:
        return errors + ["Windows Explorer field evidence is not valid UTF-16LE: odd byte count"], {}, {}
    try:
        text = payload.decode("utf-16-le")
    except UnicodeError as error:
        return errors + [f"Windows Explorer field evidence is not valid UTF-16LE: {error}"], {}, {}
    if not text.endswith("\r\n") or re.search(r"(?<!\r)\n|\r(?!\n)", text):
        errors.append("field evidence must preserve native CRLF record boundaries")
    lines = text[:-2].split("\r\n") if text.endswith("\r\n") else text.splitlines()
    native_record_count = len(_NATIVE_SEQUENCE_ROLES) * len(_NATIVE_EVENTS)
    identity_roles = (
        "selected_item",
        "background_site",
        "missing_skill_registration",
        "runtime_skill_projectless",
    )
    expected_record_count = native_record_count + len(identity_roles)
    if len(lines) != expected_record_count:
        errors.append(
            f"field evidence requires exactly {expected_record_count} native/identity records; "
            f"observed {len(lines)}"
        )
    record_pattern = re.compile(
        r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)"
        r"\tevent=(?P<event>[a-z_]+)"
        r"\tcommand_sha256=(?P<command>[0-9a-f]{64})"
        r"\tdetail=(?P<detail>0|[1-9]\d*)"
        r"\tselection_source=(?P<source>selected_item|background_site)"
        r"\tproject_sha256=(?P<project>[0-9a-f]{64}|unavailable)"
        r"\tinvocation_id=(?P<invocation>[0-9a-f]{32})"
    )
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(lines[:native_record_count], 1):
        match = record_pattern.fullmatch(line)
        if match is None:
            errors.append(f"field evidence line {line_number} is not a native record")
            continue
        record: dict[str, object] = match.groupdict()
        timestamp = _parse_utc(record["timestamp"], milliseconds_only=True)
        if timestamp is None:
            errors.append(f"field evidence line {line_number} has an invalid UTC timestamp")
        record["_timestamp"] = timestamp
        record["_line"] = line
        records.append(record)
    if len(records) != native_record_count:
        return errors, {}, {}
    timestamps = [record["_timestamp"] for record in records]
    if any(
        earlier is None or later is None or earlier > later
        for earlier, later in zip(timestamps, timestamps[1:])
    ):
        errors.append("field evidence native timestamps are not monotonic")

    sequences: dict[str, dict[str, object]] = {}
    for sequence_index, (role, source, terminal_events) in enumerate(
        _NATIVE_SEQUENCE_ROLES
    ):
        start = sequence_index * len(_NATIVE_EVENTS)
        group = records[start : start + len(_NATIVE_EVENTS)]
        if [record["source"] for record in group] != [source] * len(_NATIVE_EVENTS):
            errors.append(f"field evidence {role} records are missing or not contiguous")
            continue
        events = [str(record["event"]) for record in group]
        if events[:3] != list(_NATIVE_EVENTS[:3]) or events[3] not in terminal_events:
            errors.append(f"field evidence {role} event sequence is not exact")
            continue
        invocation_ids = {str(record["invocation"]) for record in group}
        template_command = str(group[0]["command"])
        launch_command = str(group[2]["command"])
        project = str(group[1]["project"])
        valid = True
        if len(invocation_ids) != 1:
            errors.append(f"field evidence {role} records do not share one invocation_id")
            valid = False
        if [str(record["detail"]) for record in group[:2]] != ["0", "0"]:
            errors.append(f"field evidence {role} pre-launch detail values are not zero")
            valid = False
        if not (
            group[0]["project"] == "unavailable"
            and all(str(record["project"]) == project for record in group[1:])
            and re.fullmatch(r"[0-9a-f]{64}", project)
        ):
            errors.append(f"field evidence {role} project digest transition is invalid")
            valid = False
        if not (
            group[1]["command"] == template_command
            and group[3]["command"] == launch_command
            and template_command != launch_command
        ):
            errors.append(f"field evidence {role} command digest transition is invalid")
            valid = False
        process_id = int(str(group[2]["detail"]))
        terminal_detail_valid = (
            group[3]["detail"] == group[2]["detail"]
            if events[3] == "child_running"
            else group[3]["detail"] == "0"
        )
        if process_id <= 0 or not terminal_detail_valid:
            errors.append(f"field evidence {role} process id details are invalid")
            valid = False
        if valid:
            sequences[role] = {
                "invocation": next(iter(invocation_ids)),
                "project": project,
                "template_command": template_command,
                "launch_command": launch_command,
                "process_id": process_id,
                "terminal_event": events[3],
                "records": group,
                "sequence_sha256": hashlib.sha256(_native_sequence_bytes(group)).hexdigest(),
            }
    if len({sequence.get("invocation") for sequence in sequences.values()}) != len(
        _NATIVE_SEQUENCE_ROLES
    ):
        errors.append(
            f"field evidence requires {len(_NATIVE_SEQUENCE_ROLES)} distinct invocation ids"
        )
    identity_pattern = re.compile(
        r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)"
        r"\tevent=ui_identity_bound"
        r"\tnative_role=(?P<role>selected_item|background_site|missing_skill_registration|runtime_skill_projectless)"
        r"\tproject_sha256=(?P<project>[0-9a-f]{64})"
        r"\ttarget_sha256=(?P<target>[0-9a-f]{64})"
        r"\tregistration_source_sha256=(?P<registration_source>[0-9a-f]{64}|unavailable)"
        r"\tinvocation_id=(?P<invocation>[0-9a-f]{32})"
    )
    identity_anchors: dict[str, dict[str, object]] = {}
    last_observed_time = timestamps[-1] if timestamps else None
    anchor_lines = lines[native_record_count:]
    for line_number, (expected_role, line) in enumerate(
        zip(identity_roles, anchor_lines), native_record_count + 1
    ):
        match = identity_pattern.fullmatch(line)
        if match is None:
            errors.append(f"field evidence line {line_number} is not a UI identity anchor")
            continue
        anchor: dict[str, object] = match.groupdict()
        anchor_time = _parse_utc(anchor["timestamp"], milliseconds_only=True)
        native = sequences.get(expected_role)
        valid = True
        if anchor.get("role") != expected_role:
            errors.append(f"field evidence UI identity anchor {expected_role} is out of order")
            valid = False
        if (
            native is None
            or anchor.get("project") != native.get("project")
            or anchor.get("invocation") != native.get("invocation")
        ):
            errors.append(
                f"field evidence UI identity anchor {expected_role} does not bind its native sequence"
            )
            valid = False
        if expected_role == "missing_skill_registration":
            if not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get("registration_source", ""))):
                errors.append("field evidence registration source identity anchor is invalid")
                valid = False
        elif anchor.get("registration_source") != "unavailable":
            errors.append(
                f"field evidence UI identity anchor {expected_role} has an unexpected registration source"
            )
            valid = False
        if (
            not isinstance(anchor_time, dt.datetime)
            or not isinstance(last_observed_time, dt.datetime)
            or anchor_time < last_observed_time
        ):
            errors.append(f"field evidence UI identity anchor {expected_role} is not contemporaneous")
            valid = False
        if valid:
            anchor["_timestamp"] = anchor_time
            identity_anchors[expected_role] = anchor
            last_observed_time = anchor_time
    if len(anchor_lines) != len(identity_roles) or set(identity_anchors) != set(identity_roles):
        errors.append("field evidence requires four exact UI identity anchors")
    selected = sequences.get("selected_item", {})
    manager_same = sequences.get("manager_same_folder", {})
    manager_different = sequences.get("manager_different_folder", {})
    background = sequences.get("background_site", {})
    repeated = sequences.get("same_folder_repeat", {})
    different = sequences.get("different_folder_busy", {})
    relaunched = sequences.get("closed_window_relaunch", {})
    registration = sequences.get("missing_skill_registration", {})
    runtime_skill = sequences.get("runtime_skill_projectless", {})
    if not (
        selected.get("project")
        and background.get("project")
        and manager_different.get("project")
        and different.get("project")
        and runtime_skill.get("project")
        and len(
            {
                selected.get("project"),
                background.get("project"),
                manager_different.get("project"),
                different.get("project"),
                runtime_skill.get("project"),
            }
        )
        == 5
        and manager_same.get("project") == selected.get("project")
        and registration.get("project") == selected.get("project")
        and repeated.get("project") == background.get("project")
        and relaunched.get("project") == background.get("project")
    ):
        errors.append("field evidence recovery project lineage is invalid")
    template_digests = {sequence.get("template_command") for sequence in sequences.values()}
    if len(template_digests) != 1:
        errors.append("field evidence invocations do not share the installed template command")
    if not (
        selected.get("launch_command")
        and background.get("launch_command")
        and manager_different.get("launch_command")
        and different.get("launch_command")
        and runtime_skill.get("launch_command")
        and len(
            {
                selected.get("launch_command"),
                background.get("launch_command"),
                manager_different.get("launch_command"),
                different.get("launch_command"),
                runtime_skill.get("launch_command"),
            }
        )
        == 5
        and manager_same.get("launch_command") == selected.get("launch_command")
        and registration.get("launch_command") == selected.get("launch_command")
        and repeated.get("launch_command") == background.get("launch_command")
        and relaunched.get("launch_command") == background.get("launch_command")
    ):
        errors.append("field evidence recovery launch-command lineage is invalid")
    if re.search(r"(?:[A-Za-z]:\\|/Users/|/home/)", text):
        errors.append("field evidence contains a plaintext local path")
    return errors, sequences, identity_anchors


def validate_field_evidence(ledger: dict[str, object], invoke_log: Path) -> list[str]:
    return _parse_field_evidence(ledger, invoke_log)[0]


def _decode_embedded_bytes(
    value: object, *, label: str, errors: list[str]
) -> bytes | None:
    if not isinstance(value, str):
        errors.append(f"field bundle {label} bytes_base64 must be a string")
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        errors.append(f"field bundle {label} bytes_base64 is not canonical base64")
        return None
    if base64.b64encode(decoded).decode("ascii") != value:
        errors.append(f"field bundle {label} bytes_base64 is not canonical base64")
        return None
    return decoded


def _validate_pe_x64_dll(payload: bytes) -> list[str]:
    errors: list[str] = []
    if len(payload) < 4096 or payload[:2] != b"MZ":
        return ["field bundle command DLL bytes are not a substantive PE image"]
    try:
        pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
        if pe_offset < 0x40 or pe_offset + 24 > len(payload):
            raise ValueError
        if payload[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise ValueError
        machine, sections, _, _, _, optional_size, characteristics = struct.unpack_from(
            "<HHIIIHH", payload, pe_offset + 4
        )
        optional_magic = struct.unpack_from("<H", payload, pe_offset + 24)[0]
    except (struct.error, ValueError):
        return ["field bundle command DLL bytes have an invalid PE header"]
    if machine != 0x8664 or not (1 <= sections <= 96) or optional_magic != 0x20B:
        errors.append("field bundle command DLL is not an x64 PE32+ image")
    if optional_size < 0x70 or not characteristics & 0x2000:
        errors.append("field bundle command DLL PE characteristics are invalid")
    return errors


def _windows_command_line_to_argv(command: str) -> list[str]:
    """Parse the CommandLineToArgvW quoting subset emitted by list2cmdline."""
    arguments: list[str] = []
    index = 0
    while index < len(command):
        while index < len(command) and command[index] in " \t":
            index += 1
        if index == len(command):
            break
        argument: list[str] = []
        quoted = False
        while index < len(command):
            if command[index] in " \t" and not quoted:
                break
            if command[index] == "\\":
                start = index
                while index < len(command) and command[index] == "\\":
                    index += 1
                slash_count = index - start
                if index < len(command) and command[index] == '"':
                    argument.extend("\\" * (slash_count // 2))
                    if slash_count % 2:
                        argument.append('"')
                        index += 1
                    else:
                        quoted = not quoted
                        index += 1
                else:
                    argument.extend("\\" * slash_count)
                continue
            if command[index] == '"':
                quoted = not quoted
                index += 1
                continue
            argument.append(command[index])
            index += 1
        if quoted:
            return []
        arguments.append("".join(argument))
    return arguments


def _validate_uia_element(
    value: object, *, expected_name: str, expected_control_type: str, label: str
) -> list[str]:
    errors: list[str] = []
    keys = {
        "name",
        "control_type",
        "automation_id",
        "class_name",
        "framework_id",
        "process_id",
        "native_window_handle",
        "is_enabled",
        "is_offscreen",
        "bounding_rectangle",
        "runtime_id",
    }
    if not isinstance(value, dict) or set(value) != keys:
        return [f"field bundle {label} UIAutomation element keys do not match the contract"]
    if value.get("name") != expected_name or value.get("control_type") != expected_control_type:
        errors.append(f"field bundle {label} UIAutomation identity mismatch")
    if value.get("is_enabled") is not True or value.get("is_offscreen") is not False:
        errors.append(f"field bundle {label} UIAutomation visibility/state mismatch")
    if not isinstance(value.get("process_id"), int) or int(value["process_id"]) <= 0:
        errors.append(f"field bundle {label} UIAutomation process_id is invalid")
    if not isinstance(value.get("native_window_handle"), int):
        errors.append(f"field bundle {label} UIAutomation native_window_handle is invalid")
    if not all(isinstance(value.get(key), str) for key in ("automation_id", "class_name", "framework_id")):
        errors.append(f"field bundle {label} UIAutomation string properties are invalid")
    rectangle = value.get("bounding_rectangle")
    if not isinstance(rectangle, dict) or set(rectangle) != {"left", "top", "width", "height"}:
        errors.append(f"field bundle {label} UIAutomation bounding rectangle is invalid")
    elif not all(
        isinstance(rectangle.get(key), (int, float)) for key in ("left", "top", "width", "height")
    ) or rectangle["width"] <= 0 or rectangle["height"] <= 0:
        errors.append(f"field bundle {label} UIAutomation bounding rectangle is empty")
    runtime_id = value.get("runtime_id")
    if not isinstance(runtime_id, list) or len(runtime_id) < 2 or not all(
        isinstance(part, int) for part in runtime_id
    ):
        errors.append(f"field bundle {label} UIAutomation runtime_id is invalid")
    return errors


def _configured_selector_choices(config_payload: bytes) -> list[dict[str, object]]:
    """Derive the public label-to-internal-ID map directly from release config bytes."""

    value = _strict_json_loads(config_payload, label="release config")
    packs = value.get("packs") if isinstance(value, dict) else None
    if not isinstance(packs, list):
        raise ValueError("config packs are not a list")
    candidates: list[tuple[str, str, str | None]] = []
    numeric_space = re.compile(r"&#(?:0*32|[xX]0*20);")
    for pack in packs:
        if not isinstance(pack, dict):
            raise ValueError("config pack is not an object")
        pack_id = pack.get("id")
        if not isinstance(pack_id, str) or not pack_id:
            raise ValueError("config pack has no internal id")
        if pack.get("selection_kind", "package") == "package":
            menu_label = pack.get("menu_label")
            if not isinstance(menu_label, str) or not menu_label:
                raise ValueError("package selection has no menu label")
            candidates.append(
                (f"Skill Pack: {numeric_space.sub(' ', menu_label)}", pack_id, None)
            )
            continue
        skills = pack.get("skills")
        metadata = pack.get("skill_metadata", {})
        if not isinstance(skills, list) or not isinstance(metadata, dict):
            raise ValueError("skill selection metadata is invalid")
        for skill_id in skills:
            if not isinstance(skill_id, str) or not skill_id:
                raise ValueError("skill selection has no internal id")
            item = metadata.get(skill_id, {})
            display = item.get("display_name", skill_id) if isinstance(item, dict) else skill_id
            if not isinstance(display, str) or not display:
                raise ValueError("skill selection has no display name")
            candidates.append(
                (f"Skill: {numeric_space.sub(' ', display)}", pack_id, skill_id)
            )
    counts: dict[str, int] = {}
    for label, _, _ in candidates:
        counts[label] = counts.get(label, 0) + 1
    ordinals: dict[str, int] = {}
    reserved = set(counts)
    choices: list[dict[str, object]] = []
    used: set[str] = set()
    for base, pack_id, skill_id in candidates:
        ordinals[base] = ordinals.get(base, 0) + 1
        if counts[base] == 1:
            label = base
        else:
            label = f"{base} （同名 {ordinals[base]}）"
            collision = 1
            while label in reserved or label in used:
                label = f"{base} （同名 {ordinals[base]}・候補 {collision}）"
                collision += 1
        used.add(label)
        choices.append({"label": label, "pack_id": pack_id, "skill_id": skill_id})
    return choices


def _selector_choice_map_sha256(choices: list[dict[str, object]]) -> str:
    payload = json.dumps(
        choices, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ordered_selector_label_sha256(choices: list[dict[str, object]]) -> str:
    labels = [str(choice.get("label")) for choice in choices]
    payload = json.dumps(
        labels, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _configured_repository_url(config_payload: bytes) -> str:
    """Independently derive the one unambiguous Library Manager remote."""

    value = _strict_json_loads(config_payload, label="release config")
    packs = value.get("packs") if isinstance(value, dict) else None
    if not isinstance(packs, list) or not all(isinstance(pack, dict) for pack in packs):
        raise ValueError("config packs are not a list of objects")
    remotes = {
        str(pack.get("repo_url", "")).strip()
        for pack in packs
        if str(pack.get("repo_url", "")).strip()
    }
    if len(remotes) != 1:
        raise ValueError("config does not identify exactly one repository URL")
    return next(iter(remotes))


def _validate_uia_transcript(
    transcript: object,
    hashes: dict[str, object],
    sequences: dict[str, dict[str, object]],
    expected_choices: list[dict[str, object]],
    configured_remote: str,
) -> tuple[
    list[str],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    str | None,
]:
    errors: list[str] = []
    expected_keys = {"encoding", "line_count", "sha256", "bytes_base64"}
    if not isinstance(transcript, dict) or set(transcript) != expected_keys:
        return ["field bundle UIAutomation transcript keys do not match the contract"], {}, {}, None
    if transcript.get("encoding") != "utf-8-jsonl":
        errors.append("field bundle UIAutomation transcript encoding mismatch")
    payload = _decode_embedded_bytes(transcript.get("bytes_base64"), label="UIAutomation transcript", errors=errors)
    if payload is None:
        return errors, {}, {}, None
    if not payload or len(payload) > _FIELD_UIA_TRANSCRIPT_MAX_BYTES:
        return errors + ["field bundle UIAutomation transcript size is outside the accepted range"], {}, {}, None
    digest = hashlib.sha256(payload).hexdigest()
    if transcript.get("sha256") != digest or hashes.get("uia_transcript_sha256") != digest:
        errors.append("field bundle UIAutomation transcript hash mismatch")
    if not payload.endswith(b"\n") or b"\r" in payload:
        errors.append("field bundle UIAutomation transcript must be LF-terminated UTF-8 JSONL")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeError as error:
        return errors + [f"field bundle UIAutomation transcript is not UTF-8: {error}"], {}, {}, None
    sensitive_values = [configured_remote] + [
        str(choice.get("label")) for choice in expected_choices
    ]
    if any(value and value in decoded for value in sensitive_values):
        errors.append(
            "field bundle UIAutomation transcript contains raw repository or selector labels"
        )
    if re.search(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|var/tmp)/)", decoded):
        errors.append("field bundle UIAutomation transcript contains a plaintext local path")
    raw_lines = decoded[:-1].split("\n") if decoded.endswith("\n") else decoded.splitlines()
    expected_line_count = (
        len(_FIELD_SOURCES) * len(_TRANSCRIPT_EVENTS)
        + len(_RECOVERY_TRANSCRIPT_EVENTS)
        + len(_WORKFLOW_TRANSCRIPT_EVENTS)
    )
    if transcript.get("line_count") != expected_line_count or len(raw_lines) != expected_line_count:
        errors.append(
            f"field bundle UIAutomation transcript requires exactly {expected_line_count} events"
        )
    entries: list[dict[str, object]] = []
    for line_number, line in enumerate(raw_lines, 1):
        try:
            entry = _strict_json_loads(
                line, label=f"UIAutomation transcript line {line_number}"
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(
                f"field bundle UIAutomation transcript line {line_number} "
                f"is not strict JSON: {error}"
            )
            continue
        if not isinstance(entry, dict) or set(entry) != {
            "sequence",
            "observed_at_utc",
            "session_id",
            "event",
            "source",
            "data",
        }:
            errors.append(f"field bundle UIAutomation transcript line {line_number} keys mismatch")
            continue
        entries.append(entry)
    if len(entries) != expected_line_count:
        errors.append("field bundle recovery UIAutomation events are missing or out of order")
        return errors, {}, {}, None
    if [entry.get("sequence") for entry in entries] != list(range(1, expected_line_count + 1)):
        errors.append("field bundle UIAutomation transcript sequence numbers are not exact")
    session_ids = {str(entry.get("session_id")) for entry in entries}
    session_id = next(iter(session_ids)) if len(session_ids) == 1 else None
    if session_id is None or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        errors.append("field bundle UIAutomation transcript session_id is invalid")
    transcript_times = [_parse_utc(entry.get("observed_at_utc")) for entry in entries]
    if any(timestamp is None for timestamp in transcript_times) or any(
        earlier > later
        for earlier, later in zip(transcript_times, transcript_times[1:])
        if earlier is not None and later is not None
    ):
        errors.append("field bundle UIAutomation transcript timestamps are invalid or not monotonic")

    derived: dict[str, dict[str, object]] = {}
    primary_gui_elements: dict[str, dict[str, object]] = {}
    primary_starts = {"selected_item": 0, "background_site": 5}
    expected_labels = [str(choice.get("label")) for choice in expected_choices]
    expected_label_digest = _ordered_selector_label_sha256(expected_choices)
    expected_selected_digest = _text_sha256(expected_labels[0]) if expected_labels else ""
    for source in _FIELD_SOURCES:
        start = primary_starts[source]
        group = entries[start : start + len(_TRANSCRIPT_EVENTS)]
        if [entry.get("source") for entry in group] != [source] * len(_TRANSCRIPT_EVENTS):
            errors.append(f"field bundle UIAutomation {source} events are missing or not contiguous")
            continue
        if [entry.get("event") for entry in group] != list(_TRANSCRIPT_EVENTS):
            errors.append(f"field bundle UIAutomation {source} event sequence is not exact")
            continue
        root_data, dispatch_data, gui_data, bound_data = [entry.get("data") for entry in group]
        if not isinstance(root_data, dict) or set(root_data) != {
            "element",
            "root_visible_count",
            "invoke_pattern_available",
            "expand_collapse_pattern_available",
            "submenu_item_count",
        }:
            errors.append(f"field bundle UIAutomation {source} root event data mismatch")
            continue
        errors.extend(
            _validate_uia_element(
                root_data.get("element"),
                expected_name="Skill Magnet",
                expected_control_type="ControlType.MenuItem",
                label=f"{source} root",
            )
        )
        root_element = root_data.get("element") if isinstance(root_data.get("element"), dict) else {}
        if {key: root_data.get(key) for key in root_data if key != "element"} != {
            "root_visible_count": 1,
            "invoke_pattern_available": True,
            "expand_collapse_pattern_available": False,
            "submenu_item_count": 0,
        }:
            errors.append(f"field bundle UIAutomation {source} root claims mismatch")
        if not isinstance(dispatch_data, dict) or set(dispatch_data) != {"runtime_id"} or (
            dispatch_data.get("runtime_id") != root_element.get("runtime_id")
        ):
            errors.append(f"field bundle UIAutomation {source} invoke dispatch is not bound to the root")
        expected_gui_keys = {
            "element",
            "project_sha256",
            "gui_visible",
            "gui_title",
            "project_binding_visible",
            "selection_choice_count",
            "selection_choice_values_sha256",
            "selected_choice_value_sha256",
            "selection_combo_exact_match_count",
            "library_manager_button_count",
            "library_manager_button_text_sha256",
            "register_button_count",
            "register_button_text_sha256",
        }
        if not isinstance(gui_data, dict) or set(gui_data) != expected_gui_keys:
            errors.append(f"field bundle UIAutomation {source} GUI event data mismatch")
            continue
        errors.extend(
            _validate_uia_element(
                gui_data.get("element"),
                expected_name="Skill Magnet — 実行確認",
                expected_control_type="ControlType.Window",
                label=f"{source} GUI",
            )
        )
        gui_element = gui_data.get("element") if isinstance(gui_data.get("element"), dict) else {}
        primary_gui_elements[source] = gui_element
        gui_claims = {key: gui_data.get(key) for key in gui_data if key not in {"element", "project_sha256"}}
        if gui_claims != {
            "gui_visible": True,
            "gui_title": "Skill Magnet — 実行確認",
            "project_binding_visible": True,
            "selection_choice_count": len(expected_choices),
            "selection_choice_values_sha256": expected_label_digest,
            "selected_choice_value_sha256": expected_selected_digest,
            "selection_combo_exact_match_count": 1,
            "library_manager_button_count": 1,
            "library_manager_button_text_sha256": _text_sha256("Library Manager"),
            "register_button_count": 1,
            "register_button_text_sha256": _text_sha256(
                "このフォルダーのスキルを登録"
            ),
        }:
            errors.append(f"field bundle UIAutomation {source} GUI claims mismatch")
        if not isinstance(bound_data, dict) or set(bound_data) != {
            "invocation_id",
            "project_sha256",
            "native_sequence_sha256",
        }:
            errors.append(f"field bundle UIAutomation {source} native binding data mismatch")
            continue
        native = sequences.get(source)
        if native is None or bound_data != {
            "invocation_id": native.get("invocation") if native else None,
            "project_sha256": native.get("project") if native else None,
            "native_sequence_sha256": native.get("sequence_sha256") if native else None,
        }:
            errors.append(f"field bundle UIAutomation {source} does not bind the exact native sequence")
        if native is not None and gui_data.get("project_sha256") != native.get("project"):
            errors.append(f"field bundle UIAutomation {source} GUI project digest mismatch")
        if root_element and gui_element:
            if root_element.get("process_id") == gui_element.get("process_id"):
                errors.append(f"field bundle UIAutomation {source} root and GUI process ids are not distinct")
            if native is not None and gui_element.get("process_id") != native.get("process_id"):
                errors.append(
                    f"field bundle UIAutomation {source} GUI process is not the launched native child"
                )
        native_records = native.get("records") if native else None
        if isinstance(native_records, list) and transcript_times[start] is not None and transcript_times[start + 3] is not None:
            first_native = native_records[0].get("_timestamp")
            last_native = native_records[-1].get("_timestamp")
            if not isinstance(first_native, dt.datetime) or not isinstance(last_native, dt.datetime) or not (
                transcript_times[start] <= first_native <= last_native <= transcript_times[start + 3]
            ):
                errors.append(f"field bundle UIAutomation {source} timestamps do not bracket native invocation")
        derived[source] = {
            "source": source,
            "invocation_id": bound_data.get("invocation_id"),
            "project_sha256": bound_data.get("project_sha256"),
            "root_visible_count": root_data.get("root_visible_count"),
            "invoke_pattern_available": root_data.get("invoke_pattern_available"),
            "expand_collapse_pattern_available": root_data.get("expand_collapse_pattern_available"),
            "submenu_item_count": root_data.get("submenu_item_count"),
            "gui_visible": gui_data.get("gui_visible"),
            "gui_title": gui_data.get("gui_title"),
            "project_binding_visible": gui_data.get("project_binding_visible"),
            "selection_choice_count": gui_data.get("selection_choice_count"),
            "selection_choice_values_sha256": gui_data.get(
                "selection_choice_values_sha256"
            ),
            "selected_choice_value_sha256": gui_data.get(
                "selected_choice_value_sha256"
            ),
            "selection_combo_exact_match_count": gui_data.get(
                "selection_combo_exact_match_count"
            ),
            "library_manager_button_count": gui_data.get("library_manager_button_count"),
            "library_manager_button_text_sha256": gui_data.get(
                "library_manager_button_text_sha256"
            ),
            "register_button_count": gui_data.get("register_button_count"),
            "register_button_text_sha256": gui_data.get(
                "register_button_text_sha256"
            ),
        }

    recovery_start = 9
    recovery_entries = entries[recovery_start : recovery_start + len(_RECOVERY_TRANSCRIPT_EVENTS)]
    if [
        (entry.get("event"), entry.get("source")) for entry in recovery_entries
    ] != list(_RECOVERY_TRANSCRIPT_EVENTS):
        errors.append("field bundle recovery UIAutomation events are missing or out of order")
        return errors, derived, {}, session_id

    def stable_window_identity(element: dict[str, object]) -> tuple[object, object, object]:
        return (
            element.get("process_id"),
            element.get("native_window_handle"),
            element.get("runtime_id"),
        )

    def validate_after_native(
        entry: dict[str, object], native: dict[str, object] | None, label: str
    ) -> None:
        event_time = _parse_utc(entry.get("observed_at_utc"))
        records = native.get("records") if native else None
        native_time = (
            records[-1].get("_timestamp")
            if isinstance(records, list) and records and isinstance(records[-1], dict)
            else None
        )
        if not isinstance(event_time, dt.datetime) or not isinstance(native_time, dt.datetime) or not (
            native_time <= event_time <= native_time + dt.timedelta(seconds=30)
        ):
            errors.append(f"field bundle {label} UIAutomation event is not bound in time to native invocation")

    background_native = sequences.get("background_site")
    background_element = primary_gui_elements.get("background_site", {})

    same_entry = recovery_entries[0]
    same_data = same_entry.get("data")
    same_keys = {
        "element",
        "project_sha256",
        "original_invocation_id",
        "repeat_invocation_id",
        "repeat_native_sequence_sha256",
        "gui_count",
        "foreground_window_handle",
        "unexpected_error_count",
    }
    if not isinstance(same_data, dict) or set(same_data) != same_keys:
        errors.append("field bundle same-folder repeat UIAutomation data mismatch")
    else:
        errors.extend(
            _validate_uia_element(
                same_data.get("element"),
                expected_name="Skill Magnet — 実行確認",
                expected_control_type="ControlType.Window",
                label="same-folder repeat GUI",
            )
        )
        same_element = same_data.get("element") if isinstance(same_data.get("element"), dict) else {}
        repeated_native = sequences.get("same_folder_repeat")
        expected_same = {
            "project_sha256": repeated_native.get("project") if repeated_native else None,
            "original_invocation_id": background_native.get("invocation") if background_native else None,
            "repeat_invocation_id": repeated_native.get("invocation") if repeated_native else None,
            "repeat_native_sequence_sha256": repeated_native.get("sequence_sha256") if repeated_native else None,
            "gui_count": 1,
            "foreground_window_handle": same_element.get("native_window_handle"),
            "unexpected_error_count": 0,
        }
        if {key: same_data.get(key) for key in same_keys if key != "element"} != expected_same:
            errors.append("field bundle same-folder repeat claims do not bind native evidence")
        if stable_window_identity(same_element) != stable_window_identity(background_element):
            errors.append("field bundle same-folder repeat did not preserve the existing GUI identity")
        if repeated_native and same_element.get("process_id") == repeated_native.get("process_id"):
            errors.append("field bundle same-folder repeat GUI belongs to the duplicate launcher process")
        validate_after_native(same_entry, repeated_native, "same-folder repeat")

    busy_entry = recovery_entries[1]
    busy_data = busy_entry.get("data")
    busy_keys = {
        "element",
        "project_sha256",
        "invocation_id",
        "native_sequence_sha256",
        "busy_text_visible",
        "actionable_recovery_visible",
        "ok_button_count",
    }
    if not isinstance(busy_data, dict) or set(busy_data) != busy_keys:
        errors.append("field bundle different-folder busy UIAutomation data mismatch")
    else:
        errors.extend(
            _validate_uia_element(
                busy_data.get("element"),
                expected_name="Skill Magnet エラー",
                expected_control_type="ControlType.Window",
                label="different-folder busy dialog",
            )
        )
        busy_element = busy_data.get("element") if isinstance(busy_data.get("element"), dict) else {}
        different_native = sequences.get("different_folder_busy")
        if {key: busy_data.get(key) for key in busy_keys if key != "element"} != {
            "project_sha256": different_native.get("project") if different_native else None,
            "invocation_id": different_native.get("invocation") if different_native else None,
            "native_sequence_sha256": different_native.get("sequence_sha256") if different_native else None,
            "busy_text_visible": True,
            "actionable_recovery_visible": True,
            "ok_button_count": 1,
        }:
            errors.append("field bundle different-folder busy claims do not bind native evidence")
        if different_native and busy_element.get("process_id") != different_native.get("process_id"):
            errors.append("field bundle different-folder busy dialog belongs to another process")
        validate_after_native(busy_entry, different_native, "different-folder busy")

    relaunch_entry = recovery_entries[2]
    relaunch_data = relaunch_entry.get("data")
    relaunch_keys = {
        "element",
        "project_sha256",
        "original_invocation_id",
        "relaunch_invocation_id",
        "relaunch_native_sequence_sha256",
        "original_process_id",
        "original_native_window_handle",
        "original_runtime_id",
    }
    if not isinstance(relaunch_data, dict) or set(relaunch_data) != relaunch_keys:
        errors.append("field bundle closed-window relaunch UIAutomation data mismatch")
    else:
        errors.extend(
            _validate_uia_element(
                relaunch_data.get("element"),
                expected_name="Skill Magnet — 実行確認",
                expected_control_type="ControlType.Window",
                label="closed-window relaunch GUI",
            )
        )
        relaunch_element = (
            relaunch_data.get("element")
            if isinstance(relaunch_data.get("element"), dict)
            else {}
        )
        relaunch_native = sequences.get("closed_window_relaunch")
        if {key: relaunch_data.get(key) for key in relaunch_keys if key != "element"} != {
            "project_sha256": relaunch_native.get("project") if relaunch_native else None,
            "original_invocation_id": background_native.get("invocation") if background_native else None,
            "relaunch_invocation_id": relaunch_native.get("invocation") if relaunch_native else None,
            "relaunch_native_sequence_sha256": relaunch_native.get("sequence_sha256") if relaunch_native else None,
            "original_process_id": background_element.get("process_id"),
            "original_native_window_handle": background_element.get("native_window_handle"),
            "original_runtime_id": background_element.get("runtime_id"),
        }:
            errors.append("field bundle closed-window relaunch claims do not bind native evidence")
        if relaunch_native and relaunch_element.get("process_id") != relaunch_native.get("process_id"):
            errors.append("field bundle closed-window relaunch GUI belongs to another process")
        if stable_window_identity(relaunch_element) == stable_window_identity(background_element):
            errors.append("field bundle closed-window relaunch reused the closed GUI identity")
        validate_after_native(relaunch_entry, relaunch_native, "closed-window relaunch")

    workflow_entries = {
        "library_manager_flow": entries[4],
        "missing_skill_registration": entries[12],
        "runtime_skill_projectless": entries[13],
    }
    expected_workflow_identity = {
        "library_manager_flow": _WORKFLOW_TRANSCRIPT_EVENTS[0],
        "missing_skill_registration": _WORKFLOW_TRANSCRIPT_EVENTS[1],
        "runtime_skill_projectless": _WORKFLOW_TRANSCRIPT_EVENTS[2],
    }
    for role, entry in workflow_entries.items():
        if (entry.get("event"), entry.get("source")) != expected_workflow_identity[role]:
            errors.append(f"field bundle {role} UIAutomation event is missing or out of order")

    snapshot_keys = {"config_sha256", "library_sha256", "transactions_sha256"}

    def validate_unchanged_snapshots(data: dict[str, object], label: str) -> None:
        before, after = data.get("state_before"), data.get("state_after")
        if (
            not isinstance(before, dict)
            or not isinstance(after, dict)
            or set(before) != snapshot_keys
            or set(after) != snapshot_keys
            or before != after
            or data.get("no_persistent_mutation") is not True
        ):
            errors.append(f"field bundle {label} persistent-state snapshot changed")
            return
        if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in before.values()):
            errors.append(f"field bundle {label} persistent-state snapshot digest is invalid")

    def validate_workflow_after_native(
        entry: dict[str, object], native: dict[str, object] | None, label: str
    ) -> None:
        event_time = _parse_utc(entry.get("observed_at_utc"))
        records = native.get("records") if native else None
        native_time = (
            records[-1].get("_timestamp")
            if isinstance(records, list) and records and isinstance(records[-1], dict)
            else None
        )
        if not isinstance(event_time, dt.datetime) or not isinstance(native_time, dt.datetime) or not (
            native_time <= event_time <= native_time + dt.timedelta(minutes=3)
        ):
            errors.append(f"field bundle {label} event is not contemporaneous with native evidence")

    workflows: dict[str, dict[str, object]] = {}
    manager_entry = workflow_entries["library_manager_flow"]
    manager_data = manager_entry.get("data")
    manager_keys = {
        "element",
        "configured_remote_sha256",
        "configured_remote_visible",
        "create_button_count",
        "create_button_text_sha256",
        "update_button_count",
        "update_button_text_sha256",
        "delete_button_count",
        "delete_button_text_sha256",
        "reload_button_count",
        "reload_button_text_sha256",
        "same_folder_repeat_invocation_id",
        "same_folder_repeat_project_sha256",
        "same_folder_repeat_native_sequence_sha256",
        "same_folder_repeat_focused_existing_manager",
        "same_folder_repeat_manager_count",
        "same_folder_repeat_error_count",
        "different_folder_invocation_id",
        "different_folder_project_sha256",
        "different_folder_native_sequence_sha256",
        "different_folder_busy_element",
        "different_folder_busy_text_visible",
        "different_folder_actionable_recovery_visible",
        "different_folder_ok_button_count",
        "state_before",
        "state_after",
        "no_persistent_mutation",
    }
    if not isinstance(manager_data, dict) or set(manager_data) != manager_keys:
        errors.append("field bundle Library Manager workflow data mismatch")
    else:
        manager_element = manager_data.get("element")
        manager_name = (
            str(manager_element.get("name")) if isinstance(manager_element, dict) else ""
        )
        errors.extend(
            _validate_uia_element(
                manager_element,
                expected_name=manager_name,
                expected_control_type="ControlType.Window",
                label="Library Manager window",
            )
        )
        if not manager_name.startswith("Library Manager"):
            errors.append("field bundle Library Manager window title mismatch")
        selected_native = sequences.get("selected_item")
        same_native = sequences.get("manager_same_folder")
        manager_different_native = sequences.get("manager_different_folder")
        manager_process = manager_element.get("process_id") if isinstance(manager_element, dict) else None
        expected_manager_claims = {
            "configured_remote_sha256": _text_sha256(configured_remote),
            "configured_remote_visible": True,
            "create_button_count": 1,
            "create_button_text_sha256": _text_sha256("新規登録"),
            "update_button_count": 1,
            "update_button_text_sha256": _text_sha256("選択項目を更新"),
            "delete_button_count": 1,
            "delete_button_text_sha256": _text_sha256("選択項目を削除"),
            "reload_button_count": 1,
            "reload_button_text_sha256": _text_sha256("再読込"),
            "same_folder_repeat_invocation_id": same_native.get("invocation") if same_native else None,
            "same_folder_repeat_project_sha256": same_native.get("project") if same_native else None,
            "same_folder_repeat_native_sequence_sha256": same_native.get("sequence_sha256") if same_native else None,
            "same_folder_repeat_focused_existing_manager": True,
            "same_folder_repeat_manager_count": 1,
            "same_folder_repeat_error_count": 0,
            "different_folder_invocation_id": (
                manager_different_native.get("invocation") if manager_different_native else None
            ),
            "different_folder_project_sha256": (
                manager_different_native.get("project") if manager_different_native else None
            ),
            "different_folder_native_sequence_sha256": (
                manager_different_native.get("sequence_sha256")
                if manager_different_native
                else None
            ),
            "different_folder_busy_text_visible": True,
            "different_folder_actionable_recovery_visible": True,
            "different_folder_ok_button_count": 1,
            "no_persistent_mutation": True,
        }
        manager_claims = {
            key: manager_data.get(key)
            for key in expected_manager_claims
        }
        if manager_claims != expected_manager_claims:
            errors.append("field bundle Library Manager claims do not bind config/native evidence")
        if selected_native and manager_process != selected_native.get("process_id"):
            errors.append("field bundle Library Manager belongs to another process")
        busy_element = manager_data.get("different_folder_busy_element")
        errors.extend(
            _validate_uia_element(
                busy_element,
                expected_name="Skill Magnet エラー",
                expected_control_type="ControlType.Window",
                label="Manager-open different-folder busy dialog",
            )
        )
        if (
            manager_different_native
            and isinstance(busy_element, dict)
            and busy_element.get("process_id") != manager_different_native.get("process_id")
        ):
            errors.append("field bundle Manager-open busy dialog belongs to another process")
        validate_unchanged_snapshots(manager_data, "Library Manager close")
        validate_workflow_after_native(manager_entry, same_native, "Manager same-folder repeat")
        validate_workflow_after_native(
            manager_entry, manager_different_native, "Manager different-folder busy"
        )
        workflows["library_manager_observation"] = {
            key: manager_claims[key]
            for key in (
                "configured_remote_sha256",
                "configured_remote_visible",
                "create_button_count",
                "create_button_text_sha256",
                "update_button_count",
                "update_button_text_sha256",
                "delete_button_count",
                "delete_button_text_sha256",
                "reload_button_count",
                "reload_button_text_sha256",
                "same_folder_repeat_focused_existing_manager",
                "same_folder_repeat_manager_count",
                "same_folder_repeat_error_count",
                "different_folder_busy_text_visible",
                "different_folder_actionable_recovery_visible",
                "different_folder_ok_button_count",
                "no_persistent_mutation",
            )
        }

    registration_entry = workflow_entries["missing_skill_registration"]
    registration_data = registration_entry.get("data")
    registration_keys = {
        "root_element",
        "root_visible_count",
        "invoke_pattern_available",
        "expand_collapse_pattern_available",
        "submenu_item_count",
        "unified_element",
        "manager_element",
        "error_element",
        "invocation_id",
        "project_sha256",
        "native_sequence_sha256",
        "selected_path_sha256",
        "registration_source_sha256",
        "selected_path_visible",
        "missing_skill_cause_visible",
        "actionable_recovery_visible",
        "ok_button_count",
        "state_before",
        "state_after",
        "no_persistent_mutation",
    }
    if not isinstance(registration_data, dict) or set(registration_data) != registration_keys:
        errors.append("field bundle missing-SKILL.md registration data mismatch")
    else:
        registration_native = sequences.get("missing_skill_registration")
        errors.extend(
            _validate_uia_element(
                registration_data.get("root_element"),
                expected_name="Skill Magnet",
                expected_control_type="ControlType.MenuItem",
                label="registration root",
            )
        )
        errors.extend(
            _validate_uia_element(
                registration_data.get("unified_element"),
                expected_name="Skill Magnet — 実行確認",
                expected_control_type="ControlType.Window",
                label="registration unified GUI",
            )
        )
        for key, label in (
            ("manager_element", "registration Library Manager"),
            ("error_element", "registration missing-SKILL.md dialog"),
        ):
            element = registration_data.get(key)
            name = str(element.get("name")) if isinstance(element, dict) else ""
            errors.extend(
                _validate_uia_element(
                    element,
                    expected_name=name,
                    expected_control_type="ControlType.Window",
                    label=label,
                )
            )
        root_claims = {
            "root_visible_count": 1,
            "invoke_pattern_available": True,
            "expand_collapse_pattern_available": False,
            "submenu_item_count": 0,
        }
        if {key: registration_data.get(key) for key in root_claims} != root_claims:
            errors.append("field bundle registration root is not the one-root contract")
        project = registration_native.get("project") if registration_native else None
        registration_claims = {
            "selected_path_sha256": project,
            "registration_source_sha256": registration_data.get(
                "registration_source_sha256"
            ),
            "selected_path_visible": True,
            "missing_skill_cause_visible": True,
            "actionable_recovery_visible": True,
            "ok_button_count": 1,
            "no_persistent_mutation": True,
        }
        if (
            {key: registration_data.get(key) for key in registration_claims}
            != registration_claims
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(registration_data.get("registration_source_sha256", "")),
            )
            or registration_data.get("project_sha256") != project
            or registration_data.get("invocation_id")
            != (registration_native.get("invocation") if registration_native else None)
            or registration_data.get("native_sequence_sha256")
            != (registration_native.get("sequence_sha256") if registration_native else None)
        ):
            errors.append("field bundle registration claims do not bind native evidence")
        if registration_native:
            for key in ("unified_element", "manager_element", "error_element"):
                element = registration_data.get(key)
                if isinstance(element, dict) and element.get("process_id") != registration_native.get(
                    "process_id"
                ):
                    errors.append(f"field bundle registration {key} belongs to another process")
        validate_unchanged_snapshots(registration_data, "rejected registration")
        validate_workflow_after_native(
            registration_entry, registration_native, "missing-SKILL.md registration"
        )
        workflows["registration_recovery_observation"] = registration_claims

    runtime_entry = workflow_entries["runtime_skill_projectless"]
    runtime_data = runtime_entry.get("data")
    runtime_keys = {
        "root_element",
        "root_visible_count",
        "invoke_pattern_available",
        "expand_collapse_pattern_available",
        "submenu_item_count",
        "unified_element",
        "invocation_id",
        "project_sha256",
        "native_sequence_sha256",
        "clicked_path_sha256",
        "runtime_path_hidden_as_workspace",
        "projectless_semantics_visible",
        "skill_content_before_sha256",
        "skill_content_after_sha256",
        "state_before",
        "state_after",
        "no_persistent_mutation",
        "read_only",
    }
    if not isinstance(runtime_data, dict) or set(runtime_data) != runtime_keys:
        errors.append("field bundle runtime-skill projectless data mismatch")
    else:
        runtime_native = sequences.get("runtime_skill_projectless")
        errors.extend(
            _validate_uia_element(
                runtime_data.get("root_element"),
                expected_name="Skill Magnet",
                expected_control_type="ControlType.MenuItem",
                label="runtime-skill root",
            )
        )
        errors.extend(
            _validate_uia_element(
                runtime_data.get("unified_element"),
                expected_name="Skill Magnet — 実行確認",
                expected_control_type="ControlType.Window",
                label="runtime-skill unified GUI",
            )
        )
        root_claims = {
            "root_visible_count": 1,
            "invoke_pattern_available": True,
            "expand_collapse_pattern_available": False,
            "submenu_item_count": 0,
        }
        if {key: runtime_data.get(key) for key in root_claims} != root_claims:
            errors.append("field bundle runtime-skill root is not the one-root contract")
        project = runtime_native.get("project") if runtime_native else None
        runtime_claims = {
            "clicked_path_sha256": project,
            "runtime_path_hidden_as_workspace": True,
            "projectless_semantics_visible": True,
            "skill_content_sha256": runtime_data.get("skill_content_after_sha256"),
            "read_only": True,
        }
        if (
            runtime_data.get("skill_content_before_sha256")
            != runtime_data.get("skill_content_after_sha256")
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(runtime_data.get("skill_content_after_sha256", ""))
            )
            or {key: runtime_data.get(key) for key in runtime_claims if key != "skill_content_sha256"}
            != {key: value for key, value in runtime_claims.items() if key != "skill_content_sha256"}
            or runtime_data.get("project_sha256") != project
            or runtime_data.get("invocation_id")
            != (runtime_native.get("invocation") if runtime_native else None)
            or runtime_data.get("native_sequence_sha256")
            != (runtime_native.get("sequence_sha256") if runtime_native else None)
        ):
            errors.append("field bundle runtime-skill claims do not bind read-only native evidence")
        if runtime_native:
            element = runtime_data.get("unified_element")
            if isinstance(element, dict) and element.get("process_id") != runtime_native.get(
                "process_id"
            ):
                errors.append("field bundle runtime-skill GUI belongs to another process")
        validate_unchanged_snapshots(runtime_data, "runtime-skill launch")
        validate_workflow_after_native(
            runtime_entry, runtime_native, "runtime-skill projectless launch"
        )
        workflows["runtime_skill_observation"] = runtime_claims
    return errors, derived, workflows, session_id


def _attestation_scalar(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def _field_attestation_payload(bundle: dict[str, object]) -> bytes:
    package = bundle.get("package") if isinstance(bundle.get("package"), dict) else {}
    recovery = (
        bundle.get("recovery_observations")
        if isinstance(bundle.get("recovery_observations"), dict)
        else {}
    )
    selector = (
        bundle.get("selector_contract")
        if isinstance(bundle.get("selector_contract"), dict)
        else {}
    )
    manager = (
        bundle.get("library_manager_observation")
        if isinstance(bundle.get("library_manager_observation"), dict)
        else {}
    )
    registration = (
        bundle.get("registration_recovery_observation")
        if isinstance(bundle.get("registration_recovery_observation"), dict)
        else {}
    )
    runtime_skill = (
        bundle.get("runtime_skill_observation")
        if isinstance(bundle.get("runtime_skill_observation"), dict)
        else {}
    )
    hashes = bundle.get("hashes") if isinstance(bundle.get("hashes"), dict) else {}
    native = (
        bundle.get("native_source_binding")
        if isinstance(bundle.get("native_source_binding"), dict)
        else {}
    )
    observations = bundle.get("explorer_observations")
    observations_by_source = {
        str(observation.get("source")): observation
        for observation in observations
        if isinstance(observation, dict)
    } if isinstance(observations, list) else {}
    values: list[tuple[str, object]] = [
        ("contract", "skill-magnet-windows-explorer-field-v5"),
        ("schema_version", bundle.get("schema_version")),
        ("release_version", bundle.get("release_version")),
        ("release_code_sha", bundle.get("release_code_sha")),
        ("field_status", bundle.get("field_status")),
        ("observed_at_utc", bundle.get("observed_at_utc")),
        ("collector_sha256", bundle.get("collector_sha256")),
        (
            "ui_receipts_sha256",
            hashlib.sha256(
                json.dumps(
                    bundle.get("ui_receipts"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        ),
    ]
    runtime = (
        bundle.get("python_runtime")
        if isinstance(bundle.get("python_runtime"), dict)
        else {}
    )
    for key in (
        "module_version",
        "distribution_version",
        "distribution_name",
        "executable_path_sha256",
        "module_path_sha256",
        "distribution_module_path_sha256",
        "payload_sha256",
    ):
        values.append((f"python_runtime.{key}", runtime.get(key)))
    for key in (
        "name",
        "version",
        "architecture",
        "publisher",
        "package_full_name",
        "same_name_package_count",
        "expected_identity_match_count",
        "unexpected_same_name_package_count",
        "usable_installed_state",
        "menu_contract_matches_config",
        "command_target_signature_valid",
    ):
        values.append((f"package.{key}", package.get(key)))
    for key in (
        "contract",
        "source_tree_sha256",
        "package_manifest_source_tree_sha256",
        "external_manifest_source_tree_sha256",
        "package_dll_export_source_tree_sha256",
        "external_dll_export_source_tree_sha256",
        "package_dll_embedded_binding_count",
        "external_dll_embedded_binding_count",
        "package_external_artifacts_equal",
        "signed_msix_payload_matches_package",
        "isolated_contract_probe_passed",
        "isolated_contract_probe_mode",
        "status_native_source_tree_sha256",
        "status_native_source_manifest_valid",
        "status_native_artifact_hashes_valid",
        "status_dll_native_source_binding_valid",
        "status_native_build_binding_valid",
    ):
        values.append((f"native_source_binding.{key}", native.get(key)))
    for source in _FIELD_SOURCES:
        observation = observations_by_source.get(source, {})
        values.extend(
            (
                (f"{source}.invocation_id", observation.get("invocation_id")),
                (f"{source}.project_sha256", observation.get("project_sha256")),
                (
                    f"{source}.selection_choice_values_sha256",
                    observation.get("selection_choice_values_sha256"),
                ),
                (
                    f"{source}.selected_choice_value_sha256",
                    observation.get("selected_choice_value_sha256"),
                ),
                (
                    f"{source}.library_manager_button_text_sha256",
                    observation.get("library_manager_button_text_sha256"),
                ),
                (
                    f"{source}.register_button_text_sha256",
                    observation.get("register_button_text_sha256"),
                ),
            )
        )
    values.extend(
        (
            ("selector.choice_map_sha256", selector.get("choice_map_sha256")),
            ("selector.ordered_label_sha256", selector.get("ordered_label_sha256")),
            ("selector.choice_count", selector.get("choice_count")),
            ("selector.selected_label_sha256", selector.get("selected_label_sha256")),
            (
                "selector.exact_selector_combo_count",
                selector.get("exact_selector_combo_count"),
            ),
            (
                "library_manager.configured_remote_sha256",
                manager.get("configured_remote_sha256"),
            ),
            (
                "library_manager.configured_remote_visible",
                manager.get("configured_remote_visible"),
            ),
            (
                "library_manager.create_button_text_sha256",
                manager.get("create_button_text_sha256"),
            ),
            (
                "library_manager.update_button_text_sha256",
                manager.get("update_button_text_sha256"),
            ),
            (
                "library_manager.delete_button_text_sha256",
                manager.get("delete_button_text_sha256"),
            ),
            (
                "library_manager.reload_button_text_sha256",
                manager.get("reload_button_text_sha256"),
            ),
            (
                "library_manager.same_folder_repeat_focused_existing_manager",
                manager.get("same_folder_repeat_focused_existing_manager"),
            ),
            (
                "library_manager.different_folder_actionable_recovery_visible",
                manager.get("different_folder_actionable_recovery_visible"),
            ),
            (
                "library_manager.no_persistent_mutation",
                manager.get("no_persistent_mutation"),
            ),
            (
                "registration.selected_path_sha256",
                registration.get("selected_path_sha256"),
            ),
            (
                "registration.selected_path_visible",
                registration.get("selected_path_visible"),
            ),
            (
                "registration.missing_skill_cause_visible",
                registration.get("missing_skill_cause_visible"),
            ),
            (
                "registration.actionable_recovery_visible",
                registration.get("actionable_recovery_visible"),
            ),
            (
                "registration.no_persistent_mutation",
                registration.get("no_persistent_mutation"),
            ),
            (
                "runtime_skill.clicked_path_sha256",
                runtime_skill.get("clicked_path_sha256"),
            ),
            (
                "runtime_skill.runtime_path_hidden_as_workspace",
                runtime_skill.get("runtime_path_hidden_as_workspace"),
            ),
            (
                "runtime_skill.projectless_semantics_visible",
                runtime_skill.get("projectless_semantics_visible"),
            ),
            (
                "runtime_skill.skill_content_sha256",
                runtime_skill.get("skill_content_sha256"),
            ),
            ("runtime_skill.read_only", runtime_skill.get("read_only")),
        )
    )
    for key in (
        "same_folder_repeat_focused_existing_window",
        "same_folder_repeat_gui_count",
        "different_folder_busy_message_visible",
        "different_folder_actionable_recovery_visible",
        "closed_window_relaunch_succeeded",
    ):
        values.append((f"recovery.{key}", recovery.get(key)))
    for key in _FIELD_HASH_KEYS:
        values.append((f"hashes.{key}", hashes.get(key)))
    return ("\n".join(f"{key}={_attestation_scalar(value)}" for key, value in values) + "\n").encode(
        "utf-8"
    )


def _verify_windows_field_attestation(
    signed_payload: bytes, signature: bytes, dll_payload: bytes, signer_thumbprint: str
) -> list[str]:
    """Verify detached CMS and require the installed DLL's trusted signer."""
    script = r"""
param([string]$ContentPath, [string]$SignaturePath, [string]$DllPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$content = [IO.File]::ReadAllBytes($ContentPath)
$encoded = [IO.File]::ReadAllBytes($SignaturePath)
$info = [System.Security.Cryptography.Pkcs.ContentInfo]::new($content)
$cms = [System.Security.Cryptography.Pkcs.SignedCms]::new($info, $true)
$cms.Decode($encoded)
$cms.CheckSignature($true)
if ($cms.SignerInfos.Count -ne 1) { throw 'CMS must contain exactly one signer.' }
$dll = Get-AuthenticodeSignature -LiteralPath $DllPath
[ordered]@{
    cms_thumbprint = $cms.SignerInfos[0].Certificate.Thumbprint.ToLowerInvariant()
    cms_subject = $cms.SignerInfos[0].Certificate.Subject
    cms_digest_oid = $cms.SignerInfos[0].DigestAlgorithm.Value
    cms_public_key_oid = $cms.SignerInfos[0].Certificate.PublicKey.Oid.Value
    dll_status = $dll.Status.ToString()
    dll_thumbprint = if ($dll.SignerCertificate) {
        $dll.SignerCertificate.Thumbprint.ToLowerInvariant()
    } else { '' }
    dll_subject = if ($dll.SignerCertificate) { $dll.SignerCertificate.Subject } else { '' }
    dll_public_key_oid = if ($dll.SignerCertificate) {
        $dll.SignerCertificate.PublicKey.Oid.Value
    } else { '' }
} | ConvertTo-Json -Compress
"""
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content_path = root / "attestation-content.bin"
            signature_path = root / "attestation.p7s"
            dll_path = root / "SkillMagnetCommand.dll"
            content_path.write_bytes(signed_payload)
            signature_path.write_bytes(signature)
            dll_path.write_bytes(dll_payload)
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                    "-ContentPath",
                    str(content_path),
                    "-SignaturePath",
                    str(signature_path),
                    "-DllPath",
                    str(dll_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
    except OSError as error:
        return [f"field bundle attestation verifier could not run: {error}"]
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown verification failure").strip()
        return [f"field bundle detached CMS attestation is invalid: {detail}"]
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return ["field bundle attestation verifier returned invalid output"]
    expected_thumbprint = signer_thumbprint.lower()
    expected = {
        "cms_thumbprint": expected_thumbprint,
        "cms_subject": "CN=Skill Magnet Local",
        "cms_digest_oid": "2.16.840.1.101.3.4.2.1",
        "cms_public_key_oid": "1.2.840.113549.1.1.1",
        "dll_thumbprint": expected_thumbprint,
        "dll_subject": "CN=Skill Magnet Local",
        "dll_public_key_oid": "1.2.840.113549.1.1.1",
    }
    if any(result.get(key) != value for key, value in expected.items()) or result.get(
        "dll_status"
    ) not in {"Valid", "NotTrusted", "UnknownError"}:
        return ["field bundle attestation signer does not match the installed DLL signer"]
    return []


def validate_field_bundle(
    ledger: dict[str, object], bundle_path: Path, invoke_log: Path, repository: Path
) -> list[str]:
    errors: list[str] = []
    if not bundle_path.is_file():
        return [f"Windows Explorer field bundle is missing: {bundle_path}"]
    if _is_reparse_or_link(bundle_path):
        return ["Windows Explorer field bundle must not be a link or reparse point"]
    payload = bundle_path.read_bytes()
    if not payload or len(payload) > _FIELD_BUNDLE_MAX_BYTES:
        return ["Windows Explorer field bundle size is outside the accepted range"]
    if hashlib.sha256(payload).hexdigest() != ledger.get("windows_explorer_field_bundle_sha256"):
        errors.append("windows_explorer_field_bundle_sha256 mismatch")
    try:
        bundle = _strict_json_loads(payload, label="Windows Explorer field bundle")
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        return errors + [f"Windows Explorer field bundle is invalid strict JSON: {error}"]
    if not isinstance(bundle, dict):
        return errors + ["Windows Explorer field bundle root must be an object"]
    required_root_keys = {
        "schema_version",
        "release_version",
        "release_code_sha",
        "field_status",
        "observed_at_utc",
        "collector_sha256",
        "python_runtime",
        "package",
        "native_source_binding",
        "artifacts",
        "hashes",
        "uia_transcript",
        "ui_receipts",
        "selector_contract",
        "explorer_observations",
        "library_manager_observation",
        "registration_recovery_observation",
        "runtime_skill_observation",
        "recovery_observations",
        "attestation",
    }
    if set(bundle) != required_root_keys:
        errors.append("Windows Explorer field bundle root keys do not match the v5 contract")
    version = str(ledger.get("release_version", ""))
    expected_status = f"PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_{version.replace('.', '_')}"
    if bundle.get("schema_version") != 5:
        errors.append("field bundle schema_version must be 5")
    if bundle.get("release_version") != version:
        errors.append("field bundle release_version mismatch")
    release_code_sha = str(bundle.get("release_code_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", release_code_sha):
        errors.append("field bundle release_code_sha must be lowercase 40-hex")
    elif release_code_sha != ledger.get("release_code_sha"):
        errors.append("field bundle release_code_sha does not match the release ledger")
    if bundle.get("field_status") != expected_status:
        errors.append("field bundle status mismatch")
    observed_at = _parse_utc(bundle.get("observed_at_utc"))
    if observed_at is None:
        errors.append("field bundle observed_at_utc is not UTC RFC3339")
    collector = repository / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
    collector_hash = (
        hashlib.sha256(
            _normalized_windows_powershell_bytes(collector.read_bytes())
        ).hexdigest()
        if collector.is_file()
        else ""
    )
    if bundle.get("collector_sha256") != collector_hash:
        errors.append("field bundle collector_sha256 mismatch")

    python_runtime = bundle.get("python_runtime")
    expected_runtime_keys = {
        "module_version",
        "distribution_version",
        "distribution_name",
        "executable_path_sha256",
        "module_path_sha256",
        "distribution_module_path_sha256",
        "payload_sha256",
    }
    runtime_executable_digest = ""
    if not isinstance(python_runtime, dict) or set(python_runtime) != expected_runtime_keys:
        errors.append("field bundle Python runtime keys do not match the contract")
    else:
        if (
            python_runtime.get("module_version") != version
            or python_runtime.get("distribution_version") != version
            or python_runtime.get("distribution_name") != "skill-magnet"
        ):
            errors.append("field bundle installed Python package version mismatch")
        for key in (
            "executable_path_sha256",
            "module_path_sha256",
            "distribution_module_path_sha256",
            "payload_sha256",
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", str(python_runtime.get(key, ""))):
                errors.append(f"field bundle Python runtime {key} must be 64-hex")
        if python_runtime.get("module_path_sha256") != python_runtime.get(
            "distribution_module_path_sha256"
        ):
            errors.append(
                "field bundle imported module is not owned by the installed distribution"
            )
        runtime_executable_digest = str(python_runtime.get("executable_path_sha256", ""))
        try:
            expected_runtime_digest = _release_runtime_payload_sha256(repository)
        except (OSError, ValueError) as error:
            errors.append(
                "field bundle release runtime could not be verified safely before "
                f"acceptance: {error}"
            )
        else:
            if python_runtime.get("payload_sha256") != expected_runtime_digest:
                errors.append("field bundle installed Python runtime differs from release inputs")

    package = bundle.get("package")
    expected_package = {
        "name": "SkillMagnet.ContextMenu",
        "version": f"{version}.0",
        "architecture": "X64",
        "publisher": "CN=Skill Magnet Local",
        "package_full_name": package.get("package_full_name") if isinstance(package, dict) else None,
        "same_name_package_count": 1,
        "expected_identity_match_count": 1,
        "unexpected_same_name_package_count": 0,
        "usable_installed_state": True,
        "menu_contract_matches_config": True,
        "command_target_signature_valid": True,
    }
    if not isinstance(package, dict) or package != expected_package:
        errors.append("field bundle package identity/count/status contract mismatch")
    elif not re.fullmatch(
        rf"SkillMagnet\.ContextMenu_{re.escape(version)}\.0_x64__[A-Za-z0-9]+",
        str(package.get("package_full_name", "")),
    ):
        errors.append("field bundle package_full_name mismatch")

    hashes = bundle.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(_FIELD_HASH_KEYS):
        errors.append("field bundle hashes do not match the v5 contract")
        hashes = {}
    for key in _FIELD_HASH_KEYS:
        if not re.fullmatch(r"[0-9a-f]{64}", str(hashes.get(key, ""))):
            errors.append(f"field bundle {key} must be 64-hex")
    if invoke_log.is_file() and hashes.get("invoke_log_sha256") != hashlib.sha256(
        invoke_log.read_bytes()
    ).hexdigest():
        errors.append("field bundle invoke_log_sha256 does not bind the supplied log")

    artifacts = bundle.get("artifacts")
    expected_artifact_names = {
        "appx_manifest",
        "menu_manifest",
        "command_dll",
        "identity_exe",
        "native_source_manifest",
        "external_command_dll",
        "external_identity_exe",
        "external_native_source_manifest",
        "signed_msix",
        "contract_probe_output",
        "config",
    }
    artifact_payloads: dict[str, bytes] = {}
    artifact_contract = {
        "appx_manifest": ("installed_package", "AppxManifest.xml", "appx_manifest_sha256"),
        "menu_manifest": ("installed_package", "SkillMagnetMenu.tsv", "menu_manifest_sha256"),
        "command_dll": ("installed_package", "SkillMagnetCommand.dll", "dll_sha256"),
        "identity_exe": ("installed_package", "SkillMagnetIdentity.exe", "identity_sha256"),
        "native_source_manifest": (
            "installed_package",
            "SkillMagnetNativeSource.json",
            "native_source_manifest_sha256",
        ),
        "external_command_dll": (
            "external_install_root",
            "SkillMagnetCommand.dll",
            "external_dll_sha256",
        ),
        "external_identity_exe": (
            "external_install_root",
            "SkillMagnetIdentity.exe",
            "external_identity_sha256",
        ),
        "external_native_source_manifest": (
            "external_install_root",
            "SkillMagnetNativeSource.json",
            "external_native_source_manifest_sha256",
        ),
        "signed_msix": (
            "external_install_root",
            "SkillMagnet.ContextMenu.msix",
            "signed_msix_sha256",
        ),
        "contract_probe_output": (
            "isolated_package_artifact_probe",
            "contract-test-output.txt",
            "contract_probe_output_sha256",
        ),
        "config": ("collector_config_argument", "skill-magnet.json", "config_sha256"),
    }
    release_config_path = repository / "skill-magnet.json"
    if not isinstance(artifacts, dict) or set(artifacts) != expected_artifact_names:
        errors.append("field bundle installed artifact snapshots do not match the contract")
        artifacts = {}
    for name, (source, file_name, hash_key) in artifact_contract.items():
        artifact = artifacts.get(name)
        expected_keys = (
            {"source", "file_name", "size", "sha256"}
            if name == "config"
            else {"source", "file_name", "size", "sha256", "bytes_base64"}
        )
        if not isinstance(artifact, dict) or set(artifact) != expected_keys:
            errors.append(f"field bundle {name} artifact keys do not match the contract")
            continue
        if artifact.get("source") != source or artifact.get("file_name") != file_name:
            errors.append(f"field bundle {name} artifact source/name mismatch")
        if name == "config":
            try:
                embedded = release_config_path.read_bytes()
            except OSError as error:
                errors.append(f"release config is unreadable: {error}")
                continue
        else:
            embedded = _decode_embedded_bytes(
                artifact.get("bytes_base64"), label=name, errors=errors
            )
        if embedded is None:
            continue
        digest = hashlib.sha256(embedded).hexdigest()
        if artifact.get("size") != len(embedded) or artifact.get("sha256") != digest:
            errors.append(f"field bundle {name} artifact size/hash does not bind its bytes")
        if hashes.get(hash_key) != digest:
            errors.append(f"field bundle {hash_key} does not bind embedded {name} bytes")
        artifact_payloads[name] = embedded

    try:
        expected_native_source = _native_source_manifest_from_repository(repository)
    except (OSError, UnicodeError) as error:
        errors.append(f"release native provenance inputs are unreadable: {error}")
        expected_native_source = {
            "schema_version": 1,
            "contract": _WINDOWS_NATIVE_SOURCE_CONTRACT,
            "source_tree_sha256": "",
            "inputs": [],
        }
    source_tree_sha256 = str(expected_native_source["source_tree_sha256"])

    def parse_native_manifest(name: str) -> dict[str, object] | None:
        payload = artifact_payloads.get(name)
        if payload is None:
            return None
        try:
            manifest = _strict_json_loads(payload, label=f"field bundle {name}")
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"field bundle {name} is not valid strict UTF-8 JSON: {error}")
            return None
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version",
            "contract",
            "source_tree_sha256",
            "inputs",
            "artifacts",
        }:
            errors.append(f"field bundle {name} keys do not match the native source contract")
            return None
        for key, value in expected_native_source.items():
            if manifest.get(key) != value:
                errors.append(
                    f"field bundle {name} does not match release native source inputs"
                )
                break
        return manifest

    package_native_manifest = parse_native_manifest("native_source_manifest")
    external_native_manifest = parse_native_manifest("external_native_source_manifest")
    if (
        package_native_manifest is not None
        and external_native_manifest is not None
        and package_native_manifest != external_native_manifest
    ):
        errors.append("field bundle package/external native source manifests differ")

    package_artifacts = [
        {
            "path": "SkillMagnetCommand.dll",
            "size": len(artifact_payloads.get("command_dll", b"")),
            "sha256": hashlib.sha256(artifact_payloads.get("command_dll", b"")).hexdigest(),
        },
        {
            "path": "SkillMagnetIdentity.exe",
            "size": len(artifact_payloads.get("identity_exe", b"")),
            "sha256": hashlib.sha256(artifact_payloads.get("identity_exe", b"")).hexdigest(),
        },
    ]
    external_artifacts = [
        {
            "path": "SkillMagnetCommand.dll",
            "size": len(artifact_payloads.get("external_command_dll", b"")),
            "sha256": hashlib.sha256(
                artifact_payloads.get("external_command_dll", b"")
            ).hexdigest(),
        },
        {
            "path": "SkillMagnetIdentity.exe",
            "size": len(artifact_payloads.get("external_identity_exe", b"")),
            "sha256": hashlib.sha256(
                artifact_payloads.get("external_identity_exe", b"")
            ).hexdigest(),
        },
    ]
    if package_native_manifest is not None and package_native_manifest.get(
        "artifacts"
    ) != package_artifacts:
        errors.append("field bundle package native manifest does not bind DLL/Identity bytes")
    if external_native_manifest is not None and external_native_manifest.get(
        "artifacts"
    ) != external_artifacts:
        errors.append("field bundle external native manifest does not bind DLL/Identity bytes")
    package_external_artifacts_equal = bool(
        artifact_payloads.get("command_dll")
        and artifact_payloads.get("identity_exe")
        and artifact_payloads.get("command_dll")
        == artifact_payloads.get("external_command_dll")
        and artifact_payloads.get("identity_exe")
        == artifact_payloads.get("external_identity_exe")
    )
    if not package_external_artifacts_equal:
        errors.append("field bundle package/external DLL or Identity bytes differ")

    binding_marker = (
        _WINDOWS_NATIVE_SOURCE_CONTRACT + ":" + source_tree_sha256
    ).encode("utf-16-le")
    package_marker_count = artifact_payloads.get("command_dll", b"").count(binding_marker)
    external_marker_count = artifact_payloads.get("external_command_dll", b"").count(
        binding_marker
    )
    if package_marker_count != 1 or external_marker_count != 1:
        errors.append("field bundle DLL native source marker is missing or duplicated")

    signed_msix_payload_matches_package = False
    signed_msix_payload = artifact_payloads.get("signed_msix")
    if signed_msix_payload is not None:
        try:
            with zipfile.ZipFile(io.BytesIO(signed_msix_payload)) as archive:
                required_msix_payloads = {
                    "AppxManifest.xml": artifact_payloads.get("appx_manifest"),
                    "SkillMagnetMenu.tsv": artifact_payloads.get("menu_manifest"),
                    "SkillMagnetCommand.dll": artifact_payloads.get("command_dll"),
                    "SkillMagnetIdentity.exe": artifact_payloads.get("identity_exe"),
                    "SkillMagnetNativeSource.json": artifact_payloads.get(
                        "native_source_manifest"
                    ),
                }
                infos_by_name: dict[str, list[zipfile.ZipInfo]] = {}
                for info in archive.infolist():
                    infos_by_name.setdefault(info.filename, []).append(info)
                required_names = set(required_msix_payloads) | {"AppxSignature.p7x"}
                unambiguous = all(
                    len(infos_by_name.get(name, [])) == 1 for name in required_names
                )
                signed_msix_payload_matches_package = bool(
                    unambiguous
                    and all(
                        payload is not None
                        and archive.read(infos_by_name[path][0]) == payload
                        for path, payload in required_msix_payloads.items()
                    )
                    and archive.read(infos_by_name["AppxSignature.p7x"][0])
                )
        except (KeyError, OSError, zipfile.BadZipFile):
            pass
    if not signed_msix_payload_matches_package:
        errors.append("field bundle signed MSIX does not bind installed package artifacts")

    contract_probe_payload = artifact_payloads.get("contract_probe_output")
    isolated_contract_probe_passed = bool(
        contract_probe_payload is not None
        and _normalized_text_bytes(contract_probe_payload)
        == b"SkillMagnet direct-root IExplorerCommand contract PASS (Python host)\n"
    )
    if not isolated_contract_probe_passed:
        errors.append("field bundle isolated native contract probe did not pass")

    native_binding = bundle.get("native_source_binding")
    expected_native_binding = {
        "contract": _WINDOWS_NATIVE_SOURCE_CONTRACT,
        "source_tree_sha256": source_tree_sha256,
        "package_manifest_source_tree_sha256": source_tree_sha256,
        "external_manifest_source_tree_sha256": source_tree_sha256,
        "package_dll_export_source_tree_sha256": source_tree_sha256,
        "external_dll_export_source_tree_sha256": source_tree_sha256,
        "package_dll_embedded_binding_count": 1,
        "external_dll_embedded_binding_count": 1,
        "package_external_artifacts_equal": True,
        "signed_msix_payload_matches_package": True,
        "isolated_contract_probe_passed": True,
        "isolated_contract_probe_mode": "full-invoke",
        "status_native_source_tree_sha256": source_tree_sha256,
        "status_native_source_manifest_valid": True,
        "status_native_artifact_hashes_valid": True,
        "status_dll_native_source_binding_valid": True,
        "status_native_build_binding_valid": True,
    }
    if not isinstance(native_binding, dict) or native_binding != expected_native_binding:
        errors.append("field bundle native source binding summary does not match verified artifacts")

    appx_payload = artifact_payloads.get("appx_manifest")
    release_appx = repository / "native" / "windows-modern-context-menu" / "AppxManifest.xml"
    if appx_payload is not None:
        if not release_appx.is_file() or _normalized_text_bytes(
            appx_payload
        ) != _normalized_text_bytes(release_appx.read_bytes()):
            errors.append("field bundle installed AppxManifest.xml bytes differ from the release input")
        try:
            document = ET.fromstring(appx_payload)
            namespace = {
                "f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
                "com": "http://schemas.microsoft.com/appx/manifest/com/windows10",
            }
            identity = document.find("f:Identity", namespace)
            classes = document.findall(".//com:Class", namespace)
            if identity is None or identity.attrib != {
                "Name": "SkillMagnet.ContextMenu",
                "Publisher": "CN=Skill Magnet Local",
                "Version": f"{version}.0",
                "ProcessorArchitecture": "x64",
            } or [item.get("Path") for item in classes] != ["SkillMagnetCommand.dll"]:
                errors.append("field bundle installed AppxManifest.xml identity/COM binding mismatch")
        except ET.ParseError:
            errors.append("field bundle installed AppxManifest.xml bytes are not XML")

    config_payload = artifact_payloads.get("config")
    expected_choices: list[dict[str, object]] = []
    configured_remote = ""
    release_config = release_config_path
    if config_payload is not None and (
        not release_config.is_file()
        or _normalized_text_bytes(config_payload)
        != _normalized_text_bytes(release_config.read_bytes())
    ):
        errors.append("field bundle config bytes differ from the release config")
    if config_payload is not None:
        try:
            expected_choices = _configured_selector_choices(config_payload)
            configured_remote = _configured_repository_url(config_payload)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            errors.append(f"field bundle config selector contract is invalid: {error}")
    if not expected_choices:
        errors.append("field bundle release config has no selectable skills or packs")

    def iter_strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from iter_strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from iter_strings(nested)

    def iter_keys(value: object):
        if isinstance(value, dict):
            for key, nested in value.items():
                yield str(key)
                yield from iter_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from iter_keys(nested)

    private_values = [configured_remote] + [
        str(choice.get("label")) for choice in expected_choices
    ]
    bundle_strings = tuple(iter_strings(bundle))
    if any(
        private and any(private in candidate for candidate in bundle_strings)
        for private in private_values
    ):
        errors.append(
            "field bundle contains raw repository or selector labels outside release config"
        )
    if any(
        re.search(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|var/tmp)/)", candidate)
        for candidate in bundle_strings
    ):
        errors.append("field bundle contains a plaintext local path")
    if any(re.search(r"https?://", candidate, re.IGNORECASE) for candidate in bundle_strings):
        errors.append("field bundle contains a plaintext URL")
    forbidden_raw_keys = {
        "configured_choices",
        "configured_remote",
        "config_bytes",
        "config_payload",
        "request",
        "request_text",
        "prompt",
        "selection_choice_labels",
    }
    if any(key.casefold() in forbidden_raw_keys for key in iter_keys(bundle)):
        errors.append("field bundle contains a forbidden raw config/request/label field")

    selector_contract = bundle.get("selector_contract")
    expected_selector_contract = {
        "choice_map_sha256": _selector_choice_map_sha256(expected_choices),
        "ordered_label_sha256": _ordered_selector_label_sha256(expected_choices),
        "choice_count": len(expected_choices),
        "selected_label_sha256": (
            _text_sha256(str(expected_choices[0].get("label")))
            if expected_choices
            else ""
        ),
        "exact_selector_combo_count": 1,
    }
    if (
        not isinstance(selector_contract, dict)
        or selector_contract != expected_selector_contract
    ):
        errors.append(
            "field bundle selector contract does not exactly match configured labels/internal IDs"
        )

    log_errors, sequences, identity_anchors = _parse_field_evidence(ledger, invoke_log)
    errors.extend(log_errors)
    transcript_errors, derived_observations, workflow_observations, transcript_session_id = (
        _validate_uia_transcript(
            bundle.get("uia_transcript"),
            hashes,
            sequences,
            expected_choices,
            configured_remote,
        )
    )
    errors.extend(transcript_errors)
    registration_workflow = workflow_observations.get("registration_recovery_observation")
    registration_anchor = identity_anchors.get("missing_skill_registration")
    if (
        not isinstance(registration_workflow, dict)
        or not isinstance(registration_anchor, dict)
        or registration_workflow.get("registration_source_sha256")
        != registration_anchor.get("registration_source")
    ):
        errors.append(
            "field bundle registration source digest does not bind the supplied invoke evidence log"
        )

    ui_receipts = bundle.get("ui_receipts")
    receipt_entry_keys = {
        "role", "native_role", "invocation_id", "project_sha256", "target_sha256",
        "process_id", "native_sequence_sha256", "transcript_session_id", "phase",
        "process_instance_id", "generation", "revision",
        "claim_widget_id", "claim_field", "claim_sha256", "receipt_sha256",
        "surface_sha256", "receipt",
    }
    role_contract = {
        "selected_manager_click": (
            "selected_item", "context_selection", "library_manager", "text_sha256",
            _text_sha256("Library Manager"),
        ),
        "manager_remote": (
            "selected_item", "library_manager", "configured_remote", "value_sha256",
            _text_sha256(configured_remote),
        ),
        "background_selection": (
            "background_site", "context_selection", "selection_choice", "values_sha256",
            expected_selector_contract["ordered_label_sha256"],
        ),
        "registration_click": (
            "missing_skill_registration", "context_selection", "register_selected", "text_sha256",
            _text_sha256("このフォルダーのスキルを登録"),
        ),
        "registration_source": (
            "missing_skill_registration", "library_manager", "registration_source", "value_sha256",
            (
                registration_anchor.get("registration_source")
                if isinstance(registration_anchor, dict)
                else None
            ),
        ),
        "runtime_projectless": (
            "runtime_skill_projectless", "context_selection", "project", "text_sha256",
            _text_sha256(
                "作業対象フォルダー: 指定なし（デスクトップアプリが新規タスク用領域を自動作成）"
            ),
        ),
    }
    seen_roles: set[str] = set()
    seen_receipts: set[str] = set()
    if not isinstance(ui_receipts, list) or len(ui_receipts) != len(role_contract):
        errors.append("field bundle ui_receipts must contain exactly six required receipts")
        receipt_entries = ui_receipts if isinstance(ui_receipts, list) else []
    else:
        receipt_entries = ui_receipts
    for index, entry in enumerate(receipt_entries):
        label = f"field bundle ui_receipts[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        receipt = entry.get("receipt")
        # This is deliberately unconditional for every object entry: no metadata
        # failure may bypass the exact-schema consumer gate.
        try:
            _validate_ui_owner_receipt_schema(receipt)
        except ValueError as error:
            errors.append(f"{label} receipt schema mismatch: {error}")
        if set(entry) != receipt_entry_keys:
            errors.append(f"{label} keys do not match the exact contract")
        role = entry.get("role")
        if not isinstance(role, str) or role not in role_contract:
            errors.append(f"{label} role is not permitted")
            continue
        if role in seen_roles:
            errors.append(f"{label} role is duplicated")
        seen_roles.add(role)
        native_role, expected_phase, widget_id, claim_field, expected_claim = role_contract[role]
        native = sequences.get(native_role)
        identity_anchor = identity_anchors.get(native_role)
        if entry.get("native_role") != native_role:
            errors.append(f"{label} native_role does not match role {role}")
        if (
            not re.fullmatch(r"[0-9a-f]{32}", str(entry.get("transcript_session_id", "")))
            or entry.get("transcript_session_id") != transcript_session_id
        ):
            errors.append(f"{label} does not bind the verified UI transcript session")
        if native is None or identity_anchor is None:
            errors.append(f"{label} has no verified native workflow/identity sequence")
        else:
            expected_native_binding = {
                "invocation_id": native.get("invocation"),
                "project_sha256": native.get("project"),
                "target_sha256": identity_anchor.get("target"),
                "process_id": native.get("process_id"),
                "native_sequence_sha256": native.get("sequence_sha256"),
            }
            if any(entry.get(key) != value for key, value in expected_native_binding.items()):
                errors.append(f"{label} does not bind verified native workflow role {native_role}")
        if entry.get("phase") != expected_phase:
            errors.append(f"{label} phase does not match role {role}")
        if entry.get("claim_widget_id") != widget_id or entry.get("claim_field") != claim_field:
            errors.append(f"{label} claim target does not match role {role}")
        claim_sha = entry.get("claim_sha256")
        if not isinstance(claim_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", claim_sha):
            errors.append(f"{label} claim_sha256 must be lowercase 64-hex")
        elif expected_claim is not None and claim_sha != expected_claim:
            errors.append(f"{label} claim does not match the verified field claim")
        if isinstance(receipt, dict):
            if entry.get("target_sha256") != receipt.get("target_sha256"):
                errors.append(f"{label} target_sha256 does not bind its receipt")
            if entry.get("process_id") != receipt.get("pid"):
                errors.append(f"{label} process_id does not bind its receipt")
            for key in ("phase", "process_instance_id", "generation", "revision"):
                if entry.get(key) != receipt.get(key):
                    errors.append(f"{label} {key} does not bind its receipt")
            receipt_digest = hashlib.sha256(
                json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            surface = receipt.get("ui_surface")
            surface_digest = hashlib.sha256(
                json.dumps(surface, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if entry.get("receipt_sha256") != receipt_digest:
                errors.append(f"{label} receipt_sha256 mismatch")
            elif receipt_digest in seen_receipts:
                errors.append(f"{label} reuses a receipt already bound to another role")
            else:
                seen_receipts.add(receipt_digest)
            if entry.get("surface_sha256") != surface_digest:
                errors.append(f"{label} surface_sha256 mismatch")
            if isinstance(surface, dict) and isinstance(surface.get("widgets"), list):
                matches = [
                    widget for widget in surface["widgets"]
                    if isinstance(widget, dict) and widget.get("id") == widget_id
                ]
                if len(matches) != 1 or matches[0].get(claim_field) != claim_sha:
                    errors.append(f"{label} claim does not bind its receipt widget")
    if seen_roles != set(role_contract):
        errors.append("field bundle ui_receipts required role set mismatch")

    dll_payload = artifact_payloads.get("command_dll")
    if dll_payload is not None:
        errors.extend(_validate_pe_x64_dll(dll_payload))

    menu_payload = artifact_payloads.get("menu_manifest")
    menu_command_digest: str | None = None
    if menu_payload is not None:
        try:
            menu_text = menu_payload.decode("utf-8")
        except UnicodeError:
            errors.append("field bundle installed menu manifest bytes are not UTF-8")
        else:
            menu_lines = menu_text.splitlines()
            fields = menu_lines[1].split("\t") if len(menu_lines) == 2 else []
            if (
                not menu_text.endswith("\n")
                or "\r" in menu_text
                or not menu_lines
                or menu_lines[0] != "skill-magnet-menu-v4"
                or len(fields) != 7
                or fields[:6] != [
                "__launcher__",
                "Skill Magnet",
                "launcher",
                "root",
                "Skill Magnet",
                "スキルまたはスキルパックを選び、Codex DesktopまたはClaude Code Desktopへ渡します。",
                ]
            ):
                errors.append("field bundle installed menu manifest is not the one-root v4 contract")
            else:
                command = fields[6]
                argv = _windows_command_line_to_argv(command)
                if len(argv) != 12 or argv[1:] != [
                    "-I",
                    "-m",
                    "skill_magnet",
                    "--config",
                    argv[5] if len(argv) > 5 else "",
                    "context",
                    "--platform",
                    "windows",
                    "--project",
                    "__SKILL_MAGNET_PROJECT__",
                    "--launcher",
                ] or not argv[0].lower().endswith(".exe"):
                    errors.append("field bundle installed menu command contract mismatch")
                elif hashlib.sha256(argv[5].encode("utf-16-le")).hexdigest() != hashes.get(
                    "config_path_sha256"
                ):
                    errors.append("field bundle installed menu command does not bind the config path")
                elif hashlib.sha256(argv[0].encode("utf-16-le")).hexdigest() != runtime_executable_digest:
                    errors.append("field bundle Python probe did not use the installed menu executable")
                menu_command_digest = hashlib.sha256(command.encode("utf-16-le")).hexdigest()

    if menu_command_digest is not None and any(
        sequence.get("template_command") != menu_command_digest for sequence in sequences.values()
    ):
        errors.append("field bundle installed menu command bytes do not bind native command digests")

    observations = bundle.get("explorer_observations")
    if not isinstance(observations, list) or len(observations) != 2:
        errors.append("field bundle requires two Explorer observations")
        observations = []
    observations_by_source = {
        str(observation.get("source")): observation
        for observation in observations
        if isinstance(observation, dict)
    }
    if set(observations_by_source) != set(_FIELD_SOURCES) or len(observations_by_source) != len(observations):
        errors.append("field bundle must contain one selected_item and one background_site observation")
    for source in _FIELD_SOURCES:
        if observations_by_source.get(source) != derived_observations.get(source):
            errors.append(f"field bundle {source} summary is not derived from the raw UIAutomation transcript")
    for key in (
        "library_manager_observation",
        "registration_recovery_observation",
        "runtime_skill_observation",
    ):
        if bundle.get(key) != workflow_observations.get(key):
            errors.append(
                f"field bundle {key} summary is not derived from the raw UIAutomation transcript"
            )
    if bundle.get("recovery_observations") != {
        "same_folder_repeat_focused_existing_window": True,
        "same_folder_repeat_gui_count": 1,
        "different_folder_busy_message_visible": True,
        "different_folder_actionable_recovery_visible": True,
        "closed_window_relaunch_succeeded": True,
    }:
        errors.append("field bundle recovery/repetition observations are incomplete")
    if observed_at is not None:
        transcript = bundle.get("uia_transcript")
        transcript_payload = None
        if isinstance(transcript, dict):
            transcript_payload = _decode_embedded_bytes(
                transcript.get("bytes_base64"), label="UIAutomation transcript", errors=[]
            )
        if transcript_payload:
            try:
                last_entry = json.loads(transcript_payload.decode("utf-8").strip().splitlines()[-1])
                last_observed = _parse_utc(last_entry.get("observed_at_utc"))
            except (UnicodeError, json.JSONDecodeError, IndexError, AttributeError):
                last_observed = None
            if last_observed is not None and not (
                last_observed <= observed_at <= last_observed + dt.timedelta(minutes=5)
            ):
                errors.append("field bundle observed_at_utc is not contemporaneous with the transcript")

    attestation = bundle.get("attestation")
    expected_attestation_keys = {
        "algorithm",
        "signer_thumbprint",
        "signed_payload_sha256",
        "signature_base64",
    }
    if not isinstance(attestation, dict) or set(attestation) != expected_attestation_keys:
        errors.append("field bundle detached attestation keys do not match the contract")
    else:
        signed_payload = _field_attestation_payload(bundle)
        signed_digest = hashlib.sha256(signed_payload).hexdigest()
        thumbprint = str(attestation.get("signer_thumbprint", ""))
        if attestation.get("algorithm") != "sha256-rsa-cms-detached":
            errors.append("field bundle detached attestation algorithm mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}", thumbprint):
            errors.append("field bundle detached attestation signer thumbprint is invalid")
        if thumbprint != ledger.get("windows_explorer_field_signer_thumbprint"):
            errors.append("field bundle signer thumbprint does not match the release ledger pin")
        if attestation.get("signed_payload_sha256") != signed_digest:
            errors.append("field bundle detached attestation payload hash mismatch")
        signature = _decode_embedded_bytes(
            attestation.get("signature_base64"), label="detached attestation", errors=errors
        )
        if signature is not None and dll_payload is not None and re.fullmatch(r"[0-9a-f]{40}", thumbprint):
            errors.extend(
                _verify_windows_field_attestation(
                    signed_payload, signature, dll_payload, thumbprint
                )
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current Explorer release evidence.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--observed-test-count", type=int)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--invoke-log", type=Path)
    parser.add_argument("--field-evidence", type=Path)
    parser.add_argument(
        "--cross-platform-artifact-only",
        action="store_true",
        help="Validate source/wheel provenance without replaying Windows-only field attestation.",
    )
    args = parser.parse_args(argv)
    if args.cross_platform_artifact_only and sys.platform == "win32":
        parser.error("--cross-platform-artifact-only cannot bypass field validation on Windows")
    if not args.cross_platform_artifact_only and (
        args.invoke_log is None or args.field_evidence is None
    ):
        parser.error("--invoke-log and --field-evidence are required for the release gate")
    repository = args.results.resolve().parents[1]
    sys.path.insert(0, str(repository / "src"))
    from skill_magnet.core import Config
    from skill_magnet.platforms import windows_menu_leaves
    config_path = repository / "skill-magnet.json"
    config = Config.load(config_path)
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(project["project"]["version"])
    leaves = windows_menu_leaves(config_path, "%1")
    count = args.observed_test_count
    if count is None:
        counted = subprocess.run(
            [sys.executable, "-c", "import unittest; print(unittest.defaultTestLoader.discover('tests').countTestCases())"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        count = int(counted.stdout.strip())
    results_text = args.results.read_text(encoding="utf-8")
    errors = validate_consistency(
        results_text, observed_test_count=count,
        observed_leaf_count=len(leaves),
        observed_selection_kinds=sorted(
            {config.packs[leaf.pack_id].selection_kind for leaf in leaves}
        ),
        observed_pack_skill_counts=sorted(len(leaf.skill_ids) for leaf in leaves),
        observed_version=project_version)
    errors.extend(
        validate_release_provenance(repository, parse_ledger(results_text), args.wheel)
    )
    ledger = parse_ledger(results_text)
    if not args.cross_platform_artifact_only:
        errors.extend(validate_field_evidence(ledger, args.invoke_log))
        errors.extend(
            validate_field_bundle(
                ledger, args.field_evidence, args.invoke_log, repository
            )
        )
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(
        "PASS: local self-signed release evidence matches product configuration "
        "and test suite; public distribution is not claimed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
