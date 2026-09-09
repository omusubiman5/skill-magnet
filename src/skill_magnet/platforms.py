from __future__ import annotations

import json
import hashlib
import base64
import errno
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from xml.parsers.expat import ExpatError
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from .core import Config, Engine, Pack, SafetyError, SkillMagnetError, _is_link
from . import __version__


_PACKAGE_ROOT = Path(__file__).resolve().parent
LIBRARY_MANAGER_MENU_LABEL = "Library Manager"
REGISTER_FOLDER_MENU_LABEL = "このフォルダーのスキルを登録"


def _absolute_path(path: Path) -> Path:
    """Normalize without consulting a mocked os.name inside cross-platform tests."""
    return type(path)(os.path.abspath(str(path)))


@dataclass(frozen=True)
class ContextMenuSpec:
    platform: str
    integration: str
    menu_label: str
    selected_path_placeholder: str
    command: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "integration": self.integration,
            "menu_label": self.menu_label,
            "selected_path_placeholder": self.selected_path_placeholder,
            "command": list(self.command),
            "automatic_activation": False,
            "required_flow": [
                "open_context_menu",
                "choose_skill_magnet",
                "explicitly_select_pack",
                "explicitly_select_runtime",
                "confirm_target_version_and_purpose",
                "launch",
            ],
        }


@dataclass(frozen=True)
class WindowsMenuLeaf:
    pack_id: str
    skill_ids: tuple[str, ...]
    skill_id: str | None
    display_name: str
    purpose: str
    instruction_digest: str
    acceptance_digest: str
    command: tuple[str, ...]

    @property
    def pack_label(self) -> str:
        return f"Pack: {self.pack_id}"

    @property
    def skill_label(self) -> str:
        kind = "Skill Pack" if self.skill_id is None else "Skill"
        return f"{kind}: {self.display_name}"

    @property
    def skill_ids_digest(self) -> str:
        payload = json.dumps(self.skill_ids, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cli_prefix(config: Path) -> tuple[str, ...]:
    """Return a stable installed-module command for Explorer.

    A context-menu registration outlives the source checkout that created it.
    Never embed a worktree path in the persistent menu contract: launch the
    installed distribution in isolated mode instead.
    """
    executable = type(_PACKAGE_ROOT)(sys.executable)
    return (
        str(executable),
        "-I",
        "-m",
        "skill_magnet",
        "--config",
        str(config.absolute()),
    )


def _python_runtime_source_sha256(package_root: Path) -> str:
    entries: dict[str, bytes] = {}
    for path in package_root.rglob("*.py"):
        if "__pycache__" not in path.parts:
            entries[path.relative_to(package_root).as_posix()] = path.read_bytes()
    digest = hashlib.sha256()
    for name in sorted(entries):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entries[name].replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_isolated_menu_runtime(
    *, run: object = subprocess.run, executable: Path | None = None
) -> dict[str, object]:
    """Refuse a persistent menu whose isolated interpreter loads another release."""

    target = _absolute_path(executable or Path(sys.executable))
    probe = r"""
import hashlib
import importlib.metadata
import json
import pathlib
import sysconfig
import skill_magnet

root = pathlib.Path(skill_magnet.__file__).resolve().parent
distribution = importlib.metadata.distribution("skill-magnet")
owned = []
for item in distribution.files or ():
    if pathlib.PurePosixPath(str(item).replace("\\", "/")).as_posix().endswith("skill_magnet/__init__.py"):
        owned.append(pathlib.Path(distribution.locate_file(item)).resolve())
entries = {}
for path in root.rglob("*.py"):
    if "__pycache__" not in path.parts:
        entries[path.relative_to(root).as_posix()] = path.read_bytes()
digest = hashlib.sha256()
for name in sorted(entries):
    digest.update(name.encode("utf-8")); digest.update(b"\0")
    digest.update(entries[name].replace(b"\r\n", b"\n")); digest.update(b"\0")
direct_url = distribution.read_text("direct_url.json") or ""
editable = False
if direct_url:
    try:
        editable = bool(json.loads(direct_url).get("dir_info", {}).get("editable"))
    except (TypeError, ValueError):
        editable = True
print(json.dumps({
    "module_version": skill_magnet.__version__,
    "distribution_version": distribution.version,
    "distribution_name": distribution.metadata["Name"].casefold(),
    "module_init": str(pathlib.Path(skill_magnet.__file__).resolve()),
    "distribution_module_init": str(owned[0]) if len(owned) == 1 else "",
    "purelib": str(pathlib.Path(sysconfig.get_path("purelib")).resolve()),
    "editable": editable,
    "python_payload_sha256": digest.hexdigest(),
}))
"""
    completed = run(
        [str(target), "-I", "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    detail = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "")).strip()
    if getattr(completed, "returncode", 1) != 0:
        raise SkillMagnetError(
            "右クリックメニューはまだ登録していません。導入済みPythonからSkill Magnetを"
            f"確認できませんでした: {detail or 'isolated import failed'}。同じrelease wheelを"
            "`python -m pip install --force-reinstall <wheel>`で再導入し、この登録操作を再実行してください。"
        )
    try:
        payload = json.loads(getattr(completed, "stdout", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise SkillMagnetError(
            "右クリックメニューはまだ登録していません。導入済みruntimeの確認結果が不正です。"
            "同じrelease wheelを再導入してから、この登録操作を再実行してください。"
        ) from exc
    required = {
        "module_version",
        "distribution_version",
        "distribution_name",
        "module_init",
        "distribution_module_init",
        "purelib",
        "editable",
        "python_payload_sha256",
    }
    module_value = str(payload.get("module_init", ""))
    distribution_value = str(payload.get("distribution_module_init", ""))
    purelib_value = str(payload.get("purelib", ""))
    module_path = Path(module_value)
    distribution_path = Path(distribution_value)
    purelib = Path(purelib_value)
    path_matches = (
        bool(module_value)
        and bool(distribution_value)
        and os.path.normcase(os.path.abspath(module_path))
        == os.path.normcase(os.path.abspath(distribution_path))
    )
    try:
        wheel_installed = bool(purelib_value) and module_path.resolve().is_relative_to(
            purelib.resolve()
        )
    except (OSError, ValueError):
        wheel_installed = False
    expected_digest = _python_runtime_source_sha256(_PACKAGE_ROOT)
    valid = (
        isinstance(payload, dict)
        and set(payload) == required
        and payload.get("module_version") == __version__
        and payload.get("distribution_version") == __version__
        and payload.get("distribution_name") == "skill-magnet"
        and payload.get("editable") is False
        and path_matches
        and wheel_installed
        and payload.get("python_payload_sha256") == expected_digest
    )
    if not valid:
        raise SkillMagnetError(
            "右クリックメニューはまだ登録していません。現在実行中のSkill Magnetと、"
            "永続メニューがisolated modeで読み込む導入済みwheelが同一ではありません。"
            f"期待version={__version__}, module={payload.get('module_version')}, "
            f"distribution={payload.get('distribution_version')}。同じrelease wheelを"
            "`python -m pip install --force-reinstall <wheel>`で再導入し、この登録操作を再実行してください。"
        )
    return payload


def context_menu_spec(platform: str, config: Path) -> ContextMenuSpec:
    prefix = _cli_prefix(config)
    if platform == "windows":
        placeholder = "%V"
        return ContextMenuSpec(
            platform="windows",
            integration="windows_explorer_context_menu",
            menu_label="Skill Magnet",
            selected_path_placeholder=placeholder,
            command=windows_root_launcher_command_argv(config, placeholder),
        )
    if platform == "macos":
        placeholder = "$SELECTED_PATH"
        return ContextMenuSpec(
            platform="macos",
            integration="macos_finder_quick_action",
            menu_label="Skill Magnet...",
            selected_path_placeholder=placeholder,
            command=(
                *prefix,
                "context",
                "--platform",
                "macos",
                "--project",
                placeholder,
                "--launcher",
            ),
        )
    raise SkillMagnetError(f"Unsupported platform: {platform}")


def windows_leaf_command_argv(
    config: Path,
    project: str,
    pack_id: str,
    skill_id: str | None = None,
    runtime: str | None = None,
) -> tuple[str, ...]:
    """Build one Explorer leaf argv without invoking or composing a shell."""
    loaded = Config.load(config)
    if pack_id not in loaded.packs:
        raise SkillMagnetError(f"Unknown pack: {pack_id}")
    pack = loaded.packs[pack_id]
    if pack.selection_kind == "skill" and skill_id not in pack.skills:
        raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
    if pack.selection_kind == "package" and skill_id is not None:
        raise SkillMagnetError(f"Pack {pack_id} must be selected as a complete package")
    if runtime is not None and runtime not in ("codex", "claude"):
        raise SkillMagnetError(f"Unsupported runtime: {runtime}")
    engine = Engine(loaded)
    instruction_digest = _selection_blob_digest(engine, pack, skill_id, "SKILL.md")
    acceptance_digest = _selection_blob_digest(engine, pack, skill_id, "acceptance.json")
    return _windows_leaf_argv(
        config,
        project,
        pack,
        skill_id,
        runtime,
        instruction_digest,
        acceptance_digest,
    )


def _windows_leaf_argv(
    config: Path,
    project: str,
    pack: Pack,
    skill_id: str | None,
    runtime: str | None,
    instruction_digest: str,
    acceptance_digest: str,
) -> tuple[str, ...]:
    skill_ids_digest = hashlib.sha256(
        json.dumps(pack.skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    command = [
        *_cli_prefix(config),
        "context",
        "--platform",
        "windows",
        "--project",
        project,
        "--pack",
        pack.pack_id,
        "--menu-instruction-digest",
        instruction_digest,
        "--menu-acceptance-digest",
        acceptance_digest,
        "--menu-commit",
        pack.expected_commit,
        "--menu-skill-digest",
        skill_ids_digest,
    ]
    if skill_id is not None:
        command.extend(("--skill", skill_id))
    if runtime is not None:
        command.extend(("--runtime", runtime))
    return tuple(command)


def _fixed_blob_digest(
    engine: Engine, pack: Pack, skill_id: str, filename: str
) -> str:
    return hashlib.sha256(
        engine.pack_bytes(pack, f"{skill_id}/{filename}")
    ).hexdigest()


def _selection_blob_digest(
    engine: Engine, pack: Pack, skill_id: str | None, filename: str
) -> str:
    """Bind a menu leaf to one skill or to every skill in a package."""
    if skill_id is not None:
        return _fixed_blob_digest(engine, pack, skill_id, filename)
    payload = {
        selected: _fixed_blob_digest(engine, pack, selected, filename)
        for selected in pack.skills
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def windows_menu_leaves(config: Path, placeholder: str) -> tuple[WindowsMenuLeaf, ...]:
    """Build one immutable Explorer leaf per explicitly selectable package."""
    loaded = Config.load(config)
    engine = Engine(loaded)
    leaves: list[WindowsMenuLeaf] = []
    for pack in loaded.packs.values():
        if pack.selection_kind == "package":
            instruction_digest = _selection_blob_digest(engine, pack, None, "SKILL.md")
            acceptance_digest = _selection_blob_digest(engine, pack, None, "acceptance.json")
            leaves.append(
                WindowsMenuLeaf(
                    pack_id=pack.pack_id,
                    skill_ids=pack.skills,
                    skill_id=None,
                    display_name=pack.menu_label,
                    purpose=pack.purpose,
                    instruction_digest=instruction_digest,
                    acceptance_digest=acceptance_digest,
                    command=_windows_leaf_argv(
                        config,
                        placeholder,
                        pack,
                        None,
                        None,
                        instruction_digest,
                        acceptance_digest,
                    ),
                )
            )
            continue
        for skill_id in pack.skills:
            instruction_digest = _fixed_blob_digest(engine, pack, skill_id, "SKILL.md")
            acceptance_digest = _fixed_blob_digest(engine, pack, skill_id, "acceptance.json")
            leaves.append(
                WindowsMenuLeaf(
                    pack_id=pack.pack_id,
                    skill_ids=pack.skills,
                    skill_id=skill_id,
                    display_name=pack.skill_display_name(skill_id),
                    purpose=pack.skill_purpose(skill_id),
                    instruction_digest=instruction_digest,
                    acceptance_digest=acceptance_digest,
                    command=_windows_leaf_argv(
                        config,
                        placeholder,
                        pack,
                        skill_id,
                        None,
                        instruction_digest,
                        acceptance_digest,
                    ),
                )
            )
    return tuple(leaves)


def windows_command(parts: tuple[str, ...]) -> str:
    """Quote an argv vector with the Windows command-line parsing contract."""
    return " ".join(
        f'"{part}"'
        if part in ("%1", "%V")
        else subprocess.list2cmdline([part])
        for part in parts
    )


WINDOWS_MODERN_PROJECT_MARKER = "__SKILL_MAGNET_PROJECT__"


def windows_root_launcher_command_argv(config: Path, project: str) -> tuple[str, ...]:
    """Build the childless root command that opens the unified selection UI."""
    return (
        *_cli_prefix(config),
        "context",
        "--platform",
        "windows",
        "--project",
        project,
        "--launcher",
    )


def windows_library_manager_command_argv(
    config: Path, project: str, *, register_selected: bool = False
) -> tuple[str, ...]:
    """Build the context-menu command for the standalone library manager GUI."""
    command = (
        *_cli_prefix(config),
        "library",
        "ui",
        "--repository",
        project,
    )
    return (*command, "--register-selected") if register_selected else command


def render_windows_modern_menu_manifest(config: Path) -> str:
    """Render one direct Explorer launcher.

    Windows 11 treats an IExplorerCommand with subcommands as a flyout even
    when ECF_HASSPLITBUTTON is present, so its root Invoke is not a dependable
    launch surface.  Pack, skill, registration, and library actions therefore
    live in the unified UI opened by this single root command.
    """
    Config.load(config)
    launcher_command = windows_command(
        windows_root_launcher_command_argv(config, WINDOWS_MODERN_PROJECT_MARKER)
    )
    if launcher_command.count(WINDOWS_MODERN_PROJECT_MARKER) != 1:
        raise SkillMagnetError(
            "Windows menu config path collides with the project placeholder"
        )
    lines = [
        "skill-magnet-menu-v4",
        "\t".join(
            (
                "__launcher__",
                "Skill Magnet",
                "launcher",
                "root",
                "Skill Magnet",
                "スキルまたはスキルパックを選び、Codex DesktopまたはClaude Code Desktopへ渡します。",
                launcher_command,
            )
        ),
    ]
    return "\n".join(lines) + "\n"


_WINDOWS_MODERN_PACKAGE_NAME = "SkillMagnet.ContextMenu"
_WINDOWS_MODERN_PACKAGE_IDENTITY = {
    "Name": _WINDOWS_MODERN_PACKAGE_NAME,
    "Publisher": "CN=Skill Magnet Local",
    "Version": "0.5.9.0",
    "ProcessorArchitecture": "x64",
}
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
_WINDOWS_NATIVE_ARTIFACTS = (
    "SkillMagnetCommand.dll",
    "SkillMagnetIdentity.exe",
)
_WINDOWS_NATIVE_BUILD_OUTPUTS = frozenset(
    {
        "ContractTest.exe",
        "ContractTest.obj",
        "SkillMagnetCommand.dll",
        "SkillMagnetCommand.exp",
        "SkillMagnetCommand.lib",
        "SkillMagnetCommand.obj",
        "SkillMagnetIdentity.exe",
        "SkillMagnetIdentity.obj",
        "SkillMagnetMenu.tsv",
        "SkillMagnetNativeSource.h",
        "SkillMagnetNativeSource.json",
    }
)
_WINDOWS_NATIVE_BUILD_MARKER = ".skill-magnet-native-build.json"
_WINDOWS_NATIVE_BUILD_MARKER_CONTRACT = "skill-magnet-native-build-workspace-v1"
_WINDOWS_NATIVE_RECOVERY_ROOT = ".skill-magnet-native-recovery"
_WINDOWS_NATIVE_RECOVERY_OWNER = "owner.json"
_WINDOWS_NATIVE_RECOVERY_LOCK = "operation.lock"
_WINDOWS_NATIVE_RECOVERY_CONTRACT = "skill-magnet-native-out-quarantine-v1"
_WINDOWS_MODERN_COM_CLSID = "13E2A9DD-4378-4F9D-A385-973C61B19E63"
_WINDOWS_MODERN_COM_CLASS = (
    _WINDOWS_MODERN_COM_CLSID,
    "SkillMagnetCommand.dll",
    "STA",
)
_WINDOWS_MODERN_APPLICATION = {
    "Id": "SkillMagnetContextMenu",
    "Executable": "SkillMagnetIdentity.exe",
    "{http://schemas.microsoft.com/appx/manifest/uap/windows10/10}RuntimeBehavior": "win32App",
    "{http://schemas.microsoft.com/appx/manifest/uap/windows10/10}TrustLevel": "mediumIL",
}
_WINDOWS_MODERN_TARGET_DEVICE_FAMILY = {
    "Name": "Windows.Desktop",
    "MinVersion": "10.0.22000.0",
    "MaxVersionTested": "10.0.26100.0",
}
_WINDOWS_MODERN_VERBS = [
    ("Directory", "SkillMagnetDirectory", _WINDOWS_MODERN_COM_CLSID),
    (r"Directory\Background", "SkillMagnetBackground", _WINDOWS_MODERN_COM_CLSID),
]
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    close(wintypes.HANDLE(handle))


def _windows_handle_identity(path: Path, *, directory: bool) -> dict[str, object]:
    """Return a volume-bound 128-bit Windows identity for one open object."""

    if sys.platform != "win32":
        observed = path.stat(follow_symlinks=False)
        return {
            "volume_serial": f"{int(observed.st_dev):016x}",
            "file_id": f"{int(observed.st_ino):032x}",
        }
    handle, identity = _open_windows_identity_handle(
        path,
        directory=directory,
        share=0x00000001 | 0x00000002 | 0x00000004,
    )
    try:
        return identity
    finally:
        _close_windows_handle(handle)


def _open_windows_identity_handle(
    path: Path, *, directory: bool, share: int
) -> tuple[int, dict[str, object]]:
    import ctypes
    from ctypes import wintypes

    class FileId128(ctypes.Structure):
        _fields_ = [("identifier", ctypes.c_ubyte * 16)]

    class FileIdInfo(ctypes.Structure):
        _fields_ = [
            ("volume_serial_number", ctypes.c_ulonglong),
            ("file_id", FileId128),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create_file(
        str(path),
        0x80000000 if not directory else 0x00000080,
        share,
        None,
        3,
        0x00200000 | (0x02000000 if directory else 0),
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = FileIdInfo()
        if not get_information(
            handle, 18, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = {
            "volume_serial": f"{int(information.volume_serial_number):016x}",
            "file_id": bytes(information.file_id.identifier).hex(),
        }
        if identity["volume_serial"] == "0" * 16 or identity["file_id"] == "0" * 32:
            raise SafetyError(f"Windows native path has no stable identity: {path}")
        return int(handle), identity
    except BaseException:
        close(handle)
        raise


def _acquire_windows_native_source_lease(native_root: Path) -> dict[str, object]:
    """Deny source mutation/rebinding for the complete native install transaction."""

    manifest = _windows_native_source_manifest(native_root)
    if sys.platform != "win32":
        return {"handles": [], "manifest": manifest}
    handles: list[int] = []
    try:
        current = _absolute_path(native_root)
        while True:
            if _is_link(current) or not current.is_dir():
                raise SafetyError(f"Windows native source path is redirected: {current}")
            handle, _ = _open_windows_identity_handle(
                current,
                directory=True,
                share=0x00000001 | 0x00000002,  # deny DELETE
            )
            handles.append(handle)
            parent = current.parent
            if parent == current:
                break
            current = parent
        for relative in _WINDOWS_NATIVE_SOURCE_INPUTS:
            path = native_root / relative
            if _is_link(path) or not path.is_file():
                raise SafetyError(f"Windows native source input is unsafe: {path}")
            handle, _ = _open_windows_identity_handle(
                path,
                directory=False,
                share=0x00000001,  # reads allowed; writes/deletes denied
            )
            handles.append(handle)
        locked_manifest = _windows_native_source_manifest(native_root)
        if locked_manifest != manifest:
            raise SafetyError("Windows native source changed while acquiring its lease")
        return {"handles": handles, "manifest": locked_manifest}
    except BaseException:
        for handle in reversed(handles):
            _close_windows_handle(handle)
        raise


def _release_windows_native_source_lease(lease: dict[str, object] | None) -> None:
    if lease is None or sys.platform != "win32":
        return
    for handle in reversed(list(lease.get("handles", []))):
        _close_windows_handle(handle)


def _windows_native_source_manifest(native_root: Path) -> dict[str, object]:
    """Return the reproducible source identity embedded into the native DLL."""

    combined = hashlib.sha256()
    inputs: list[dict[str, object]] = []
    for relative in _WINDOWS_NATIVE_SOURCE_INPUTS:
        path = native_root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SkillMagnetError(
                f"Windows native provenance input is missing: {relative}"
            ) from exc
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            normalized = raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        except UnicodeError as exc:
            raise SkillMagnetError(
                f"Windows native provenance input is not UTF-8: {relative}"
            ) from exc
        encoded_name = relative.encode("utf-8")
        combined.update(encoded_name)
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


def _windows_native_build_binding(
    native_root: Path,
    content_root: Path,
    *,
    expected_source_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    """Validate source manifest, embedded DLL identity, and packaged binaries."""

    expected = (
        expected_source_manifest
        if expected_source_manifest is not None
        else _windows_native_source_manifest(native_root)
    )
    manifest_path = content_root / "SkillMagnetNativeSource.json"
    manifest: object = None
    try:
        manifest = _strict_native_json(
            manifest_path.read_bytes(), label="Windows native source manifest"
        )
    except (OSError, SafetyError):
        pass
    source_manifest_valid = bool(
        isinstance(manifest, dict)
        and set(manifest) == {
            "schema_version",
            "contract",
            "source_tree_sha256",
            "inputs",
            "artifacts",
        }
        and all(manifest.get(key) == value for key, value in expected.items())
    )
    expected_artifacts: list[dict[str, object]] = []
    artifacts_available = True
    for relative in _WINDOWS_NATIVE_ARTIFACTS:
        path = content_root / relative
        try:
            payload = path.read_bytes()
        except OSError:
            artifacts_available = False
            break
        expected_artifacts.append(
            {
                "path": relative,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    artifact_hashes_valid = bool(
        source_manifest_valid
        and artifacts_available
        and isinstance(manifest, dict)
        and manifest.get("artifacts") == expected_artifacts
    )
    dll_source_binding_valid = False
    dll_path = content_root / "SkillMagnetCommand.dll"
    try:
        dll_payload = dll_path.read_bytes()
    except OSError:
        pass
    else:
        binding = (
            _WINDOWS_NATIVE_SOURCE_CONTRACT
            + ":"
            + str(expected["source_tree_sha256"])
        ).encode("utf-16-le")
        dll_source_binding_valid = dll_payload.count(binding) == 1
    return {
        "native_source_manifest_path": str(manifest_path),
        "native_source_tree_sha256": expected["source_tree_sha256"],
        "native_source_manifest_valid": source_manifest_valid,
        "native_artifact_hashes_valid": artifact_hashes_valid,
        "dll_native_source_binding_valid": dll_source_binding_valid,
        "native_build_binding_valid": bool(
            source_manifest_valid
            and artifact_hashes_valid
            and dll_source_binding_valid
        ),
    }


def _validate_windows_install_root_path(install_root: Path) -> Path:
    """Reject an install root whose existing path chain can redirect writes.

    ``abspath`` is deliberately lexical.  Resolving first would hide the exact
    junction/symlink that must make every install, rollback, and uninstall
    operation fail closed before package commands or recursive deletion run.
    """

    root = _absolute_path(install_root)
    if root.parent == root:
        raise SafetyError("Windows context-menu install root cannot be a filesystem root")
    current = root
    while True:
        if os.path.lexists(current):
            if _is_link(current):
                raise SafetyError(
                    "Windows context-menu install path contains a symlink or junction; "
                    f"nothing was changed: {current}"
                )
            if not current.is_dir():
                raise SafetyError(
                    "Windows context-menu install path contains a non-directory; "
                    f"nothing was changed: {current}"
                )
        parent = current.parent
        if parent == current:
            break
        current = parent
    return root


def _validate_windows_managed_tree(root: Path, *, label: str) -> None:
    """Prove a managed tree contains no symlink/junction before traversing it."""

    if not os.path.lexists(root):
        return
    if _is_link(root) or not root.is_dir():
        raise SafetyError(f"Unsafe {label}; nothing was changed: {root}")
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise SafetyError(f"Cannot verify {label}; nothing was changed: {directory}") from exc
        for entry in entries:
            path = type(root)(entry.path)
            if entry.is_symlink() or _is_link(path):
                raise SafetyError(
                    f"Unsafe {label} contains a symlink or junction; "
                    f"nothing was changed: {path}"
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif not entry.is_file(follow_symlinks=False):
                    raise SafetyError(
                        f"Unsafe {label} contains a non-file entry; "
                        f"nothing was changed: {path}"
                    )
            except OSError as exc:
                raise SafetyError(
                    f"Cannot verify {label}; nothing was changed: {path}"
                ) from exc


def _packaged_windows_native_root(native_root: Path) -> bool:
    """Return whether native sources live inside the imported wheel package."""

    packaged = _PACKAGE_ROOT / "_native" / "windows-modern-context-menu"
    return os.path.normcase(os.path.abspath(native_root)) == os.path.normcase(
        os.path.abspath(packaged)
    )


def _native_json_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_exclusive_native_record(path: Path, payload: dict[str, object]) -> bytes:
    encoded = _native_json_bytes(payload)
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise SkillMagnetError(
            f"Cannot persist Windows native recovery evidence: {path}"
        ) from exc
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise SkillMagnetError(
            f"Cannot verify Windows native recovery evidence: {path}"
        ) from exc
    if observed != encoded:
        raise SafetyError(f"Windows native recovery evidence changed: {path}")
    return encoded


def _flush_native_directory(path: Path) -> None:
    """Best-effort metadata durability for recovery-record publication."""

    if sys.platform != "win32":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create_file(
        str(path),
        0x00000080,  # FILE_READ_ATTRIBUTES
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not flush(handle):
            error = ctypes.get_last_error()
            # Some Windows filesystems do not expose directory flushing. The
            # durable evidence file remains sufficient to reconstruct a lost
            # hard-link publication on the next launch.
            if error not in {1, 5, 6, 87}:
                raise ctypes.WinError(error)
    finally:
        close(handle)


def _publish_native_record(
    directory: Path,
    final: Path,
    payload: dict[str, object],
    *,
    evidence_prefix: str,
) -> tuple[bytes, Path]:
    """Publish an immutable record via a fully-written recovery hard link."""

    encoded = _native_json_bytes(payload)
    if os.path.lexists(final):
        if _is_link(final) or not final.is_file() or final.read_bytes() != encoded:
            raise SafetyError(f"Windows native recovery record already differs: {final}")
        return encoded, final
    evidence = directory / f"{evidence_prefix}-{uuid.uuid4().hex}.json"
    _write_exclusive_native_record(evidence, payload)
    try:
        os.link(evidence, final)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SkillMagnetError(
            f"Cannot atomically publish Windows native recovery record: {final}"
        ) from exc
    try:
        if (
            _is_link(final)
            or not final.is_file()
            or final.read_bytes() != encoded
            or not os.path.samefile(evidence, final)
        ):
            raise SafetyError(f"Windows native recovery publication changed: {final}")
    except OSError as exc:
        raise SafetyError(f"Windows native recovery publication is invalid: {final}") from exc
    _flush_native_directory(directory)
    return encoded, evidence


def _strict_native_json(encoded: bytes, *, label: str) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            encoded.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SafetyError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise SafetyError(f"{label} is invalid")
    return payload


def _native_tree_snapshot(root: Path) -> dict[str, object]:
    """Hash a regular tree twice-addressably without deleting or following links."""

    _validate_windows_managed_tree(root, label="Windows native recovery tree")
    try:
        root_before = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SafetyError(f"Cannot identify Windows native recovery tree: {root}") from exc
    if not root_before.st_ino:
        raise SafetyError(f"Windows native recovery tree has no stable identity: {root}")
    records: list[dict[str, object]] = []
    total_size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if _is_link(path):
            raise SafetyError(f"Windows native recovery tree contains a link: {path}")
        try:
            before = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SafetyError(f"Cannot identify Windows native recovery entry: {path}") from exc
        if path.is_dir():
            records.append(
                {
                    "kind": "directory",
                    "path": relative,
                    "device": int(before.st_dev),
                    "inode": int(before.st_ino),
                    "mode": int(before.st_mode),
                    "link_count": int(before.st_nlink),
                    "modified_ns": int(before.st_mtime_ns),
                    "file_attributes": int(getattr(before, "st_file_attributes", 0)),
                }
            )
            continue
        if not path.is_file():
            raise SafetyError(f"Windows native recovery entry is not regular: {path}")
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
            after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SafetyError(f"Cannot hash Windows native recovery entry: {path}") from exc
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or size != after.st_size
        ):
            raise SafetyError(f"Windows native recovery entry changed while hashing: {path}")
        total_size += size
        records.append(
            {
                "kind": "file",
                "path": relative,
                "device": int(after.st_dev),
                "inode": int(after.st_ino),
                "mode": int(after.st_mode),
                "link_count": int(after.st_nlink),
                "modified_ns": int(after.st_mtime_ns),
                "file_attributes": int(getattr(after, "st_file_attributes", 0)),
                "size": size,
                "sha256": digest.hexdigest(),
            }
        )
    try:
        root_after = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SafetyError(f"Windows native recovery tree disappeared: {root}") from exc
    if (root_before.st_dev, root_before.st_ino) != (
        root_after.st_dev,
        root_after.st_ino,
    ):
        raise SafetyError(f"Windows native recovery root identity changed: {root}")
    manifest = _native_json_bytes({"entries": records})
    return {
        "root_device": int(root_after.st_dev),
        "root_inode": int(root_after.st_ino),
        "root_mode": int(root_after.st_mode),
        "root_link_count": int(root_after.st_nlink),
        "root_modified_ns": int(root_after.st_mtime_ns),
        "root_file_attributes": int(getattr(root_after, "st_file_attributes", 0)),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "entry_count": len(records),
        "total_size": total_size,
    }


def _stable_native_tree_snapshot(root: Path) -> dict[str, object]:
    first = _native_tree_snapshot(root)
    second = _native_tree_snapshot(root)
    if first != second:
        raise SafetyError(f"Windows native recovery tree is changing: {root}")
    return first


def _native_recovery_root(native_root: Path) -> Path:
    root = type(native_root)(str(_PACKAGE_ROOT.parent)) / _WINDOWS_NATIVE_RECOVERY_ROOT
    package = _absolute_path(_PACKAGE_ROOT)
    absolute = _absolute_path(root)
    if absolute == package or absolute.is_relative_to(package):
        raise SafetyError("Windows native recovery directory must be outside the package")
    return root


def _ensure_native_recovery_root(native_root: Path) -> Path:
    root = _validate_windows_install_root_path(_native_recovery_root(native_root))
    owner_payload = {
        "schema_version": 1,
        "owner": "skill-magnet",
        "contract": _WINDOWS_NATIVE_RECOVERY_CONTRACT,
    }
    owner_bytes = _native_json_bytes(owner_payload)
    if not root.exists():
        try:
            root.mkdir()
        except OSError as exc:
            raise SkillMagnetError(
                f"Cannot create Windows native recovery directory: {root}"
            ) from exc
    _validate_windows_managed_tree(root, label="Windows native recovery directory")
    owner = root / _WINDOWS_NATIVE_RECOVERY_OWNER
    if not owner.exists():
        entries = list(root.iterdir())
        unexpected = [
            path.name
            for path in entries
            if not path.is_file()
            or _is_link(path)
            or re.fullmatch(r"owner-evidence-[0-9a-f]{32}\.json", path.name)
            is None
        ]
        if unexpected:
            raise SafetyError(
                "Windows native recovery owner is missing and the directory "
                "contains unbound entries; nothing was changed: "
                + ", ".join(sorted(unexpected))
            )
        _publish_native_record(
            root,
            owner,
            owner_payload,
            evidence_prefix="owner-evidence",
        )
    try:
        observed = owner.read_bytes()
    except OSError as exc:
        raise SafetyError(f"Windows native recovery owner is missing: {root}") from exc
    if _is_link(owner) or observed != owner_bytes:
        raise SafetyError(f"Windows native recovery owner is invalid: {root}")
    return root


def _acquire_native_recovery_lock(native_root: Path) -> object:
    recovery = _ensure_native_recovery_root(native_root)
    lock_path = recovery / _WINDOWS_NATIVE_RECOVERY_LOCK
    if os.path.lexists(lock_path) and (_is_link(lock_path) or not lock_path.is_file()):
        raise SafetyError(f"Windows native recovery lock is unsafe: {lock_path}")
    lock_attempted = False
    try:
        handle = lock_path.open("a+b")
        opened = os.fstat(handle.fileno())
        current = lock_path.stat(follow_symlinks=False)
        if (
            _is_link(lock_path)
            or not lock_path.is_file()
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or opened.st_nlink != 1
            or current.st_nlink != 1
        ):
            raise SafetyError(f"Windows native recovery lock is unsafe: {lock_path}")
        handle.seek(0)
        lock_attempted = True
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_attempted = False
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            # Re-check the path-to-handle binding immediately before the only
            # mutation this function performs.
            current = lock_path.stat(follow_symlinks=False)
            if (
                _is_link(lock_path)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
                or current.st_nlink != 1
            ):
                raise SafetyError(f"Windows native recovery lock changed: {lock_path}")
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if handle.read() != b"\0":
            raise SafetyError(f"Windows native recovery lock is invalid: {lock_path}")
    except BaseException as exc:
        try:
            handle.close()
        except (OSError, UnboundLocalError):
            pass
        if isinstance(exc, (SafetyError, KeyboardInterrupt, SystemExit)):
            raise
        contention = (
            isinstance(exc, OSError)
            and lock_attempted
            and getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}
        )
        if contention:
            raise SkillMagnetError(
                "Another Windows native recovery/install operation is active; "
                "nothing was changed. Retry after it finishes."
            ) from exc
        raise SkillMagnetError(
            "Cannot open or verify the Windows native recovery lock at "
            f"{lock_path}. Check access permissions and available disk space, "
            "then retry; no recovery data was changed."
        ) from exc
    return handle


def _release_native_recovery_lock(handle: object | None) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Closing an OS-locked file handle releases the lock even if an explicit
        # unlock races process teardown.
        pass
    finally:
        handle.close()


def _native_source_identity(source: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(source))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _read_native_quarantine_journal(
    path: Path, source: Path, encoded: bytes
) -> dict[str, object]:
    try:
        payload = _strict_native_json(
            encoded, label=f"Windows native recovery journal {path}"
        )
    except SafetyError as exc:
        raise SafetyError(f"Windows native recovery journal is invalid: {path}") from exc
    expected_keys = {
        "schema_version",
        "contract",
        "nonce",
        "source_identity_sha256",
        "target_name",
        "snapshot",
    }
    nonce = payload.get("nonce") if isinstance(payload, dict) else None
    target_name = payload.get("target_name") if isinstance(payload, dict) else None
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version") != 1
        or payload.get("contract") != _WINDOWS_NATIVE_RECOVERY_CONTRACT
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or (
            path.name != f"transaction-{nonce}.json"
            and re.fullmatch(
                rf"journal-evidence-{nonce}-[0-9a-f]{{32}}\.json", path.name
            )
            is None
        )
        or target_name != f"legacy-out-{nonce}"
        or payload.get("source_identity_sha256") != _native_source_identity(source)
        or not isinstance(snapshot, dict)
        or set(snapshot)
        != {
            "root_device",
            "root_inode",
            "root_mode",
            "root_link_count",
            "root_modified_ns",
            "root_file_attributes",
            "manifest_sha256",
            "entry_count",
            "total_size",
        }
        or not isinstance(snapshot.get("root_device"), int)
        or not isinstance(snapshot.get("root_inode"), int)
        or not snapshot.get("root_inode")
        or not isinstance(snapshot.get("root_mode"), int)
        or not isinstance(snapshot.get("root_link_count"), int)
        or not isinstance(snapshot.get("root_modified_ns"), int)
        or not isinstance(snapshot.get("root_file_attributes"), int)
        or re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("manifest_sha256", "")))
        is None
        or not isinstance(snapshot.get("entry_count"), int)
        or not isinstance(snapshot.get("total_size"), int)
    ):
        raise SafetyError(f"Windows native recovery journal is unsupported: {path}")
    return payload


def _native_completion_status(path: Path, journal_bytes: bytes) -> str | None:
    if not os.path.lexists(path):
        return None
    if not path.is_file() or _is_link(path):
        raise SafetyError(f"Windows native completion record is unsafe: {path}")
    try:
        payload = _strict_native_json(
            path.read_bytes(), label=f"Windows native completion record {path}"
        )
    except OSError as exc:
        raise SafetyError(f"Windows native completion record is unreadable: {path}") from exc
    if (
        set(payload)
        != {"schema_version", "contract", "status", "journal_sha256"}
        or payload.get("schema_version") != 1
        or payload.get("contract") != _WINDOWS_NATIVE_RECOVERY_CONTRACT
        or payload.get("journal_sha256")
        != hashlib.sha256(journal_bytes).hexdigest()
        or payload.get("status")
        not in {
            "preserved_after_success",
            "restored_after_failure",
            "recovered_after_interruption",
        }
    ):
        raise SafetyError(f"Windows native completion record is invalid: {path}")
    return str(payload["status"])


def _write_native_completion_record(
    recovery: Path, nonce: str, payload: dict[str, object]
) -> None:
    """Publish completion atomically without overwriting an existing record."""

    final = recovery / f"complete-{nonce}.json"
    _publish_native_record(
        recovery,
        final,
        payload,
        evidence_prefix=f"completion-evidence-{nonce}",
    )


def _assert_native_snapshot(path: Path, expected: dict[str, object]) -> None:
    observed = _stable_native_tree_snapshot(path)
    if observed != expected:
        raise SafetyError(f"Windows native recovery snapshot changed: {path}")


def _restore_native_quarantine(record: dict[str, object]) -> None:
    source = record["source"]
    target = record["target"]
    snapshot = record["snapshot"]
    if not isinstance(source, Path) or not isinstance(target, Path) or not isinstance(snapshot, dict):
        raise SafetyError("Windows native recovery record is invalid")
    source_exists = os.path.lexists(source)
    target_exists = os.path.lexists(target)
    if source_exists and target_exists:
        raise SafetyError(
            "Windows native recovery has both source and preserved copies; "
            f"nothing was overwritten: {target}"
        )
    if source_exists:
        _assert_native_snapshot(source, snapshot)
        return
    if not target_exists:
        raise SafetyError(f"Windows native recovery copy is missing: {target}")
    _assert_native_snapshot(target, snapshot)
    try:
        os.replace(target, source)
    except OSError as exc:
        raise SkillMagnetError(
            f"Cannot restore preserved Windows native output: {target}"
        ) from exc
    _assert_native_snapshot(source, snapshot)


def _recover_incomplete_native_quarantines(native_root: Path) -> None:
    recovery = _native_recovery_root(native_root)
    if not os.path.lexists(recovery):
        return
    recovery = _ensure_native_recovery_root(native_root)
    source = native_root / "out"
    entries = list(recovery.iterdir())
    journal_evidence: dict[str, list[Path]] = {}
    for path in entries:
        match = re.fullmatch(
            r"journal-evidence-([0-9a-f]{32})-[0-9a-f]{32}\.json", path.name
        )
        if match is not None:
            if _is_link(path) or not path.is_file():
                raise SafetyError(f"Windows native journal evidence is unsafe: {path}")
            journal_evidence.setdefault(match.group(1), []).append(path)
    journals = {
        path.stem.removeprefix("transaction-"): path
        for path in entries
        if path.is_file() and re.fullmatch(r"transaction-[0-9a-f]{32}\.json", path.name)
    }
    for nonce, candidates in journal_evidence.items():
        if nonce in journals:
            continue
        target = recovery / f"legacy-out-{nonce}"
        source_exists = os.path.lexists(source)
        target_exists = os.path.lexists(target)
        if source_exists and not target_exists:
            # The canonical journal was never published. Evidence may be a
            # partial crash artifact, so it is preserved but never authorizes a
            # mutation of the intact source tree.
            continue
        if source_exists or not target_exists:
            raise SafetyError(
                "Windows native recovery has an incomplete journal and an "
                f"ambiguous tree state; nothing was changed: {nonce}"
            )
        valid: list[tuple[Path, bytes]] = []
        for evidence in candidates:
            encoded = evidence.read_bytes()
            try:
                _read_native_quarantine_journal(evidence, source, encoded)
            except SafetyError:
                continue
            valid.append((evidence, encoded))
        if len(valid) != 1:
            raise SafetyError(
                "Windows native recovery cannot reconstruct its journal; "
                f"preserved files were not changed: {target}"
            )
        evidence, encoded = valid[0]
        journal = recovery / f"transaction-{nonce}.json"
        try:
            os.link(evidence, journal)
        except OSError as exc:
            raise SkillMagnetError(
                f"Cannot restore Windows native recovery journal: {journal}"
            ) from exc
        if journal.read_bytes() != encoded or not os.path.samefile(evidence, journal):
            raise SafetyError(f"Windows native recovery journal reconstruction failed: {journal}")
        _flush_native_directory(recovery)
        journals[nonce] = journal

    allowed = {_WINDOWS_NATIVE_RECOVERY_OWNER, _WINDOWS_NATIVE_RECOVERY_LOCK}
    allowed.update(
        path.name
        for path in entries
        if re.fullmatch(r"owner-evidence-[0-9a-f]{32}\.json", path.name)
        or re.fullmatch(
            r"journal-evidence-[0-9a-f]{32}-[0-9a-f]{32}\.json", path.name
        )
    )
    for nonce, journal in journals.items():
        allowed.update(
            {
                journal.name,
                f"complete-{nonce}.json",
                f"legacy-out-{nonce}",
            }
        )
        allowed.update(
            path.name
            for path in entries
            if re.fullmatch(
                rf"completion-evidence-{nonce}-[0-9a-f]{{32}}\.json",
                path.name,
            )
        )
    unknown = sorted(path.name for path in entries if path.name not in allowed)
    if unknown:
        raise SafetyError(
            "Windows native recovery directory contains unbound entries; "
            "nothing was changed: " + ", ".join(unknown)
        )
    for nonce, journal in sorted(journals.items()):
        journal_bytes = journal.read_bytes()
        payload = _read_native_quarantine_journal(journal, source, journal_bytes)
        target = recovery / str(payload["target_name"])
        complete = recovery / f"complete-{nonce}.json"
        completion_status = _native_completion_status(complete, journal_bytes)
        if completion_status is not None:
            if completion_status == "preserved_after_success":
                if not os.path.lexists(target):
                    raise SafetyError(
                        f"Completed Windows native recovery copy is missing: {target}"
                    )
                _assert_native_snapshot(target, payload["snapshot"])
            elif os.path.lexists(target):
                raise SafetyError(
                    f"Restored Windows native recovery target still exists: {target}"
                )
            continue
        record = {
            "source": source,
            "target": target,
            "snapshot": payload["snapshot"],
            "recovery": recovery,
            "nonce": nonce,
            "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
        }
        # Every incomplete phase is authoritative: before rename the source must
        # still match, after rename the target must be restorable, and neither or
        # both existing is a fail-closed collision.
        _restore_native_quarantine(record)
        _complete_native_quarantine(
            record, status="recovered_after_interruption"
        )


def _quarantine_packaged_windows_native_output(
    native_root: Path,
) -> dict[str, object] | None:
    if not _packaged_windows_native_root(native_root):
        return None
    _recover_incomplete_native_quarantines(native_root)
    source = native_root / "out"
    if not os.path.lexists(source):
        return None
    snapshot = _stable_native_tree_snapshot(source)
    recovery = _ensure_native_recovery_root(native_root)
    if source.stat(follow_symlinks=False).st_dev != recovery.stat(follow_symlinks=False).st_dev:
        raise SafetyError("Windows native recovery must remain on the package filesystem")
    nonce = uuid.uuid4().hex
    target = recovery / f"legacy-out-{nonce}"
    journal = recovery / f"transaction-{nonce}.json"
    payload = {
        "schema_version": 1,
        "contract": _WINDOWS_NATIVE_RECOVERY_CONTRACT,
        "nonce": nonce,
        "source_identity_sha256": _native_source_identity(source),
        "target_name": target.name,
        "snapshot": snapshot,
    }
    journal_bytes, _ = _publish_native_record(
        recovery,
        journal,
        payload,
        evidence_prefix=f"journal-evidence-{nonce}",
    )
    _assert_native_snapshot(source, snapshot)
    try:
        os.replace(source, target)
    except OSError as exc:
        raise SkillMagnetError(
            f"Cannot preserve legacy Windows native output for recovery: {source}"
        ) from exc
    record = {
        "source": source,
        "target": target,
        "snapshot": snapshot,
        "recovery": recovery,
        "nonce": nonce,
        "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
    }
    try:
        _assert_native_snapshot(target, snapshot)
    except BaseException:
        _restore_native_quarantine(record)
        raise
    return record


def _complete_native_quarantine(
    record: dict[str, object], *, status: str = "preserved_after_success"
) -> None:
    recovery = record.get("recovery")
    nonce = record.get("nonce")
    journal_sha256 = record.get("journal_sha256")
    if (
        not isinstance(recovery, Path)
        or not isinstance(nonce, str)
        or not isinstance(journal_sha256, str)
        or status
        not in {
            "preserved_after_success",
            "restored_after_failure",
            "recovered_after_interruption",
        }
    ):
        raise SafetyError("Windows native recovery completion record is invalid")
    _validate_windows_managed_tree(
        recovery, label="Windows native recovery directory"
    )
    journal = recovery / f"transaction-{nonce}.json"
    try:
        journal_bytes = journal.read_bytes()
    except OSError as exc:
        raise SafetyError("Windows native recovery journal disappeared") from exc
    if hashlib.sha256(journal_bytes).hexdigest() != journal_sha256:
        raise SafetyError("Windows native recovery journal changed")
    source = record.get("source")
    target = record.get("target")
    snapshot = record.get("snapshot")
    if (
        not isinstance(source, Path)
        or not isinstance(target, Path)
        or not isinstance(snapshot, dict)
    ):
        raise SafetyError("Windows native recovery completion paths are invalid")
    _read_native_quarantine_journal(journal, source, journal_bytes)
    if status == "preserved_after_success":
        if os.path.lexists(source):
            raise SafetyError("Windows native recovery source unexpectedly reappeared")
        _assert_native_snapshot(target, snapshot)
    else:
        if os.path.lexists(target):
            raise SafetyError("Windows native recovery target still exists after restore")
        _assert_native_snapshot(source, snapshot)
    _write_native_completion_record(
        recovery,
        nonce,
        {
            "schema_version": 1,
            "contract": _WINDOWS_NATIVE_RECOVERY_CONTRACT,
            "status": status,
            "journal_sha256": journal_sha256,
        },
    )


def _create_windows_native_build_workspace(native_root: Path) -> dict[str, object]:
    raw = tempfile.mkdtemp(prefix="skill-magnet-native-build-")
    path = type(native_root)(raw)
    nonce = uuid.uuid4().hex
    stat = path.stat(follow_symlinks=False)
    if _is_link(path) or not path.is_dir() or not stat.st_ino:
        raise SafetyError(f"Windows native build workspace is unsafe: {path}")
    output = path / "out"
    output.mkdir()
    output_stat = output.stat(follow_symlinks=False)
    root_identity = _windows_handle_identity(path, directory=True)
    output_identity = _windows_handle_identity(output, directory=True)
    if root_identity["volume_serial"] != output_identity["volume_serial"]:
        raise SafetyError("Windows native build workspace crosses filesystem volumes")
    payload = {
        "schema_version": 1,
        "contract": _WINDOWS_NATIVE_BUILD_MARKER_CONTRACT,
        "nonce": nonce,
        "volume_serial": root_identity["volume_serial"],
        "root_file_id": root_identity["file_id"],
        "output_file_id": output_identity["file_id"],
    }
    marker = path / _WINDOWS_NATIVE_BUILD_MARKER
    marker_bytes = _write_exclusive_native_record(marker, payload)
    if (
        _is_link(output)
        or not output.is_dir()
        or not output_stat.st_ino
        or sorted(item.name for item in path.iterdir())
        != [_WINDOWS_NATIVE_BUILD_MARKER, "out"]
        or any(output.iterdir())
    ):
        # Never recursively delete a workspace which failed its ownership proof.
        raise SafetyError(f"Windows native build workspace was not empty: {path}")
    return {
        "path": path,
        "output": output,
        "nonce": nonce,
        "root_device": int(stat.st_dev),
        "root_inode": int(stat.st_ino),
        "root_volume_serial": root_identity["volume_serial"],
        "root_file_id": root_identity["file_id"],
        "output_device": int(output_stat.st_dev),
        "output_inode": int(output_stat.st_ino),
        "output_volume_serial": output_identity["volume_serial"],
        "output_file_id": output_identity["file_id"],
        "marker_sha256": hashlib.sha256(marker_bytes).hexdigest(),
    }


def _validate_windows_native_build_workspace(workspace: dict[str, object]) -> Path:
    path = workspace.get("path")
    if not isinstance(path, Path) or _is_link(path) or not path.is_dir():
        raise SafetyError("Windows native build workspace is missing or redirected")
    stat = path.stat(follow_symlinks=False)
    if (int(stat.st_dev), int(stat.st_ino)) != (
        workspace.get("root_device"),
        workspace.get("root_inode"),
    ):
        raise SafetyError(f"Windows native build workspace identity changed: {path}")
    marker = path / _WINDOWS_NATIVE_BUILD_MARKER
    if _is_link(marker) or not marker.is_file():
        raise SafetyError(f"Windows native build marker is missing: {path}")
    if hashlib.sha256(marker.read_bytes()).hexdigest() != workspace.get("marker_sha256"):
        raise SafetyError(f"Windows native build marker changed: {path}")
    output = workspace.get("output")
    if not isinstance(output, Path) or output != path / "out":
        raise SafetyError("Windows native build output binding is invalid")
    if _is_link(output) or not output.is_dir():
        raise SafetyError(f"Windows native build output is missing or redirected: {output}")
    output_stat = output.stat(follow_symlinks=False)
    if (int(output_stat.st_dev), int(output_stat.st_ino)) != (
        workspace.get("output_device"),
        workspace.get("output_inode"),
    ):
        raise SafetyError(f"Windows native build output identity changed: {output}")
    root_identity = _windows_handle_identity(path, directory=True)
    output_identity = _windows_handle_identity(output, directory=True)
    if (
        root_identity.get("volume_serial") != workspace.get("root_volume_serial")
        or root_identity.get("file_id") != workspace.get("root_file_id")
        or output_identity.get("volume_serial") != workspace.get("output_volume_serial")
        or output_identity.get("file_id") != workspace.get("output_file_id")
        or root_identity.get("volume_serial") != output_identity.get("volume_serial")
    ):
        raise SafetyError(f"Windows native build volume/file identity changed: {path}")
    root_entries = sorted(item.name for item in path.iterdir())
    if root_entries != [_WINDOWS_NATIVE_BUILD_MARKER, "out"]:
        raise SafetyError(
            "Windows native build workspace contains unowned entries; nothing was "
            "deleted: " + ", ".join(root_entries)
        )
    unexpected = sorted(
        item.name
        for item in output.iterdir()
        if item.name not in _WINDOWS_NATIVE_BUILD_OUTPUTS
        or not item.is_file()
        or _is_link(item)
    )
    if unexpected:
        raise SafetyError(
            "Windows native build workspace contains unowned entries; nothing was "
            "deleted: " + ", ".join(unexpected)
        )
    return output


def _native_cleanup_identity(path: Path, *, directory: bool) -> dict[str, object]:
    if _is_link(path) or (path.is_dir() if directory else path.is_file()) is False:
        raise SafetyError(f"Windows native build cleanup entry is unsafe: {path}")
    stat = path.stat(follow_symlinks=False)
    if not stat.st_ino or stat.st_nlink != 1:
        raise SafetyError(f"Windows native build cleanup entry is not unique: {path}")
    identity: dict[str, object] = {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "mode": int(stat.st_mode),
        "link_count": int(stat.st_nlink),
        "file_attributes": int(getattr(stat, "st_file_attributes", 0)),
    }
    if not directory:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        after = path.stat(follow_symlinks=False)
        if (
            stat.st_dev != after.st_dev
            or stat.st_ino != after.st_ino
            or stat.st_size != after.st_size
            or stat.st_mtime_ns != after.st_mtime_ns
            or size != after.st_size
        ):
            raise SafetyError(f"Windows native build cleanup entry changed: {path}")
        identity.update(
            {
                "size": size,
                "modified_ns": int(after.st_mtime_ns),
                "sha256": digest.hexdigest(),
            }
        )
    return identity


def _capture_windows_native_cleanup_identity(
    workspace: dict[str, object],
) -> dict[str, object]:
    output = _validate_windows_native_build_workspace(workspace)
    root = workspace.get("path")
    if not isinstance(root, Path):
        raise SafetyError("Windows native build cleanup record is invalid")
    marker = root / _WINDOWS_NATIVE_BUILD_MARKER

    def capture() -> dict[str, object]:
        return {
            "root": _native_cleanup_identity(root, directory=True),
            "output": _native_cleanup_identity(output, directory=True),
            "marker": _native_cleanup_identity(marker, directory=False),
            "files": {
                path.name: _native_cleanup_identity(path, directory=False)
                for path in sorted(output.iterdir(), key=lambda item: item.name)
            },
        }

    first = capture()
    second = capture()
    if first != second:
        raise SafetyError("Windows native build cleanup identities are changing")
    workspace["cleanup_identity"] = first
    return first


def _identity_matches_stat(expected: dict[str, object], observed: os.stat_result) -> bool:
    return (
        int(observed.st_dev) == expected.get("device")
        and int(observed.st_ino) == expected.get("inode")
        # Windows path stat synthesizes executable permission bits from a file
        # suffix while handle stat cannot; compare the object kind, not those
        # name-derived bits.
        and int(observed.st_mode) & 0o170000
        == int(expected.get("mode", 0)) & 0o170000
        and int(observed.st_nlink) == expected.get("link_count") == 1
        and int(getattr(observed, "st_file_attributes", 0))
        == expected.get("file_attributes")
        and (
            "size" not in expected
            or (
                int(observed.st_size) == expected.get("size")
                and int(observed.st_mtime_ns) == expected.get("modified_ns")
            )
        )
    )


def _delete_windows_native_path_by_handle(
    path: Path, expected: dict[str, object], *, directory: bool
) -> None:
    """Delete only the object proven by its open Windows handle, never its pathname."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    handle = create_file(
        str(path),
        0x00010000 | 0x00000080 | (0 if directory else 0x80000000),
        # Files are opened without FILE_SHARE_WRITE, so their bytes cannot
        # change between the handle-bound digest and disposition operation.
        0x00000001 | (0x00000002 if directory else 0) | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x00200000 | (0x02000000 if directory else 0),
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = -1
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        observed = os.fstat(descriptor)
        if not _identity_matches_stat(expected, observed):
            raise SafetyError(
                f"Windows native build cleanup identity changed: {path}"
            )
        if not directory:
            digest = hashlib.sha256()
            size = 0
            os.lseek(descriptor, 0, os.SEEK_SET)
            while chunk := os.read(descriptor, 1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                not _identity_matches_stat(expected, after)
                or size != expected.get("size")
                or digest.hexdigest() != expected.get("sha256")
            ):
                raise SafetyError(
                    f"Windows native build cleanup content changed: {path}"
                )
        delete = ctypes.c_ubyte(1)
        if not set_information(
            wintypes.HANDLE(msvcrt.get_osfhandle(descriptor)),
            4,  # FileDispositionInfo
            ctypes.byref(delete),
            ctypes.sizeof(delete),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        else:
            kernel32.CloseHandle(handle)


def _delete_posix_native_workspace(
    retired: Path, identity: dict[str, object]
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(retired.parent, flags)
    root_fd = -1
    output_fd = -1
    try:
        root_fd = os.open(retired.name, flags, dir_fd=parent_fd)
        if not _identity_matches_stat(identity["root"], os.fstat(root_fd)):
            raise SafetyError("Windows native build cleanup root identity changed")
        output_fd = os.open("out", flags, dir_fd=root_fd)
        if not _identity_matches_stat(identity["output"], os.fstat(output_fd)):
            raise SafetyError("Windows native build cleanup output identity changed")
        for name, expected in identity["files"].items():
            observed = os.stat(name, dir_fd=output_fd, follow_symlinks=False)
            if not _identity_matches_stat(expected, observed):
                raise SafetyError(
                    f"Windows native build cleanup file identity changed: {name}"
                )
            os.unlink(name, dir_fd=output_fd)
        os.close(output_fd)
        output_fd = -1
        os.rmdir("out", dir_fd=root_fd)
        marker_stat = os.stat(
            _WINDOWS_NATIVE_BUILD_MARKER, dir_fd=root_fd, follow_symlinks=False
        )
        if not _identity_matches_stat(identity["marker"], marker_stat):
            raise SafetyError("Windows native build cleanup marker identity changed")
        os.unlink(_WINDOWS_NATIVE_BUILD_MARKER, dir_fd=root_fd)
        os.close(root_fd)
        root_fd = -1
        os.rmdir(retired.name, dir_fd=parent_fd)
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if root_fd >= 0:
            os.close(root_fd)
        os.close(parent_fd)


def _cleanup_windows_native_build_workspace(workspace: dict[str, object]) -> None:
    identity = workspace.get("cleanup_identity")
    if not isinstance(identity, dict):
        identity = _capture_windows_native_cleanup_identity(workspace)
    path = workspace.get("path")
    if not isinstance(path, Path):
        raise SafetyError("Windows native build cleanup record is invalid")
    retired = path.with_name(path.name + ".cleanup-" + str(workspace["nonce"]))
    if os.path.lexists(retired):
        raise SafetyError(f"Windows native build cleanup target already exists: {retired}")
    try:
        os.replace(path, retired)
    except OSError as exc:
        raise SkillMagnetError(f"Cannot isolate Windows native build cleanup: {path}") from exc
    moved = {**workspace, "path": retired, "output": retired / "out"}
    try:
        _validate_windows_native_build_workspace(moved)
        if sys.platform == "win32":
            files = identity.get("files")
            if not isinstance(files, dict):
                raise SafetyError("Windows native build cleanup file identities are invalid")
            for name, expected in files.items():
                _delete_windows_native_path_by_handle(
                    retired / "out" / name, expected, directory=False
                )
            _delete_windows_native_path_by_handle(
                retired / "out", identity["output"], directory=True
            )
            _delete_windows_native_path_by_handle(
                retired / _WINDOWS_NATIVE_BUILD_MARKER,
                identity["marker"],
                directory=False,
            )
            _delete_windows_native_path_by_handle(
                retired, identity["root"], directory=True
            )
        else:
            _delete_posix_native_workspace(retired, identity)
    except BaseException as exc:
        raise SkillMagnetError(
            "Windows native build cleanup did not complete. The isolated build "
            f"directory was preserved for recovery: {retired}"
        ) from exc


def _windows_modern_paths(install_root: Path | None = None) -> tuple[Path, Path, Path]:
    packaged_native = _PACKAGE_ROOT / "_native" / "windows-modern-context-menu"
    source_native = (
        _PACKAGE_ROOT.parents[1] / "native" / "windows-modern-context-menu"
    )
    native_root = source_native if source_native.is_dir() else packaged_native
    if install_root is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise SkillMagnetError("LOCALAPPDATA is required for modern context-menu installation")
        install_root = type(_PACKAGE_ROOT)(local_app_data) / "SkillMagnet" / "ContextMenu"
    return (
        native_root,
        _validate_windows_install_root_path(install_root),
        native_root / "package.ps1",
    )


def _powershell_executable() -> str:
    # The desktop host already supplies pwsh with a coherent module path. Starting
    # Windows PowerShell from that environment can mix PS7 type data into PS5 and
    # omit the Certificate provider.
    return shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"


def _package_action(
    action: str,
    script: Path,
    *,
    install_root: Path,
    run: object,
) -> dict[str, object]:
    command = [
        _powershell_executable(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Action",
        action,
    ]
    if action in {"install", "cleanup-certificate"}:
        if action == "install":
            command.extend(["-Manifest", str(install_root / "AppxManifest.xml")])
        command.extend(
            [
                "-ExternalLocation",
                str(install_root),
            ]
        )
    result = run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown package error").strip()
        raise SkillMagnetError(f"Windows modern context-menu {action} failed: {detail}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SkillMagnetError("Windows package command returned invalid status") from exc


def _windows_registry_root_present(root: str, *, run: object) -> bool:
    """Prove whether one owned root exists without treating every exit 1 as absent."""

    if run is subprocess.run and os.name == "nt":
        import winreg

        prefix, separator, subkey = root.partition("\\")
        if not separator or prefix not in {"HKCU", "HKEY_CURRENT_USER"}:
            raise SkillMagnetError(f"Unsupported Windows registry root: {root}")
        try:
            handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise SkillMagnetError(
                f"Cannot inspect Windows context-menu root {root}: {exc}"
            ) from exc
        else:
            winreg.CloseKey(handle)
            return True

    result = run(["reg", "query", root], capture_output=True, text=True)
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or "").strip()
    known_absence = any(
        marker in detail.casefold()
        for marker in (
            "unable to find",
            "cannot find",
            "not found",
            "見つかりません",
            "__registry_key_not_found__",
        )
    )
    if result.returncode == 1 and known_absence:
        return False
    raise SkillMagnetError(
        f"Cannot inspect Windows context-menu root {root}: "
        + (detail or f"reg query exited {result.returncode} without an absence reason")
    )


def _windows_owned_registry_roots_present(*, run: object) -> list[str]:
    """Return product-owned classic roots and fail when absence is not provable."""

    return [
        root
        for root in _windows_owned_menu_roots()
        if _windows_registry_root_present(root, run=run)
    ]


def windows_modern_context_menu_status(
    *,
    install_root: Path | None = None,
    config: Path | None = None,
    run: object = subprocess.run,
    require_exclusive_entry: bool = True,
) -> dict[str, object]:
    native_root, root, script = _windows_modern_paths(install_root)
    status = _package_action("status", script, install_root=root, run=run)
    classic_owned_roots_present = _windows_owned_registry_roots_present(run=run)
    package_registered = bool(status.get("installed"))
    same_name_package_count = int(
        status.get("same_name_package_count", 1 if package_registered else 0)
    )
    expected_identity_match_count = int(
        status.get("expected_identity_match_count", 1 if package_registered else 0)
    )
    unexpected_same_name_package_count = int(
        status.get(
            "unexpected_same_name_package_count",
            max(0, same_name_package_count - expected_identity_match_count),
        )
    )
    registered_identity_matches = bool(
        package_registered
        and same_name_package_count == 1
        and expected_identity_match_count == 1
        and unexpected_same_name_package_count == 0
        and status.get("name") == _WINDOWS_MODERN_PACKAGE_IDENTITY["Name"]
        and status.get("version") == _WINDOWS_MODERN_PACKAGE_IDENTITY["Version"]
        and str(status.get("architecture", "")).casefold()
        == _WINDOWS_MODERN_PACKAGE_IDENTITY["ProcessorArchitecture"].casefold()
        and status.get("publisher") == _WINDOWS_MODERN_PACKAGE_IDENTITY["Publisher"]
    )
    package_location = status.get("install_location")
    # Preserve the concrete path flavour supplied by the caller.  Tests exercise
    # the Windows contract on macOS by patching ``os.name``; constructing a new
    # pathlib.Path while that patch is active would incorrectly try to create a
    # WindowsPath on a POSIX host.
    path_type = type(root)
    content_root = (
        path_type(str(package_location))
        if package_registered and package_location
        else root
    )
    manifest = content_root / "AppxManifest.xml"
    identity_matches = False
    com_identity_matches = False
    dependency_matches = False
    capability_matches = False
    manifest_contexts: list[str] = []
    if manifest.is_file():
        try:
            document = ET.parse(manifest).getroot()
            namespaces = {
                "foundation": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
                "com": "http://schemas.microsoft.com/appx/manifest/com/windows10",
                "desktop4": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/4",
                "desktop5": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/5",
                "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
            }
            identity = document.find("foundation:Identity", namespaces)
            identity_matches = bool(
                identity is not None
                and dict(identity.attrib) == _WINDOWS_MODERN_PACKAGE_IDENTITY
            )
            target_families = document.findall(
                "foundation:Dependencies/foundation:TargetDeviceFamily", namespaces
            )
            dependency_matches = bool(
                len(target_families) == 1
                and all(
                    target_families[0].get(key) == value
                    for key, value in _WINDOWS_MODERN_TARGET_DEVICE_FAMILY.items()
                )
                and set(target_families[0].attrib)
                == set(_WINDOWS_MODERN_TARGET_DEVICE_FAMILY)
            )
            capabilities = document.find("foundation:Capabilities", namespaces)
            capability_contract = (
                [
                    (item.tag, tuple(sorted(item.attrib.items())))
                    for item in list(capabilities)
                ]
                if capabilities is not None
                else []
            )
            capability_matches = capability_contract == [
                (
                    "{http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities}Capability",
                    (("Name", "runFullTrust"),),
                )
            ]
            applications = document.findall(
                "foundation:Applications/foundation:Application", namespaces
            )
            application = applications[0] if len(applications) == 1 else None
            application_matches = bool(
                application is not None
                and dict(application.attrib) == _WINDOWS_MODERN_APPLICATION
            )
            extensions = (
                application.find("foundation:Extensions", namespaces)
                if application is not None
                else None
            )
            com_extensions = (
                extensions.findall("com:Extension", namespaces)
                if extensions is not None
                else []
            )
            desktop_extensions = (
                extensions.findall("desktop4:Extension", namespaces)
                if extensions is not None
                else []
            )
            com_extension = com_extensions[0] if len(com_extensions) == 1 else None
            desktop_extension = (
                desktop_extensions[0] if len(desktop_extensions) == 1 else None
            )
            extension_contract = (
                [(item.tag, tuple(sorted(item.attrib.items()))) for item in list(extensions)]
                if extensions is not None
                else []
            )
            expected_extension_contract = [
                (
                    "{http://schemas.microsoft.com/appx/manifest/com/windows10}Extension",
                    (("Category", "windows.comServer"),),
                ),
                (
                    "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/4}Extension",
                    (("Category", "windows.fileExplorerContextMenus"),),
                ),
            ]
            com_servers = (
                com_extension.findall("com:ComServer", namespaces)
                if com_extension is not None
                else []
            )
            surrogates = (
                com_servers[0].findall("com:SurrogateServer", namespaces)
                if len(com_servers) == 1
                else []
            )
            surrogate = surrogates[0] if len(surrogates) == 1 else None
            classes = (
                surrogate.findall("com:Class", namespaces)
                if surrogate is not None
                else []
            )
            menu_containers = (
                desktop_extension.findall(
                    "desktop4:FileExplorerContextMenus", namespaces
                )
                if desktop_extension is not None
                else []
            )
            item_types = (
                menu_containers[0].findall("desktop5:ItemType", namespaces)
                if len(menu_containers) == 1
                else []
            )
            manifest_contexts = [
                item.get("Type", "") for item in item_types if item.get("Type")
            ]
            class_contract = [dict(item.attrib) for item in classes]
            item_contract = [
                (
                    dict(item.attrib),
                    [
                        dict(verb.attrib)
                        for verb in item.findall("desktop5:Verb", namespaces)
                    ],
                    [verb.tag for verb in list(item)],
                )
                for item in item_types
            ]
            expected_item_contract = [
                (
                    {"Type": item_type},
                    [{"Id": verb_id, "Clsid": clsid}],
                    ["{http://schemas.microsoft.com/appx/manifest/desktop/windows10/5}Verb"],
                )
                for item_type, verb_id, clsid in _WINDOWS_MODERN_VERBS
            ]
            com_server_structure_matches = bool(
                com_extension is not None
                and len(com_servers) == 1
                and dict(com_servers[0].attrib) == {}
                and [item.tag for item in list(com_extension)]
                == ["{http://schemas.microsoft.com/appx/manifest/com/windows10}ComServer"]
                and [item.tag for item in list(com_servers[0])]
                == ["{http://schemas.microsoft.com/appx/manifest/com/windows10}SurrogateServer"]
            )
            surrogate_matches = bool(
                surrogate is not None
                and dict(surrogate.attrib)
                == {
                    "AppId": _WINDOWS_MODERN_COM_CLSID,
                    "DisplayName": "Skill Magnet commands",
                }
                and [item.tag for item in list(surrogate)]
                == ["{http://schemas.microsoft.com/appx/manifest/com/windows10}Class"]
            )
            menu_structure_matches = bool(
                desktop_extension is not None
                and len(menu_containers) == 1
                and dict(menu_containers[0].attrib) == {}
                and [item.tag for item in list(desktop_extension)]
                == [
                    "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/4}FileExplorerContextMenus"
                ]
                and [item.tag for item in list(menu_containers[0])]
                == [
                    "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/5}ItemType",
                    "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/5}ItemType",
                ]
            )
            com_identity_matches = bool(
                application_matches
                and dependency_matches
                and capability_matches
                and extension_contract == expected_extension_contract
                and com_extension is not None
                and com_extension.get("Category") == "windows.comServer"
                and desktop_extension is not None
                and desktop_extension.get("Category")
                == "windows.fileExplorerContextMenus"
                and com_server_structure_matches
                and surrogate_matches
                and class_contract
                == [
                    {
                        "Id": _WINDOWS_MODERN_COM_CLASS[0],
                        "Path": _WINDOWS_MODERN_COM_CLASS[1],
                        "ThreadingModel": _WINDOWS_MODERN_COM_CLASS[2],
                    }
                ]
                and menu_structure_matches
                and item_contract == expected_item_contract
            )
        except (ET.ParseError, OSError):
            pass
    expected_contexts = ["Directory", r"Directory\Background"]
    identity_anchor_exists = (content_root / "SkillMagnetIdentity.exe").is_file()
    deprecated_launcher_exists = (content_root / "SkillMagnetLauncher.exe").exists()
    dll_exists = (content_root / "SkillMagnetCommand.dll").is_file()
    menu_manifest_exists = (content_root / "SkillMagnetMenu.tsv").is_file()
    try:
        native_build_binding = _windows_native_build_binding(native_root, content_root)
    except SkillMagnetError:
        native_build_binding = {
            "native_source_manifest_path": str(
                content_root / "SkillMagnetNativeSource.json"
            ),
            "native_source_tree_sha256": None,
            "native_source_manifest_valid": False,
            "native_artifact_hashes_valid": False,
            "dll_native_source_binding_valid": False,
            "native_build_binding_valid": False,
        }
    menu_leaf_count = 0
    menu_action_count = 0
    root_launcher_entry_count = 0
    library_manager_entry_count = 0
    register_folder_entry_count = 0
    menu_selection_kinds: list[str] = []
    configured_selection_count: int | None = None
    configured_selection_kinds: list[str] = []
    menu_contract_valid = False
    menu_contract_matches_config: bool | None = None
    command_target: Path | None = None
    command_target_exists = False
    command_target_signature_valid = False
    self_signed_launcher_referenced = False
    if menu_manifest_exists:
        try:
            menu_text = (content_root / "SkillMagnetMenu.tsv").read_text(encoding="utf-8")
            lines = menu_text.splitlines()
            records = [line.split("\t") for line in lines[1:] if line]
            menu_action_count = len(records)
            menu_leaf_count = sum(
                1
                for record in records
                if len(record) == 7 and record[2] in {"package", "skill"}
            )
            library_manager_entry_count = sum(
                1
                for record in records
                if len(record) == 7 and record[2] == "manager"
            )
            register_folder_entry_count = sum(
                1
                for record in records
                if len(record) == 7 and record[2] == "register"
            )
            root_launcher_entry_count = sum(
                1
                for record in records
                if len(record) == 7 and record[2] == "launcher"
            )
            menu_selection_kinds = [
                record[2]
                for record in records
                if len(record) == 7 and record[2] in {"package", "skill"}
            ]
            menu_contract_valid = bool(
                lines
                and lines[0] == "skill-magnet-menu-v4"
                and len(records) == 1
                and root_launcher_entry_count == 1
                and library_manager_entry_count == 0
                and register_folder_entry_count == 0
                and all(
                    len(record) == 7
                    and record[0] == "__launcher__"
                    and record[1] == "Skill Magnet"
                    and record[2] == "launcher"
                    and record[3] == "root"
                    and record[4] == "Skill Magnet"
                    and bool(record[5])
                    and record[6].count(WINDOWS_MODERN_PROJECT_MARKER) == 1
                    for record in records
                )
            )
            if config is not None:
                menu_contract_matches_config = (
                    menu_text == render_windows_modern_menu_manifest(config)
                )
                configured = Config.load(config)
                configured_selection_kinds = []
                for pack in configured.packs.values():
                    configured_selection_kinds.extend(
                        [pack.selection_kind]
                        * (1 if pack.selection_kind == "package" else len(pack.skills))
                    )
                configured_selection_count = len(configured_selection_kinds)
                launcher = windows_root_launcher_command_argv(
                    config, WINDOWS_MODERN_PROJECT_MARKER
                )
                if launcher:
                    command_target = path_type(launcher[0])
                    command_target_exists = command_target.is_file()
                    self_signed_launcher_referenced = (
                        command_target.name.casefold() == "skillmagnetlauncher.exe"
                    )
                    if command_target_exists:
                        if sys.platform != "win32":
                            command_target_signature_valid = True
                        else:
                            encoded_target = base64.b64encode(
                                str(command_target).encode("utf-8")
                            ).decode("ascii")
                            signature = subprocess.run(
                                [
                                    _powershell_executable(),
                                    "-NoProfile",
                                    "-NonInteractive",
                                    "-Command",
                                    "$p=[Text.Encoding]::UTF8.GetString("
                                    "[Convert]::FromBase64String('"
                                    + encoded_target
                                    + "'));(Get-AuthenticodeSignature "
                                    "-LiteralPath $p).Status.ToString()",
                                ],
                                capture_output=True,
                                text=True,
                                errors="replace",
                            )
                            command_target_signature_valid = bool(
                                signature.returncode == 0
                                and signature.stdout.strip() == "Valid"
                            )
        except (OSError, UnicodeError, SkillMagnetError):
            menu_contract_valid = False
    usable_installed_state = bool(
        package_registered
        and registered_identity_matches
        and identity_matches
        and dependency_matches
        and capability_matches
        and com_identity_matches
        and manifest_contexts == expected_contexts
        and identity_anchor_exists
        and not deprecated_launcher_exists
        and command_target_exists
        and command_target_signature_valid
        and not self_signed_launcher_referenced
        and dll_exists
        and native_build_binding["native_build_binding_valid"]
        and menu_manifest_exists
        and menu_contract_valid
        and menu_contract_matches_config is not False
        and (not require_exclusive_entry or not classic_owned_roots_present)
    )
    status.update(
        {
            "platform": "windows",
            "integration": "windows_11_modern_context_menu",
            "external_location": str(root),
            "package_content_location": str(content_root),
            "package_registered": package_registered,
            "same_name_package_count": same_name_package_count,
            "expected_identity_match_count": expected_identity_match_count,
            "unexpected_same_name_package_count": unexpected_same_name_package_count,
            "unexpected_same_name_package_full_names": list(
                status.get("unexpected_same_name_package_full_names", [])
            ),
            "same_name_packages": list(status.get("same_name_packages", [])),
            "registered_identity_matches": registered_identity_matches,
            "classic_owned_roots_present": classic_owned_roots_present,
            "exclusive_visible_entry": not classic_owned_roots_present,
            "identity_matches": identity_matches,
            "dependency_matches": dependency_matches,
            "capability_matches": capability_matches,
            "com_identity_matches": com_identity_matches,
            "identity_anchor_exists": identity_anchor_exists,
            "deprecated_launcher_exists": deprecated_launcher_exists,
            "command_target": str(command_target) if command_target else None,
            "command_target_exists": command_target_exists,
            "command_target_signature_valid": command_target_signature_valid,
            "self_signed_launcher_referenced": self_signed_launcher_referenced,
            "dll_exists": dll_exists,
            **native_build_binding,
            "menu_manifest_exists": menu_manifest_exists,
            "menu_contract_valid": menu_contract_valid,
            "menu_contract_matches_config": menu_contract_matches_config,
            "menu_leaf_count": menu_leaf_count,
            "menu_action_count": menu_action_count,
            "library_manager_entry_count": library_manager_entry_count,
            "register_folder_entry_count": register_folder_entry_count,
            "root_launcher_entry_count": root_launcher_entry_count,
            "menu_selection_kinds": menu_selection_kinds,
            "configured_selection_count": configured_selection_count,
            "configured_selection_kinds": configured_selection_kinds,
            "manifest_contexts": manifest_contexts,
            "contexts": expected_contexts,
            "usable_installed_state": usable_installed_state,
        }
    )
    return status


def install_windows_modern_context_menu(
    config: Path,
    *,
    install_root: Path | None = None,
    run: object = subprocess.run,
    build: bool = True,
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows modern context menu can only be installed on Windows")
    native_root, root, script = _windows_modern_paths(install_root)
    source_lease: dict[str, object] | None = None
    recovery_lock: object | None = None
    quarantine: dict[str, object] | None = None
    workspace: dict[str, object] | None = None
    output = native_root / "out"
    try:
        source_lease = _acquire_windows_native_source_lease(native_root)
        _validate_windows_managed_tree(root, label="Windows context-menu install tree")
        if _packaged_windows_native_root(native_root) and not build:
            raise SkillMagnetError(
                "An installed wheel cannot reuse native build output from its package "
                "directory; rerun with native build enabled."
            )
        recovery_lock = (
            _acquire_native_recovery_lock(native_root)
            if build and _packaged_windows_native_root(native_root)
            else None
        )
        if build:
            quarantine = _quarantine_packaged_windows_native_output(native_root)
        if build:
            workspace = _create_windows_native_build_workspace(native_root)
            output_value = workspace.get("output")
            if not isinstance(output_value, Path):
                raise SafetyError("Windows native build workspace has no bound output")
            output = output_value
            build_command = [
                _powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(native_root / "build.ps1"),
                "-OutDir",
                str(output),
                "-BuildNonce",
                str(workspace["nonce"]),
                "-MarkerSha256",
                str(workspace["marker_sha256"]),
            ]
            if os.environ.get("SKILL_MAGNET_SKIP_NATIVE_CONTRACT_TEST") == "1":
                build_command.append("-SkipContractTest")
            build_result = run(
                build_command,
                capture_output=True,
                text=True,
                errors="replace",
            )
            if build_result.returncode != 0:
                detail = (
                    build_result.stderr
                    or build_result.stdout
                    or "unknown build error"
                ).strip()
                raise SkillMagnetError(
                    f"Windows modern context-menu build failed: {detail}"
                )
            _validate_windows_native_build_workspace(workspace)
            _capture_windows_native_cleanup_identity(workspace)
        required = (
            output / "SkillMagnetCommand.dll",
            output / "SkillMagnetIdentity.exe",
            output / "SkillMagnetNativeSource.json",
        )
        if not all(path.is_file() for path in required):
            raise SkillMagnetError("Windows modern context-menu build outputs are missing")
        expected_source_manifest = source_lease.get("manifest")
        if not isinstance(expected_source_manifest, dict):
            raise SafetyError("Windows native source lease is invalid")
        output_binding = _windows_native_build_binding(
            native_root,
            output,
            expected_source_manifest=expected_source_manifest,
        )
        if not output_binding["native_build_binding_valid"]:
            raise SkillMagnetError(
                "Windows native build does not match this release source; "
                "nothing was registered. Rebuild the native context menu."
            )

        root.mkdir(parents=True, exist_ok=True)
        # Remove the 0.3.0 process adapter before registering the new contract.
        # Smart App Control can block that self-signed executable with error 4551.
        (root / "SkillMagnetLauncher.exe").unlink(missing_ok=True)
        shutil.copy2(required[0], root / required[0].name)
        shutil.copy2(required[1], root / required[1].name)
        shutil.copy2(required[2], root / required[2].name)
        shutil.copy2(native_root / "AppxManifest.xml", root / "AppxManifest.xml")
        (root / "SkillMagnetMenu.tsv").write_text(
            render_windows_modern_menu_manifest(config), encoding="utf-8", newline="\n"
        )
        assets = root / "Assets"
        assets.mkdir(exist_ok=True)
        for name in ("StoreLogo.png", "Square150x150Logo.png", "Square44x44Logo.png"):
            (assets / name).write_bytes(_TRANSPARENT_PNG)

        if build:
            package_build = run(
                [
                    _powershell_executable(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(native_root / "build-package.ps1"),
                    "-ExternalLocation",
                    str(root),
                ],
                capture_output=True,
                text=True,
                errors="replace",
            )
            if package_build.returncode != 0:
                detail = (
                    package_build.stderr
                    or package_build.stdout
                    or "unknown package build error"
                ).strip()
                raise SkillMagnetError(
                    f"Windows signed identity package build failed: {detail}"
                )

        install_status = _package_action("install", script, install_root=root, run=run)
        if not install_status.get("installed"):
            raise SkillMagnetError("Windows modern context-menu package did not register")
        status = windows_modern_context_menu_status(
            install_root=root,
            config=config,
            run=run,
            require_exclusive_entry=False,
        )
        if not status.get("usable_installed_state"):
            raise SkillMagnetError("Windows modern context-menu installed state is incomplete")
        if _windows_native_source_manifest(native_root) != expected_source_manifest:
            raise SafetyError("Windows native source changed during package installation")
        status.update(
            {
                "platform": "windows",
                "integration": "windows_11_modern_context_menu",
                "external_location": str(root),
                "contexts": ["Directory", r"Directory\Background"],
                "reinstall_required_after_pack_change": False,
                "packaged_native_build_residue_removed": False,
                "packaged_native_build_residue_preserved": quarantine is not None,
                "packaged_native_build_recovery_id": (
                    quarantine.get("nonce") if quarantine is not None else None
                ),
                "packaged_native_build_recovery_directory": (
                    str(quarantine["recovery"])
                    if quarantine is not None
                    else None
                ),
                # status is intentionally read back after registration, but that
                # command cannot reconstruct which historical trust entries the
                # install transaction removed. Preserve the transaction evidence.
                "legacy_certificate_thumbprints_removed": list(
                    install_status.get("legacy_certificate_thumbprints_removed", [])
                ),
            }
        )
        if workspace is not None:
            completed_workspace = workspace
            workspace = None
            _cleanup_windows_native_build_workspace(completed_workspace)
        if quarantine is not None:
            _complete_native_quarantine(quarantine)
        return status
    except BaseException as original:
        recovery_failures: list[str] = []
        if workspace is not None:
            failed_workspace = workspace
            workspace = None
            try:
                _cleanup_windows_native_build_workspace(failed_workspace)
            except BaseException as exc:
                recovery_failures.append(f"temporary build cleanup: {exc}")
        if quarantine is not None:
            try:
                _restore_native_quarantine(quarantine)
                _complete_native_quarantine(
                    quarantine, status="restored_after_failure"
                )
            except BaseException as exc:
                recovery_failures.append(f"legacy output restore: {exc}")
        if recovery_failures:
            raise SkillMagnetError(
                "Windows context-menu install failed and automatic recovery did not "
                "complete. Recovery evidence was preserved; retry after resolving: "
                + "; ".join(recovery_failures)
            ) from original
        raise
    finally:
        _release_native_recovery_lock(recovery_lock)
        _release_windows_native_source_lease(source_lease)


def uninstall_windows_modern_context_menu(
    *,
    install_root: Path | None = None,
    run: object = subprocess.run,
    cleanup_certificates: bool = True,
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows modern context menu can only be removed on Windows")
    _, root, script = _windows_modern_paths(install_root)
    _validate_windows_managed_tree(root, label="Windows context-menu install tree")
    status = _package_action("uninstall", script, install_root=root, run=run)
    if status.get("installed"):
        raise SkillMagnetError("Windows modern context-menu package remains registered")
    if root.exists() and cleanup_certificates:
        _package_action("cleanup-certificate", script, install_root=root, run=run)
        shutil.rmtree(root)
    return {
        "removed": True,
        "platform": "windows",
        "integration": "windows_11_modern_context_menu",
        "external_location": str(root),
    }


def _windows_context_backup_root(install_root: Path) -> Path:
    return install_root.with_name(install_root.name + ".rollback")


def _windows_rotation_paths(backup: Path) -> tuple[Path, Path, Path]:
    return (
        backup.with_name(backup.name + ".update"),
        backup.with_name(backup.name + ".rotation-old"),
        backup.with_name(backup.name + ".rotation.json"),
    )


def _write_windows_rotation_marker(marker: Path) -> None:
    temporary = marker.with_name(marker.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "operation": "promote-update"}) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, marker)


def _recover_windows_rollback_rotation(backup: Path) -> bool:
    """Finish only a validated rollback-point promotion interrupted by a crash."""
    update, retired, marker = _windows_rotation_paths(backup)
    parent = _absolute_path(backup).parent
    if os.path.lexists(backup):
        _validate_windows_residue(backup, parent)
    if update.exists():
        _validate_windows_residue(update, parent)
    if retired.exists():
        _validate_windows_residue(retired, parent)
    if not marker.exists():
        # Compatibility with the previous non-atomic rotation: the only known
        # crash residue was a valid .update after the canonical backup vanished.
        if update.exists() and not backup.exists() and not retired.exists():
            os.replace(update, backup)
            return True
        if update.exists() or retired.exists():
            raise SafetyError(
                "Ambiguous Windows rollback rotation residue requires repair: "
                + str(update if update.exists() else retired)
            )
        return False
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError("Invalid Windows rollback rotation marker") from exc
    if marker_value != {"version": 1, "operation": "promote-update"}:
        raise SafetyError("Unsupported Windows rollback rotation marker")
    if backup.exists() and update.exists() and not retired.exists():
        os.replace(backup, retired)
    if not backup.exists() and update.exists() and retired.exists():
        os.replace(update, backup)
    if backup.exists() and retired.exists() and not update.exists():
        shutil.rmtree(retired)
    if not backup.exists() or update.exists() or retired.exists():
        raise SafetyError("Windows rollback rotation could not be recovered")
    marker.unlink()
    return True


def _rotate_windows_context_backup(backup: Path, update: Path) -> None:
    expected_update, retired, marker = _windows_rotation_paths(backup)
    if update != expected_update or not backup.is_dir() or not update.is_dir():
        raise SafetyError("Windows rollback rotation inputs are invalid")
    if retired.exists() or marker.exists():
        raise SafetyError("A Windows rollback rotation is already active")
    parent = _absolute_path(backup).parent
    _validate_windows_residue(backup, parent)
    _validate_windows_residue(update, parent)
    _write_windows_rotation_marker(marker)
    os.replace(backup, retired)
    os.replace(update, backup)
    shutil.rmtree(retired)
    marker.unlink()


def _windows_residue_candidates(install_root: Path) -> list[Path]:
    parent = _absolute_path(install_root).parent
    prefix = re.escape(install_root.name + ".rollback")
    owned_name = re.compile(
        rf"^{prefix}\.(?:interrupted|recovered)-[0-9]{{8}}-[0-9]{{4,6}}$"
    )
    if not parent.is_dir():
        return []
    return sorted(
        candidate
        for candidate in parent.iterdir()
        if owned_name.fullmatch(candidate.name)
    )


def _validate_windows_residue(candidate: Path, parent: Path) -> dict[str, object]:
    if not candidate.is_dir() or _is_link(candidate) or _absolute_path(candidate).parent != parent:
        raise SafetyError(f"Unsafe Windows transaction residue: {candidate}")
    _validate_windows_managed_tree(candidate, label="Windows transaction residue")
    metadata_path = candidate / "backup.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError(f"Invalid Windows transaction residue: {candidate}") from exc
    if metadata.get("version") not in {1, 2, 3}:
        raise SafetyError(f"Unsupported Windows transaction residue: {candidate}")
    return metadata


def _recover_windows_certificate_ownership_from_residue(install_root: Path) -> bool:
    """Migrate ownership lost by older updates before deleting their backups."""
    _validate_windows_managed_tree(
        install_root, label="Windows context-menu install tree"
    )
    current_path = install_root / "certificate-state.json"
    if not current_path.is_file():
        return False
    try:
        current = json.loads(current_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError("Invalid current certificate ownership state") from exc
    thumbprint = str(current.get("thumbprint", ""))
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", thumbprint):
        raise SafetyError("Invalid current certificate thumbprint")
    flags = (
        "created_my",
        "created_trusted_people",
        "created_machine_trusted_people",
    )
    recovered = False
    parent = _absolute_path(install_root).parent
    for candidate in _windows_residue_candidates(install_root):
        _validate_windows_residue(candidate, parent)
        historical_path = candidate / "external" / "certificate-state.json"
        if not historical_path.is_file():
            continue
        try:
            historical = json.loads(historical_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Invalid historical certificate state: {candidate}") from exc
        if str(historical.get("thumbprint", "")).casefold() != thumbprint.casefold():
            continue
        for flag in flags:
            if bool(historical.get(flag)) and not bool(current.get(flag)):
                current[flag] = True
                recovered = True
    if recovered:
        temporary = current_path.with_name(current_path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, current_path)
    return recovered


def _cleanup_windows_context_residue(install_root: Path) -> list[str]:
    """Remove only obsolete, self-identifying transaction backups."""
    parent = _absolute_path(install_root).parent
    removed: list[str] = []
    for candidate in _windows_residue_candidates(install_root):
        _validate_windows_residue(candidate, parent)
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    return removed


def _capture_windows_context_backup(
    backup: Path,
    *,
    install_root: Path,
    run: object,
) -> dict[str, object]:
    _validate_windows_managed_tree(
        install_root, label="Windows context-menu install tree"
    )
    if os.path.lexists(backup):
        raise SkillMagnetError("A Windows context-menu transaction is already active")
    package_status = windows_modern_context_menu_status(install_root=install_root, run=run)
    roots_present: list[bool] = []
    registry_hashes: list[str | None] = []
    backup.mkdir(parents=True)
    try:
        registry_roots = _windows_owned_menu_roots()
        for index, registry_root in enumerate(registry_roots):
            present = _windows_registry_root_present(registry_root, run=run)
            roots_present.append(present)
            if present:
                exported = run(
                    ["reg", "export", registry_root, str(backup / f"classic-{index}.reg"), "/y"],
                    capture_output=True,
                    text=True,
                )
                if exported.returncode != 0:
                    raise SkillMagnetError(f"Cannot back up Windows context-menu root: {registry_root}")
                registry_hashes.append(
                    hashlib.sha256((backup / f"classic-{index}.reg").read_bytes()).hexdigest()
                )
            else:
                registry_hashes.append(None)
        external_existed = install_root.exists()
        if external_existed:
            shutil.copytree(install_root, backup / "external")
        same_name_packages = package_status.get("same_name_packages", [])
        owned_packages = [
            {
                key: str(package.get(key, ""))
                for key in (
                    "name",
                    "version",
                    "architecture",
                    "publisher",
                    "package_full_name",
                )
            }
            for package in same_name_packages
            if isinstance(package, dict)
            and package.get("name") == _WINDOWS_MODERN_PACKAGE_NAME
            and package.get("publisher") == _WINDOWS_MODERN_PACKAGE_IDENTITY["Publisher"]
        ]
        if not same_name_packages and package_status.get("installed"):
            owned_packages = [
                {
                    "name": str(package_status.get("name", "")),
                    "version": str(package_status.get("version", "")),
                    "architecture": str(package_status.get("architecture", "")),
                    "publisher": str(package_status.get("publisher", "")),
                    "package_full_name": str(package_status.get("package_full_name", "")),
                }
            ]
        saved_external = backup / "external"
        external_manifest = (
            {
                path.relative_to(saved_external).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(saved_external.rglob("*"))
                if path.is_file()
            }
            if external_existed
            else {}
        )
        metadata = {
            "version": 3,
            "registry_roots": roots_present,
            "registry_sha256": registry_hashes,
            "package_installed": bool(owned_packages),
            "owned_packages": owned_packages,
            "external_existed": external_existed,
            "external_manifest": external_manifest,
        }
        (backup / "backup.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        return metadata
    except Exception:
        if backup.exists():
            shutil.rmtree(backup)
        raise


def _validated_windows_context_backup(
    backup: Path,
    *,
    install_root: Path,
) -> dict[str, object]:
    """Validate the complete rollback snapshot before any destructive action."""

    backup = _absolute_path(backup)
    install_root = _absolute_path(install_root)
    expected_backup = _windows_context_backup_root(install_root)
    allowed_backups = {
        os.path.normcase(str(expected_backup)),
        os.path.normcase(
            str(expected_backup.with_name(expected_backup.name + ".update"))
        ),
    }
    if os.path.normcase(str(backup)) not in allowed_backups:
        raise SkillMagnetError(
            "Windows context-menu rollback path is not owned by this install; "
            "the current installation was not changed"
        )
    _validate_windows_managed_tree(backup, label="Windows rollback tree")
    _validate_windows_managed_tree(
        install_root, label="Windows context-menu install tree"
    )
    metadata_path = backup / "backup.json"
    if not metadata_path.is_file() or _is_link(metadata_path):
        raise SkillMagnetError("Windows context-menu rollback metadata is missing")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillMagnetError(
            "Windows context-menu rollback metadata is unreadable; "
            "the current installation was not changed"
        ) from exc
    required = {
        "version",
        "registry_roots",
        "registry_sha256",
        "package_installed",
        "owned_packages",
        "external_existed",
        "external_manifest",
    }
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise SkillMagnetError(
            "Windows context-menu rollback metadata schema is incomplete; "
            "the current installation was not changed"
        )
    if type(metadata["version"]) is not int or metadata["version"] != 3:
        raise SkillMagnetError(
            "Windows context-menu rollback metadata version is unsupported; "
            "the current installation was not changed"
        )
    if type(metadata["package_installed"]) is not bool or type(
        metadata["external_existed"]
    ) is not bool:
        raise SkillMagnetError(
            "Windows context-menu rollback metadata has invalid boolean fields; "
            "the current installation was not changed"
        )

    registry_roots = metadata["registry_roots"]
    registry_hashes = metadata["registry_sha256"]
    expected_root_count = len(_windows_owned_menu_roots())
    if (
        not isinstance(registry_roots, list)
        or len(registry_roots) != expected_root_count
        or any(type(value) is not bool for value in registry_roots)
        or not isinstance(registry_hashes, list)
        or len(registry_hashes) != expected_root_count
    ):
        raise SkillMagnetError(
            "Windows context-menu rollback registry metadata is invalid; "
            "the current installation was not changed"
        )
    expected_entries = {"backup.json"}
    for index, present in enumerate(registry_roots):
        saved_registry = backup / f"classic-{index}.reg"
        expected_hash = registry_hashes[index]
        if present:
            if (
                not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
                or not saved_registry.is_file()
                or _is_link(saved_registry)
                or hashlib.sha256(saved_registry.read_bytes()).hexdigest()
                != expected_hash
            ):
                raise SkillMagnetError(
                    "Windows context-menu rollback registry digest is invalid; "
                    "the current installation was not changed"
                )
            expected_entries.add(saved_registry.name)
        elif expected_hash is not None or os.path.lexists(saved_registry):
            raise SkillMagnetError(
                "Windows context-menu rollback registry metadata is inconsistent; "
                "the current installation was not changed"
            )

    package_fields = {
        "name",
        "version",
        "architecture",
        "publisher",
        "package_full_name",
    }
    owned_packages = metadata["owned_packages"]
    if not isinstance(owned_packages, list):
        raise SkillMagnetError(
            "Windows context-menu rollback package ownership metadata is invalid; "
            "the current installation was not changed"
        )
    seen_packages: set[tuple[str, ...]] = set()
    for package in owned_packages:
        if (
            not isinstance(package, dict)
            or set(package) != package_fields
            or any(not isinstance(package[field], str) for field in package_fields)
            or package["name"] != _WINDOWS_MODERN_PACKAGE_NAME
            or package["publisher"] != _WINDOWS_MODERN_PACKAGE_IDENTITY["Publisher"]
        ):
            raise SkillMagnetError(
                "Windows context-menu rollback package ownership metadata is invalid; "
                "the current installation was not changed"
            )
        identity = tuple(package[field] for field in sorted(package_fields))
        if identity in seen_packages:
            raise SkillMagnetError(
                "Windows context-menu rollback package ownership metadata is duplicated; "
                "the current installation was not changed"
            )
        seen_packages.add(identity)
    if bool(owned_packages) is not metadata["package_installed"]:
        raise SkillMagnetError(
            "Windows context-menu rollback package ownership state is inconsistent; "
            "the current installation was not changed"
        )

    external_manifest = metadata["external_manifest"]
    if not isinstance(external_manifest, dict):
        raise SkillMagnetError(
            "Windows context-menu rollback external manifest is invalid; "
            "the current installation was not changed"
        )
    for relative, digest in external_manifest.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise SkillMagnetError(
                "Windows context-menu rollback external manifest is invalid; "
                "the current installation was not changed"
            )
        parsed = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.as_posix() != relative
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise SkillMagnetError(
                "Windows context-menu rollback external manifest path or digest is invalid; "
                "the current installation was not changed"
            )
    saved_external = backup / "external"
    if metadata["external_existed"]:
        if not saved_external.is_dir() or _is_link(saved_external):
            raise SkillMagnetError(
                "Windows context-menu rollback external backup is missing; "
                "the current installation was not changed"
            )
        actual_manifest = {
            path.relative_to(saved_external).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(saved_external.rglob("*"))
            if path.is_file()
        }
        if actual_manifest != external_manifest:
            raise SkillMagnetError(
                "Windows context-menu rollback external manifest does not match; "
                "the current installation was not changed"
            )
        expected_entries.add("external")
    elif external_manifest or os.path.lexists(saved_external):
        raise SkillMagnetError(
            "Windows context-menu rollback external metadata is inconsistent; "
            "the current installation was not changed"
        )
    if {entry.name for entry in backup.iterdir()} != expected_entries:
        raise SkillMagnetError(
            "Windows context-menu rollback tree contains unexpected paths; "
            "the current installation was not changed"
        )
    return metadata


def _windows_certificate_thumbprint(install_root: Path) -> str | None:
    state_path = install_root / "certificate-state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError("Invalid Windows rollback certificate state; nothing was changed") from exc
    thumbprint = state.get("thumbprint") if isinstance(state, dict) else None
    if not isinstance(thumbprint, str) or not re.fullmatch(r"[0-9A-Fa-f]{40}", thumbprint):
        raise SafetyError("Invalid Windows rollback certificate identity; nothing was changed")
    return thumbprint.upper()


def _restore_windows_context_backup(
    backup: Path,
    *,
    install_root: Path,
    run: object,
) -> None:
    metadata = _validated_windows_context_backup(
        backup, install_root=install_root
    )
    _, _, package_script = _windows_modern_paths(install_root)
    saved_external = backup / "external"
    current_certificate = _windows_certificate_thumbprint(install_root)
    previous_certificate = (
        _windows_certificate_thumbprint(saved_external) if metadata["external_existed"] else None
    )
    shared_certificate = current_certificate is not None and current_certificate == previous_certificate

    # Remove the current package before replacing its external content.
    _package_action("uninstall", package_script, install_root=install_root, run=run)
    if install_root.exists():
        # Updates usually reuse the signing certificate. The backup records its
        # ownership but does not contain a private key that can recreate it.
        if not shared_certificate:
            _package_action("cleanup-certificate", package_script, install_root=install_root, run=run)
        shutil.rmtree(install_root)
    if metadata["external_existed"]:
        shutil.copytree(saved_external, install_root)
        _validate_windows_managed_tree(
            install_root, label="restored Windows context-menu install tree"
        )
        restored_manifest = {
            path.relative_to(install_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(install_root.rglob("*"))
            if path.is_file()
        }
        if restored_manifest != metadata["external_manifest"]:
            raise SkillMagnetError("Restored Windows external files do not match backup")
    if metadata["package_installed"]:
        _package_action("install", package_script, install_root=install_root, run=run)
        restored_status = _package_action(
            "status", package_script, install_root=install_root, run=run
        )
        restored_owned = [
            {
                key: str(package.get(key, ""))
                for key in (
                    "name",
                    "version",
                    "architecture",
                    "publisher",
                    "package_full_name",
                )
            }
            for package in restored_status.get("same_name_packages", [])
            if isinstance(package, dict)
            and package.get("publisher")
            == _WINDOWS_MODERN_PACKAGE_IDENTITY["Publisher"]
        ]
        if not restored_status.get("same_name_packages") and restored_status.get(
            "installed"
        ):
            restored_owned = [
                {
                    "name": str(restored_status.get("name", "")),
                    "version": str(restored_status.get("version", "")),
                    "architecture": str(restored_status.get("architecture", "")),
                    "publisher": str(restored_status.get("publisher", "")),
                    "package_full_name": str(
                        restored_status.get("package_full_name", "")
                    ),
                }
            ]
        if restored_owned != metadata["owned_packages"]:
            raise SkillMagnetError(
                "Restored Windows package identity does not match backup"
            )

    registry_roots = _windows_owned_menu_roots()
    roots_present = metadata["registry_roots"]
    for index, root in enumerate(registry_roots):
        deleted = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
        if deleted.returncode not in (0, 1):
            raise SkillMagnetError(f"Cannot clear context-menu root during rollback: {root}")
        if roots_present[index]:
            expected_hash = metadata["registry_sha256"][index]
            actual_hash = hashlib.sha256(
                (backup / f"classic-{index}.reg").read_bytes()
            ).hexdigest()
            if actual_hash != expected_hash:
                raise SkillMagnetError(
                    f"Windows rollback registry backup changed: {root}"
                )
            restored = run(
                ["reg", "import", str(backup / f"classic-{index}.reg")],
                capture_output=True,
                text=True,
            )
            if restored.returncode != 0:
                raise SkillMagnetError(f"Cannot restore context-menu root: {root}")
            readback = backup / f"classic-{index}.readback-{uuid.uuid4().hex}.reg"
            try:
                exported = run(
                    ["reg", "export", root, str(readback), "/y"],
                    capture_output=True,
                    text=True,
                )
                if exported.returncode != 0 or hashlib.sha256(
                    readback.read_bytes()
                ).hexdigest() != metadata["registry_sha256"][index]:
                    raise SkillMagnetError(
                        f"Restored Windows registry content does not match backup: {root}"
                    )
            finally:
                readback.unlink(missing_ok=True)
    restored_presence = [
        _windows_registry_root_present(root, run=run) for root in registry_roots
    ]
    if restored_presence != list(roots_present):
        raise SkillMagnetError(
            "Windows context-menu rollback readback does not match the saved state"
        )


def _acquire_windows_context_mutation_lock() -> object:
    """Serialize every public Windows context-menu state transition."""

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = create_mutex(
            None,
            True,
            "Local\\SkillMagnet-WindowsContextMenuMutation-v1",
        )
        if not handle:
            raise SkillMagnetError(
                "Cannot acquire the Windows context-menu operation lock. "
                "Nothing was changed; check this user's access permissions and retry."
            )
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            close(handle)
            raise SkillMagnetError(
                "Another Windows context-menu operation is active. Nothing was "
                "changed; wait for it to finish and retry."
            )
        return (kernel32, handle)

    # Cross-platform tests use the same process contract without weakening the
    # real Windows named-mutex boundary.
    import fcntl

    path = Path(tempfile.gettempdir()) / "skill-magnet-windows-context-mutation.lock"
    handle = path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SkillMagnetError(
            "Another Windows context-menu operation is active. Nothing was "
            "changed; wait for it to finish and retry."
        ) from exc
    return handle


def _release_windows_context_mutation_lock(lock: object | None) -> None:
    if lock is None:
        return
    if isinstance(lock, tuple):
        kernel32, handle = lock
        try:
            if not kernel32.ReleaseMutex(handle):
                error = __import__("ctypes").get_last_error()
                if error not in {0, 288}:  # ERROR_NOT_OWNER during teardown
                    raise OSError(error, "Cannot release Windows context-menu mutex")
        finally:
            kernel32.CloseHandle(handle)
        return
    try:
        import fcntl

        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        lock.close()


def _install_windows_context_menus_unlocked(
    config: Path,
    *,
    install_root: Path | None = None,
    run: object = subprocess.run,
    build: bool = True,
) -> dict[str, object]:
    """Install exactly one visible Windows entry and retain one rollback point."""
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be installed on Windows")
    _, root, _ = _windows_modern_paths(install_root)
    _validate_windows_managed_tree(root, label="Windows context-menu install tree")
    recovered_rotation = _recover_windows_rollback_rotation(
        _windows_context_backup_root(root)
    )
    recovered_certificate_ownership = _recover_windows_certificate_ownership_from_residue(root)
    removed_residue = _cleanup_windows_context_residue(root)
    backup = _windows_context_backup_root(root)
    first_install = not backup.exists()
    transaction_backup = backup if first_install else backup.with_name(backup.name + ".update")
    modern_recovery: dict[str, object] | None = None
    try:
        previous = _capture_windows_context_backup(
            transaction_backup, install_root=root, run=run
        )
        if previous["package_installed"]:
            _, _, package_script = _windows_modern_paths(root)
            _package_action("uninstall", package_script, install_root=root, run=run)
        try:
            modern = install_windows_modern_context_menu(
                config, install_root=root, run=run, build=build
            )
            if modern.get("packaged_native_build_residue_preserved"):
                modern_recovery = {
                    "id": modern.get("packaged_native_build_recovery_id"),
                    "directory": modern.get(
                        "packaged_native_build_recovery_directory"
                    ),
                }
        except SkillMagnetError as modern_error:
            # A partial modern registration must not coexist with another
            # entry. Do not fall back to a self-signed process adapter: Smart
            # App Control can reject it even when Authenticode is locally
            # trusted. The outer transaction restores the previous state.
            try:
                uninstall_windows_modern_context_menu(
                    install_root=root,
                    run=run,
                    cleanup_certificates=False,
                )
            except SkillMagnetError as cleanup_error:
                raise SkillMagnetError(
                    "Modern context menu failed and could not be removed; "
                    "the previous state could not be restored: " + str(cleanup_error)
                ) from modern_error
            raise SkillMagnetError(
                "Modern context menu failed; no policy-incompatible classic "
                "fallback was registered: " + str(modern_error)
            ) from modern_error
        else:
            # The modern root is canonical. Remove every classic/legacy root so
            # Explorer exposes only one visible Skill Magnet entry.
            uninstall_context_menu("windows", run=run)
            remaining_classic_roots = _windows_owned_registry_roots_present(run=run)
            if remaining_classic_roots:
                raise SkillMagnetError(
                    "Classic Skill Magnet context-menu roots remain after cleanup: "
                    + ", ".join(remaining_classic_roots)
                )
            classic = {
                "installed": False,
                "fallback_while_modern_unavailable": False,
                "locations": list(_windows_owned_menu_roots()),
                "verified_absent": True,
            }
            modern_receipt = modern
            modern = windows_modern_context_menu_status(
                install_root=root, config=config, run=run
            )
            for field in (
                "packaged_native_build_residue_removed",
                "packaged_native_build_residue_preserved",
                "packaged_native_build_recovery_id",
                "packaged_native_build_recovery_directory",
                "legacy_certificate_thumbprints_removed",
            ):
                if field in modern_receipt:
                    modern[field] = modern_receipt[field]
            if not modern.get("usable_installed_state"):
                raise SkillMagnetError(
                    "Windows context-menu install did not reach one exclusive usable root"
                )
        if not first_install:
            # A successful update advances the one rollback point to the
            # immediately previous installed state. The original pre-install
            # snapshot belongs to uninstall, not update rollback semantics.
            _rotate_windows_context_backup(backup, transaction_backup)
        return {
            "installed": True,
            "platform": "windows",
            "classic": classic,
            "modern": modern,
            "rollback_point": str(backup),
            "removed_transaction_residue": removed_residue,
            "recovered_certificate_ownership": recovered_certificate_ownership,
            "recovered_rollback_rotation": recovered_rotation,
        }
    except Exception as original:
        rollback_failure: Exception | None = None
        try:
            if (transaction_backup / "backup.json").is_file():
                _restore_windows_context_backup(
                    transaction_backup, install_root=root, run=run
                )
            if transaction_backup.exists():
                shutil.rmtree(transaction_backup)
        except Exception as exc:
            rollback_failure = exc
        details: list[str] = []
        if rollback_failure is not None:
            details.append(f"previous menu-state recovery failed: {rollback_failure}")
        if modern_recovery is not None:
            details.append(
                "legacy native output remains preserved for user recovery at "
                f"{modern_recovery['directory']} "
                f"(recovery id {modern_recovery['id']}); no preserved file was deleted"
            )
        if details:
            raise SkillMagnetError(str(original) + "; " + "; ".join(details)) from original
        raise


def install_windows_context_menus(
    config: Path,
    *,
    install_root: Path | None = None,
    run: object = subprocess.run,
    build: bool = True,
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be installed on Windows")
    lock = _acquire_windows_context_mutation_lock()
    try:
        return _install_windows_context_menus_unlocked(
            config, install_root=install_root, run=run, build=build
        )
    finally:
        _release_windows_context_mutation_lock(lock)


def _rollback_windows_context_menus_unlocked(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be rolled back on Windows")
    _, root, _ = _windows_modern_paths(install_root)
    _validate_windows_managed_tree(root, label="Windows context-menu install tree")
    backup = _windows_context_backup_root(root)
    _recover_windows_rollback_rotation(backup)
    _restore_windows_context_backup(backup, install_root=root, run=run)
    shutil.rmtree(backup)
    removed_residue = _cleanup_windows_context_residue(root)
    return {
        "rolled_back": True,
        "platform": "windows",
        "external_location": str(root),
        "rollback_point_removed": True,
        "removed_transaction_residue": removed_residue,
    }


def rollback_windows_context_menus(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be rolled back on Windows")
    lock = _acquire_windows_context_mutation_lock()
    try:
        return _rollback_windows_context_menus_unlocked(
            install_root=install_root, run=run
        )
    finally:
        _release_windows_context_mutation_lock(lock)


def _uninstall_windows_context_menus_unlocked(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    """Remove the current product state instead of restoring an older update."""
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be uninstalled on Windows")
    _, root, package_script = _windows_modern_paths(install_root)
    _validate_windows_managed_tree(root, label="Windows context-menu install tree")
    status = _package_action("uninstall", package_script, install_root=root, run=run)
    if status.get("installed"):
        raise SkillMagnetError("Windows modern context-menu package remains registered")
    if root.exists():
        _package_action(
            "cleanup-certificate", package_script, install_root=root, run=run
        )
        shutil.rmtree(root)
    uninstall_context_menu("windows", run=run)
    backup = _windows_context_backup_root(root)
    if backup.exists():
        _validate_windows_residue(backup, backup.parent)
        shutil.rmtree(backup)
    removed_residue = _cleanup_windows_context_residue(root)
    return {
        "removed": True,
        "platform": "windows",
        "external_location": str(root),
        "rollback_point_removed": not backup.exists(),
        "removed_transaction_residue": removed_residue,
    }


def uninstall_windows_context_menus(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be uninstalled on Windows")
    lock = _acquire_windows_context_mutation_lock()
    try:
        return _uninstall_windows_context_menus_unlocked(
            install_root=install_root, run=run
        )
    finally:
        _release_windows_context_mutation_lock(lock)


def _windows_menu_roots(prefix: str = "HKCU") -> tuple[tuple[str, str], ...]:
    return (
        (prefix + r"\Software\Classes\Directory\shell\SkillMagnetClassic", "%1"),
        (prefix + r"\Software\Classes\Directory\Background\shell\SkillMagnetClassic", "%V"),
    )


def _windows_legacy_menu_roots(prefix: str = "HKCU") -> tuple[str, ...]:
    return (
        prefix + r"\Software\Classes\Directory\shell\SkillMagnet",
        prefix + r"\Software\Classes\Directory\Background\shell\SkillMagnet",
    )


def _windows_owned_menu_roots(prefix: str = "HKCU") -> tuple[str, ...]:
    return tuple(root for root, _ in _windows_menu_roots(prefix)) + (
        _windows_legacy_menu_roots(prefix)
    )


def _reject_windows_classic_registration() -> NoReturn:
    raise SkillMagnetError(
        "Windows classic context-menu registration is disabled; "
        "use install-context-menu --platform windows --confirm to install "
        "the supported modern package"
    )


def _windows_registry_entries(config: Path, root: str, placeholder: str) -> list[tuple[str, str, str]]:
    """Reject generation of the unsupported Windows classic registration."""
    _reject_windows_classic_registration()


def windows_directory_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Retained API boundary that rejects classic Directory registration."""
    _reject_windows_classic_registration()


def windows_background_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Retained API boundary that rejects classic Background registration."""
    _reject_windows_classic_registration()


def render_registration(platform: str, config: Path) -> str:
    if platform == "windows":
        _reject_windows_classic_registration()
    spec = context_menu_spec(platform, config)
    payload = json.dumps(spec.as_dict(), ensure_ascii=False, sort_keys=True)
    return (
        "#!/bin/sh\n"
        "# Finder Quick Action adapter generated by Skill Magnet.\n"
        f"SKILL_MAGNET_SPEC={shlex.quote(payload)}\n"
        'SELECTED_PATH="$1"\n'
        + subprocess_command(spec.command)
        + "\n"
    )


def subprocess_command(
    parts: tuple[str, ...], *, selected_path_expression: str = '"$SELECTED_PATH"'
) -> str:
    """Render static argv with POSIX quoting and one explicit dynamic path slot."""

    return " ".join(
        selected_path_expression if part == "$SELECTED_PATH" else shlex.quote(part)
        for part in parts
    )


def _finder_workflow_command(config: Path) -> str:
    spec = context_menu_spec("macos", config)
    return subprocess_command(
        spec.command, selected_path_expression='"$1"'
    ) + ' --finder-selection-count "$#"'


def _notify_windows_shell_change() -> None:
    """Invalidate Explorer's cached context-menu tree after registry changes."""
    if sys.platform != "win32":
        return
    import ctypes

    # SHCNE_ASSOCCHANGED with SHCNF_IDLIST is the documented shell-wide
    # notification for association and verb changes.  No Explorer restart is
    # required, so existing windows and user state are preserved.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


_FINDER_OWNER_MARKER = ".skill-magnet-owner.json"
_FINDER_TRANSACTION_JOURNAL = ".skill-magnet-workflow-transaction.json"
_FINDER_TRANSACTION_PREFIX = ".skill-magnet-workflow-"


def _finder_recovery_error(detail: str) -> SafetyError:
    return SafetyError(
        f"Finder Quick Actionの中断状態を安全に自動復旧できません: {detail}\n"
        "候補は削除していません。FinderのQuick Actions設定と"
        "~/Library/Services内の .skill-magnet-workflow-* を確認し、"
        "必要な内容を退避してからLibrary Managerでメニュー反映を再実行してください。"
    )


def _finder_document_path(root: Path) -> Path:
    return root / "Contents" / "document.wflow"


def _finder_document_digest(root: Path) -> str:
    contents = root / "Contents"
    document = _finder_document_path(root)
    if _is_link(contents) or not contents.is_dir() or _is_link(document) or not document.is_file():
        raise _finder_recovery_error(f"workflow documentが通常のfileではありません: {document}")
    return hashlib.sha256(document.read_bytes()).hexdigest()


def _finder_owner_marker_payload(nonce: str, document_digest: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "owner": "skill-magnet",
        "artifact": "finder-quick-action",
        "workflow_name": "Skill Magnet.workflow",
        "transaction_nonce": nonce,
        "document_sha256": document_digest,
    }


def _write_finder_owner_marker(root: Path, nonce: str, document_digest: str) -> bytes:
    payload = (
        json.dumps(
            _finder_owner_marker_payload(nonce, document_digest),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    marker = root / _FINDER_OWNER_MARKER
    with marker.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _validate_finder_owned_workflow(
    root: Path,
    *,
    expected_document_digest: str | None = None,
    expected_marker_digest: str | None = None,
    expected_transaction_nonce: str | None = None,
) -> dict[str, object]:
    if _is_link(root) or not root.is_dir():
        raise _finder_recovery_error(f"workflow候補が安全なdirectoryではありません: {root}")
    marker = root / _FINDER_OWNER_MARKER
    if _is_link(marker) or not marker.is_file():
        raise _finder_recovery_error(f"所有markerがありません: {root}")
    marker_bytes = marker.read_bytes()
    if expected_marker_digest is not None and hashlib.sha256(marker_bytes).hexdigest() != expected_marker_digest:
        raise _finder_recovery_error(f"所有markerがtransaction記録と一致しません: {root}")
    try:
        payload = json.loads(marker_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _finder_recovery_error(f"所有markerを読み取れません: {root}") from exc
    expected_keys = {
        "schema_version",
        "owner",
        "artifact",
        "workflow_name",
        "transaction_nonce",
        "document_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise _finder_recovery_error(f"所有markerの形式が不正です: {root}")
    nonce = payload.get("transaction_nonce")
    digest = payload.get("document_sha256")
    if (
        payload.get("schema_version") != 1
        or payload.get("owner") != "skill-magnet"
        or payload.get("artifact") != "finder-quick-action"
        or payload.get("workflow_name") != "Skill Magnet.workflow"
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise _finder_recovery_error(f"所有markerの値が不正です: {root}")
    if expected_transaction_nonce is not None and nonce != expected_transaction_nonce:
        raise _finder_recovery_error(f"所有markerのnonceがtransactionと一致しません: {root}")
    actual_document_digest = _finder_document_digest(root)
    if digest != actual_document_digest or (
        expected_document_digest is not None
        and expected_document_digest != actual_document_digest
    ):
        raise _finder_recovery_error(f"workflow documentのdigestが一致しません: {root}")
    return {**payload, "marker_sha256": hashlib.sha256(marker_bytes).hexdigest()}


def _write_finder_transaction_journal(
    services_dir: Path, payload: dict[str, object]
) -> Path:
    journal = services_dir / _FINDER_TRANSACTION_JOURNAL
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    with journal.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return journal


def _remove_verified_finder_directory(path: Path) -> None:
    if _is_link(path) or not path.is_dir():
        raise _finder_recovery_error(f"削除候補が安全なdirectoryではありません: {path}")
    shutil.rmtree(path)


def _recover_finder_workflow_transaction(
    services_dir: Path, workflow_root: Path
) -> bool:
    """Recover only the exact journal-bound Finder workflow directory swap."""

    if _is_link(services_dir) or not services_dir.is_dir():
        raise _finder_recovery_error(f"Services directoryが安全ではありません: {services_dir}")
    if _absolute_path(workflow_root).parent != _absolute_path(services_dir):
        raise _finder_recovery_error("workflow pathがServices directory外を指しています")
    journal = services_dir / _FINDER_TRANSACTION_JOURNAL
    residues = sorted(services_dir.glob(_FINDER_TRANSACTION_PREFIX + "*"))
    if not os.path.lexists(journal):
        if residues:
            raise _finder_recovery_error(
                "検証可能なtransaction journalがない候補があります: "
                + ", ".join(path.name for path in residues)
            )
        return False
    if _is_link(journal) or not journal.is_file():
        raise _finder_recovery_error("transaction journalが通常のfileではありません")
    try:
        record = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _finder_recovery_error("transaction journalを読み取れません") from exc
    expected_keys = {
        "schema_version",
        "owner",
        "operation",
        "nonce",
        "services_dir",
        "workflow_name",
        "stage_name",
        "backup_name",
        "previous_exists",
        "previous_document_sha256",
        "previous_marker_sha256",
        "candidate_document_sha256",
    }
    nonce = record.get("nonce") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or set(record) != expected_keys
        or record.get("schema_version") != 1
        or record.get("owner") != "skill-magnet"
        or record.get("operation") != "finder-workflow-swap"
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or record.get("services_dir") != str(_absolute_path(services_dir))
        or record.get("workflow_name") != workflow_root.name
        or record.get("stage_name") != f".skill-magnet-workflow-stage-{nonce}"
        or record.get("backup_name") != f".skill-magnet-workflow-backup-{nonce}"
        or not isinstance(record.get("previous_exists"), bool)
        or re.fullmatch(r"[0-9a-f]{64}", str(record.get("candidate_document_sha256", "")))
        is None
    ):
        raise _finder_recovery_error("transaction journalの内容が不正です")
    previous_exists = bool(record["previous_exists"])
    previous_document_digest = record.get("previous_document_sha256")
    previous_marker_digest = record.get("previous_marker_sha256")
    if previous_exists:
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(previous_document_digest or "")) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(previous_marker_digest or "")) is None
        ):
            raise _finder_recovery_error("以前のworkflow証拠が不完全です")
    elif previous_document_digest is not None or previous_marker_digest is not None:
        raise _finder_recovery_error("初回導入journalに以前のworkflow証拠が混在しています")

    stage = services_dir / str(record["stage_name"])
    backup = services_dir / str(record["backup_name"])
    allowed = {journal, stage, backup}
    unknown = [path for path in residues if path not in allowed]
    if unknown:
        raise _finder_recovery_error(
            "journalに属さない候補があります: "
            + ", ".join(path.name for path in unknown)
        )
    stage_exists = os.path.lexists(stage)
    backup_exists = os.path.lexists(backup)
    workflow_exists = os.path.lexists(workflow_root)
    candidate_digest = str(record["candidate_document_sha256"])
    if stage_exists:
        _validate_finder_owned_workflow(
            stage,
            expected_document_digest=candidate_digest,
            expected_transaction_nonce=nonce,
        )
    if backup_exists:
        _validate_finder_owned_workflow(
            backup,
            expected_document_digest=str(previous_document_digest),
            expected_marker_digest=str(previous_marker_digest),
        )

    if previous_exists:
        if stage_exists and not backup_exists and workflow_exists:
            _validate_finder_owned_workflow(
                workflow_root,
                expected_document_digest=str(previous_document_digest),
                expected_marker_digest=str(previous_marker_digest),
            )
            _remove_verified_finder_directory(stage)
        elif stage_exists and backup_exists and not workflow_exists:
            os.replace(backup, workflow_root)
            _remove_verified_finder_directory(stage)
        elif not stage_exists and backup_exists and workflow_exists:
            _validate_finder_owned_workflow(
                workflow_root,
                expected_document_digest=candidate_digest,
                expected_transaction_nonce=nonce,
            )
            _remove_verified_finder_directory(backup)
        elif not stage_exists and not backup_exists and workflow_exists:
            _validate_finder_owned_workflow(
                workflow_root,
                expected_document_digest=candidate_digest,
                expected_transaction_nonce=nonce,
            )
        else:
            raise _finder_recovery_error("backup、stage、installed workflowの組合せが曖昧です")
    else:
        if stage_exists and not backup_exists and not workflow_exists:
            _remove_verified_finder_directory(stage)
        elif not stage_exists and not backup_exists and workflow_exists:
            _validate_finder_owned_workflow(
                workflow_root,
                expected_document_digest=candidate_digest,
                expected_transaction_nonce=nonce,
            )
        elif not stage_exists and not backup_exists and not workflow_exists:
            pass
        else:
            raise _finder_recovery_error("初回導入のstageとinstalled workflowの組合せが曖昧です")
    journal.unlink()
    return True


def install_context_menu(
    platform: str,
    config: Path,
    *,
    services_dir: Path | None = None,
    run: object = subprocess.run,
    replace_existing: bool = False,
) -> dict[str, object]:
    """Install only after an explicit CLI request; never activates a pack."""
    if platform == "windows":
        _reject_windows_classic_registration()
    spec = context_menu_spec(platform, config)
    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action can only be installed on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    base_preexisting = os.path.lexists(base)
    if base_preexisting and (_is_link(base) or not base.is_dir()):
        raise _finder_recovery_error(f"Services directoryが安全ではありません: {base}")
    base.mkdir(parents=True, exist_ok=True)
    workflow_root = base / "Skill Magnet.workflow"
    recovered_transaction = _recover_finder_workflow_transaction(base, workflow_root)
    previous_exists = os.path.lexists(workflow_root)
    previous_owner: dict[str, object] | None = None
    if previous_exists:
        previous_owner = _validate_finder_owned_workflow(workflow_root)
    if previous_exists and not replace_existing:
        raise SkillMagnetError(f"Finder Quick Action already exists: {workflow_root}")
    shell_command = _finder_workflow_command(config)
    document = {
        "AMApplicationBuild": "SkillMagnet",
        "AMApplicationVersion": "1",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.path"]},
                    "AMActionVersion": "2.0.3",
                    "AMParameterProperties": {},
                    "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.path"]},
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "Class Name": "RunShellScriptAction",
                    "ActionParameters": {
                        "COMMAND_STRING": shell_command,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": 1,
                        "shell": "/bin/zsh",
                        "source": "",
                    },
                }
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            "serviceInputTypeIdentifier": "com.apple.finder.file-or-folder",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "serviceApplicationBundleID": "com.apple.finder",
        },
    }
    document_bytes = plistlib.dumps(document)
    candidate_digest = hashlib.sha256(document_bytes).hexdigest()
    if previous_exists and _finder_document_path(workflow_root).read_bytes() == document_bytes:
        return {
            "installed": True,
            "updated": False,
            "unchanged": True,
            "recovered_transaction": recovered_transaction,
            "platform": platform,
            "locations": [str(workflow_root)],
        }

    nonce = uuid.uuid4().hex
    temporary_root = base / f".skill-magnet-workflow-stage-{nonce}"
    backup_root = base / f".skill-magnet-workflow-backup-{nonce}"
    journal_record = {
        "schema_version": 1,
        "owner": "skill-magnet",
        "operation": "finder-workflow-swap",
        "nonce": nonce,
        "services_dir": str(_absolute_path(base)),
        "workflow_name": workflow_root.name,
        "stage_name": temporary_root.name,
        "backup_name": backup_root.name,
        "previous_exists": previous_exists,
        "previous_document_sha256": (
            str(previous_owner["document_sha256"]) if previous_owner is not None else None
        ),
        "previous_marker_sha256": (
            str(previous_owner["marker_sha256"]) if previous_owner is not None else None
        ),
        "candidate_document_sha256": candidate_digest,
    }
    journal = _write_finder_transaction_journal(base, journal_record)
    replaced_previous = False
    installed_candidate = False
    try:
        temporary_root.mkdir()
        workflow = temporary_root / "Contents"
        workflow.mkdir()
        with (workflow / "document.wflow").open("xb") as handle:
            handle.write(document_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        _write_finder_owner_marker(temporary_root, nonce, candidate_digest)
        if previous_exists:
            os.replace(workflow_root, backup_root)
            replaced_previous = True
        os.replace(temporary_root, workflow_root)
        installed_candidate = True
        if replaced_previous:
            _validate_finder_owned_workflow(
                backup_root,
                expected_document_digest=str(previous_owner["document_sha256"]),
                expected_marker_digest=str(previous_owner["marker_sha256"]),
            )
            _remove_verified_finder_directory(backup_root)
            replaced_previous = False
        journal.unlink()
    except Exception:
        # Once the candidate is visible, retain the journal and every remaining
        # artifact so the next launch can verify and finish recovery. Before
        # that commit point, restore the exact verified previous directory.
        if not installed_candidate:
            try:
                if replaced_previous and os.path.lexists(backup_root):
                    if os.path.lexists(workflow_root):
                        raise _finder_recovery_error(
                            "rollback先に別のworkflowが現れたため自動復旧を停止しました"
                        )
                    os.replace(backup_root, workflow_root)
                    replaced_previous = False
                if os.path.lexists(temporary_root):
                    # This exact nonce was created after the exclusive journal.
                    # Reject links even in this in-process cleanup path.
                    if _is_link(temporary_root) or not temporary_root.is_dir():
                        raise _finder_recovery_error(
                            f"stageの種類が変化しました: {temporary_root}"
                        )
                    shutil.rmtree(temporary_root)
                journal.unlink(missing_ok=True)
                if not base_preexisting and not workflow_root.exists():
                    try:
                        base.rmdir()
                    except OSError:
                        pass
            except Exception:
                # Preserve the original exception and the complete recovery
                # record. The next public entry will fail closed if ambiguous.
                pass
        raise
    return {
        "installed": True,
        "updated": previous_exists,
        "unchanged": False,
        "recovered_transaction": recovered_transaction,
        "platform": platform,
        "locations": [str(workflow_root)],
    }


def uninstall_context_menu(
    platform: str,
    *,
    services_dir: Path | None = None,
    run: object = subprocess.run,
) -> dict[str, object]:
    if platform == "windows":
        if os.name != "nt":
            raise SkillMagnetError("Windows context menu can only be removed on Windows")
        roots = _windows_owned_menu_roots()
        for root in roots:
            result = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
            if result.returncode not in (0, 1):
                raise SkillMagnetError(
                    f"Cannot remove Windows context menu: {result.stderr.strip()}"
                )
        remaining = _windows_owned_registry_roots_present(run=run)
        if remaining:
            raise SkillMagnetError(
                "Windows context-menu roots remain after removal: " + ", ".join(remaining)
            )
        _notify_windows_shell_change()
        return {"removed": True, "platform": platform, "locations": list(roots)}
    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action can only be removed on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    workflow = base / "Skill Magnet.workflow"
    if os.path.lexists(base) and (_is_link(base) or not base.is_dir()):
        raise _finder_recovery_error(f"Services directoryが安全ではありません: {base}")
    if base.is_dir():
        _recover_finder_workflow_transaction(base, workflow)
    if os.path.lexists(workflow):
        _validate_finder_owned_workflow(workflow)
        _remove_verified_finder_directory(workflow)
    return {"removed": True, "platform": platform, "locations": [str(workflow)]}


def finder_context_menu_status(
    *, config: Path | None = None, services_dir: Path | None = None
) -> dict[str, object]:
    """Report whether the product-owned Finder Quick Action is usable."""

    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action status is only available on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    workflow = base / "Skill Magnet.workflow"
    document = workflow / "Contents" / "document.wflow"
    safe_base = not os.path.lexists(base) or (base.is_dir() and not _is_link(base))
    installed = safe_base and workflow.is_dir() and not _is_link(workflow)
    document_exists = installed and document.is_file() and not _is_link(document)
    workflow_owned = False
    ownership_error = ""
    if installed:
        try:
            _validate_finder_owned_workflow(workflow)
            workflow_owned = True
        except SafetyError as exc:
            ownership_error = str(exc)
    elif not safe_base:
        ownership_error = "Finder Services directory is not a safe product location"
    contract_valid = False
    contract_matches_config: bool | None = None
    command = ""
    if document_exists:
        try:
            payload = plistlib.loads(document.read_bytes())
            metadata = payload["workflowMetaData"]
            actions = payload["actions"]
            action = actions[0]["action"]
            parameters = action["ActionParameters"]
            command = str(parameters["COMMAND_STRING"])
            contract_valid = (
                len(actions) == 1
                and action["BundleIdentifier"] == "com.apple.RunShellScript"
                and metadata["serviceApplicationBundleID"] == "com.apple.finder"
                and metadata["serviceInputTypeIdentifier"]
                == "com.apple.finder.file-or-folder"
                and parameters["shell"] == "/bin/zsh"
                and "python" in command.casefold()
                and "context" in command
                and "--platform" in command
                and "macos" in command
                and "--launcher" in command
                and "--finder-selection-count" in command
            )
            if config is not None:
                contract_matches_config = command == _finder_workflow_command(config)
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            OSError,
            ExpatError,
            plistlib.InvalidFileException,
        ):
            contract_valid = False
            if config is not None:
                contract_matches_config = False
    residue = (
        sorted(str(path) for path in base.glob(_FINDER_TRANSACTION_PREFIX + "*"))
        if safe_base and base.is_dir()
        else []
    )
    return {
        "installed": installed,
        "platform": "macos",
        "integration": "macos_finder_quick_action",
        "location": str(workflow),
        "document_exists": document_exists,
        "workflow_owned": workflow_owned,
        "ownership_error": ownership_error,
        "workflow_contract_valid": contract_valid,
        "workflow_contract_matches_config": contract_matches_config,
        "release_probe_present": "--release-probe" in command,
        "transaction_residue": residue,
        "usable_installed_state": (
            installed
            and document_exists
            and workflow_owned
            and contract_valid
            and contract_matches_config is not False
            and "--release-probe" not in command
            and not residue
        ),
    }
