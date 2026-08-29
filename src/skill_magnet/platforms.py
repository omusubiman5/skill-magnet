from __future__ import annotations

import json
import hashlib
import base64
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .core import Config, Pack, SafetyError, SkillMagnetError, _is_link


_PACKAGE_ROOT = Path(__file__).resolve().parent
_SOURCE_ROOT = _PACKAGE_ROOT.parent


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
        return self.display_name

    @property
    def skill_ids_digest(self) -> str:
        payload = json.dumps(self.skill_ids, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _windows_launcher_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = tempfile.gettempdir()
    # Path chooses its concrete class from os.name at call time.  The Windows
    # contract is also exercised on POSIX runners with os.name mocked, so keep
    # the host-native class captured at import time.
    return (
        type(_PACKAGE_ROOT)(local_app_data)
        / "SkillMagnet"
        / "ContextMenu"
        / "SkillMagnetLauncher.exe"
    )


def _cli_prefix(config: Path, *, windows_launcher: bool = False) -> tuple[str, ...]:
    """Return a command that works from Explorer's unrelated working directory."""
    source_root = _SOURCE_ROOT
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(source_root)!r});"
        "runpy.run_module('skill_magnet',run_name='__main__')"
    )
    executable = type(_PACKAGE_ROOT)(sys.executable)
    command = (str(executable), "-c", bootstrap, "--config", str(config.absolute()))
    if windows_launcher:
        return (str(_windows_launcher_path()), *command)
    return command


def context_menu_spec(platform: str, config: Path) -> ContextMenuSpec:
    prefix = _cli_prefix(config, windows_launcher=platform == "windows")
    if platform == "windows":
        placeholder = "%V"
        return ContextMenuSpec(
            platform="windows",
            integration="windows_explorer_context_menu",
            menu_label="Skill Magnet...",
            selected_path_placeholder=placeholder,
            command=(*prefix, "context", "--platform", "windows", "--project", placeholder),
        )
    if platform == "macos":
        placeholder = "$SELECTED_PATH"
        return ContextMenuSpec(
            platform="macos",
            integration="macos_finder_quick_action",
            menu_label="Skill Magnet...",
            selected_path_placeholder=placeholder,
            command=(*prefix, "context", "--platform", "macos", "--project", placeholder),
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
    instruction_digest = _selection_blob_digest(pack, skill_id, "SKILL.md")
    acceptance_digest = _selection_blob_digest(pack, skill_id, "acceptance.json")
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
        *_cli_prefix(config, windows_launcher=True),
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


def _fixed_blob_digest(pack: Pack, skill_id: str, filename: str) -> str:
    if (pack.source / ".skill-magnet-snapshot.json").is_file():
        path = pack.source / skill_id / filename
        if not path.is_file():
            raise SkillMagnetError(
                f"Cannot read approved skill artifact: {skill_id}/{filename}"
            )
        return hashlib.sha256(path.read_bytes()).hexdigest()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(pack.source),
            "show",
            f"{pack.expected_commit}:{skill_id}/{filename}",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SkillMagnetError(
            f"Cannot read approved skill artifact: {skill_id}/{filename}"
        )
    return hashlib.sha256(result.stdout).hexdigest()


def _selection_blob_digest(pack: Pack, skill_id: str | None, filename: str) -> str:
    """Bind a menu leaf to one skill or to every skill in a package."""
    if skill_id is not None:
        return _fixed_blob_digest(pack, skill_id, filename)
    payload = {
        selected: _fixed_blob_digest(pack, selected, filename)
        for selected in pack.skills
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def windows_menu_leaves(config: Path, placeholder: str) -> tuple[WindowsMenuLeaf, ...]:
    """Build one immutable Explorer leaf per explicitly selectable package."""
    loaded = Config.load(config)
    leaves: list[WindowsMenuLeaf] = []
    for pack in loaded.packs.values():
        if pack.selection_kind == "package":
            instruction_digest = _selection_blob_digest(pack, None, "SKILL.md")
            acceptance_digest = _selection_blob_digest(pack, None, "acceptance.json")
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
            instruction_digest = _fixed_blob_digest(pack, skill_id, "SKILL.md")
            acceptance_digest = _fixed_blob_digest(pack, skill_id, "acceptance.json")
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


def render_windows_modern_menu_manifest(config: Path) -> str:
    """Render the immutable, low-cost menu data consumed by IExplorerCommand."""
    loaded = Config.load(config)
    lines = ["skill-magnet-menu-v3"]
    for leaf in windows_menu_leaves(config, WINDOWS_MODERN_PROJECT_MARKER):
        pack = loaded.packs[leaf.pack_id]
        fields = (
            leaf.pack_id,
            "Skill Magnet",
            pack.selection_kind,
            leaf.skill_id or leaf.pack_id,
            leaf.display_name,
            leaf.purpose,
            windows_command(leaf.command),
        )
        if any("\t" in field or "\r" in field or "\n" in field for field in fields):
            raise SkillMagnetError("Windows modern menu fields cannot contain tabs or newlines")
        lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


_WINDOWS_MODERN_PACKAGE_NAME = "SkillMagnet.ContextMenu"
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
    return native_root, _absolute_path(install_root), native_root / "package.ps1"


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


def windows_modern_context_menu_status(
    *,
    install_root: Path | None = None,
    config: Path | None = None,
    run: object = subprocess.run,
) -> dict[str, object]:
    _, root, script = _windows_modern_paths(install_root)
    status = _package_action("status", script, install_root=root, run=run)
    manifest = root / "AppxManifest.xml"
    identity_matches = False
    com_identity_matches = False
    manifest_contexts: list[str] = []
    if manifest.is_file():
        try:
            document = ET.parse(manifest).getroot()
            namespaces = {
                "foundation": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
                "com": "http://schemas.microsoft.com/appx/manifest/com/windows10",
                "desktop5": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/5",
            }
            identity = document.find("foundation:Identity", namespaces)
            identity_matches = bool(
                identity is not None and identity.get("Name") == _WINDOWS_MODERN_PACKAGE_NAME
            )
            classes = document.findall(".//com:Class", namespaces)
            verbs = document.findall(".//desktop5:Verb", namespaces)
            item_types = document.findall(".//desktop5:ItemType", namespaces)
            class_ids = {item.get("Id") for item in classes}
            verb_ids = {item.get("Clsid") for item in verbs}
            com_identity_matches = bool(class_ids and class_ids == verb_ids)
            manifest_contexts = [
                item.get("Type", "") for item in item_types if item.get("Type")
            ]
        except (ET.ParseError, OSError):
            pass
    expected_contexts = ["Directory", r"Directory\Background"]
    command_target_exists = (root / "SkillMagnetLauncher.exe").is_file()
    dll_exists = (root / "SkillMagnetCommand.dll").is_file()
    menu_manifest_exists = (root / "SkillMagnetMenu.tsv").is_file()
    menu_leaf_count = 0
    menu_selection_kinds: list[str] = []
    menu_contract_valid = False
    menu_contract_matches_config: bool | None = None
    if menu_manifest_exists:
        try:
            menu_text = (root / "SkillMagnetMenu.tsv").read_text(encoding="utf-8-sig")
            lines = menu_text.splitlines()
            records = [line.split("\t") for line in lines[1:] if line]
            menu_leaf_count = len(records)
            menu_selection_kinds = [record[2] for record in records if len(record) == 7]
            menu_contract_valid = bool(
                lines
                and lines[0] == "skill-magnet-menu-v3"
                and records
                and all(
                    len(record) == 7
                    and record[1] == "Skill Magnet"
                    and record[2] in {"package", "skill"}
                    and all(record[index] for index in (0, 3, 4, 5, 6))
                    and WINDOWS_MODERN_PROJECT_MARKER in record[6]
                    for record in records
                )
            )
            if config is not None:
                menu_contract_matches_config = (
                    menu_text == render_windows_modern_menu_manifest(config)
                )
        except (OSError, UnicodeError, SkillMagnetError):
            menu_contract_valid = False
    package_registered = bool(status.get("installed"))
    usable_installed_state = bool(
        package_registered
        and identity_matches
        and com_identity_matches
        and manifest_contexts == expected_contexts
        and command_target_exists
        and dll_exists
        and menu_manifest_exists
        and menu_contract_valid
        and menu_contract_matches_config is not False
    )
    status.update(
        {
            "platform": "windows",
            "integration": "windows_11_modern_context_menu",
            "external_location": str(root),
            "package_registered": package_registered,
            "identity_matches": identity_matches,
            "com_identity_matches": com_identity_matches,
            "command_target_exists": command_target_exists,
            "dll_exists": dll_exists,
            "menu_manifest_exists": menu_manifest_exists,
            "menu_contract_valid": menu_contract_valid,
            "menu_contract_matches_config": menu_contract_matches_config,
            "menu_leaf_count": menu_leaf_count,
            "menu_selection_kinds": menu_selection_kinds,
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
    output = native_root / "out"
    if build:
        build_result = run(
            [
                _powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(native_root / "build.ps1"),
                "-OutDir",
                str(output),
                "-SkipContractTest",
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if build_result.returncode != 0:
            detail = (build_result.stderr or build_result.stdout or "unknown build error").strip()
            raise SkillMagnetError(f"Windows modern context-menu build failed: {detail}")
    required = (output / "SkillMagnetCommand.dll", output / "SkillMagnetLauncher.exe")
    if not all(path.is_file() for path in required):
        raise SkillMagnetError("Windows modern context-menu build outputs are missing")

    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(required[0], root / required[0].name)
    shutil.copy2(required[1], root / required[1].name)
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
            detail = (package_build.stderr or package_build.stdout or "unknown package build error").strip()
            raise SkillMagnetError(f"Windows signed identity package build failed: {detail}")

    status = _package_action("install", script, install_root=root, run=run)
    if not status.get("installed"):
        raise SkillMagnetError("Windows modern context-menu package did not register")
    status = windows_modern_context_menu_status(
        install_root=root, config=config, run=run
    )
    if not status.get("usable_installed_state"):
        raise SkillMagnetError("Windows modern context-menu installed state is incomplete")
    status.update(
        {
            "platform": "windows",
            "integration": "windows_11_modern_context_menu",
            "external_location": str(root),
            "contexts": ["Directory", r"Directory\Background"],
            "reinstall_required_after_pack_change": True,
        }
    )
    return status


def uninstall_windows_modern_context_menu(
    *,
    install_root: Path | None = None,
    run: object = subprocess.run,
    cleanup_certificates: bool = True,
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows modern context menu can only be removed on Windows")
    _, root, script = _windows_modern_paths(install_root)
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
    metadata_path = candidate / "backup.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError(f"Invalid Windows transaction residue: {candidate}") from exc
    if metadata.get("version") not in {1, 2}:
        raise SafetyError(f"Unsupported Windows transaction residue: {candidate}")
    return metadata


def _recover_windows_certificate_ownership_from_residue(install_root: Path) -> bool:
    """Migrate ownership lost by older updates before deleting their backups."""
    current_path = install_root / "certificate-state.json"
    if not current_path.is_file():
        return False
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
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
            historical = json.loads(historical_path.read_text(encoding="utf-8"))
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
    if backup.exists():
        raise SkillMagnetError("A Windows context-menu transaction is already active")
    package_status = windows_modern_context_menu_status(install_root=install_root, run=run)
    roots_present: list[bool] = []
    backup.mkdir(parents=True)
    try:
        registry_roots = _windows_owned_menu_roots()
        for index, registry_root in enumerate(registry_roots):
            queried = run(["reg", "query", registry_root], capture_output=True, text=True)
            if queried.returncode not in (0, 1):
                raise SkillMagnetError(f"Cannot inspect Windows context-menu root: {registry_root}")
            present = queried.returncode == 0
            roots_present.append(present)
            if present:
                exported = run(
                    ["reg", "export", registry_root, str(backup / f"classic-{index}.reg"), "/y"],
                    capture_output=True,
                    text=True,
                )
                if exported.returncode != 0:
                    raise SkillMagnetError(f"Cannot back up Windows context-menu root: {registry_root}")
        external_existed = install_root.exists()
        if external_existed:
            shutil.copytree(install_root, backup / "external")
        metadata = {
            "version": 2,
            "registry_roots": roots_present,
            "package_installed": bool(package_status.get("installed")),
            "external_existed": external_existed,
        }
        (backup / "backup.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        return metadata
    except Exception:
        if backup.exists():
            shutil.rmtree(backup)
        raise


def _restore_windows_context_backup(
    backup: Path,
    *,
    install_root: Path,
    run: object,
) -> None:
    metadata_path = backup / "backup.json"
    if not metadata_path.is_file():
        raise SkillMagnetError("Windows context-menu rollback metadata is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    _, _, package_script = _windows_modern_paths(install_root)

    # Remove the current package before replacing its external content.
    _package_action("uninstall", package_script, install_root=install_root, run=run)
    if install_root.exists():
        _package_action("cleanup-certificate", package_script, install_root=install_root, run=run)
        shutil.rmtree(install_root)
    saved_external = backup / "external"
    if metadata["external_existed"]:
        shutil.copytree(saved_external, install_root)
    if metadata["package_installed"]:
        restored = _package_action("install", package_script, install_root=install_root, run=run)
        if not restored.get("installed"):
            raise SkillMagnetError("Cannot restore the previous modern context-menu package")

    if metadata.get("version") == 1:
        registry_roots = tuple(root for root, _ in _windows_menu_roots())
        roots_present = metadata["classic_roots"]
    else:
        registry_roots = _windows_owned_menu_roots()
        roots_present = metadata["registry_roots"]
    if len(roots_present) != len(registry_roots):
        raise SkillMagnetError("Windows context-menu rollback metadata is invalid")
    for index, root in enumerate(registry_roots):
        deleted = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
        if deleted.returncode not in (0, 1):
            raise SkillMagnetError(f"Cannot clear context-menu root during rollback: {root}")
        if roots_present[index]:
            restored = run(
                ["reg", "import", str(backup / f"classic-{index}.reg")],
                capture_output=True,
                text=True,
            )
            if restored.returncode != 0:
                raise SkillMagnetError(f"Cannot restore context-menu root: {root}")


def install_windows_context_menus(
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
    recovered_certificate_ownership = _recover_windows_certificate_ownership_from_residue(root)
    removed_residue = _cleanup_windows_context_residue(root)
    backup = _windows_context_backup_root(root)
    first_install = not backup.exists()
    transaction_backup = backup if first_install else backup.with_name(backup.name + ".update")
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
        except SkillMagnetError as modern_error:
            # A partial modern registration must not coexist with the fallback.
            try:
                uninstall_windows_modern_context_menu(
                    install_root=root,
                    run=run,
                    cleanup_certificates=False,
                )
            except SkillMagnetError as cleanup_error:
                raise SkillMagnetError(
                    "Modern context menu failed and could not be removed; "
                    "classic fallback was not registered: " + str(cleanup_error)
                ) from modern_error
            # Classic commands use the GUI-subsystem adapter too. Keep only
            # that command target after the unusable package is removed, so
            # fallback never regresses to a visible python.exe console.
            launcher_source = _windows_modern_paths(root)[0] / "out" / "SkillMagnetLauncher.exe"
            if not launcher_source.is_file():
                raise SkillMagnetError(
                    "Modern context menu failed and the consoleless classic launcher is missing"
                ) from modern_error
            root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(launcher_source, root / launcher_source.name)
            classic = {
                **install_context_menu("windows", config, run=run),
                "fallback_while_modern_unavailable": True,
            }
            modern = {
                "installed": False,
                "usable_installed_state": False,
                "error": str(modern_error),
            }
        else:
            # The modern root is canonical. Remove every classic/legacy root so
            # Explorer exposes only one visible Skill Magnet entry.
            uninstall_context_menu("windows", run=run)
            classic = {
                "installed": False,
                "fallback_while_modern_unavailable": False,
                "locations": list(_windows_owned_menu_roots()),
            }
        if not first_install:
            shutil.rmtree(transaction_backup)
        return {
            "installed": True,
            "platform": "windows",
            "classic": classic,
            "modern": modern,
            "rollback_point": str(backup),
            "removed_transaction_residue": removed_residue,
            "recovered_certificate_ownership": recovered_certificate_ownership,
        }
    except Exception:
        if (transaction_backup / "backup.json").is_file():
            _restore_windows_context_backup(transaction_backup, install_root=root, run=run)
        if transaction_backup.exists():
            shutil.rmtree(transaction_backup)
        raise


def rollback_windows_context_menus(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be rolled back on Windows")
    _, root, _ = _windows_modern_paths(install_root)
    backup = _windows_context_backup_root(root)
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


def _windows_registry_entries(config: Path, root: str, placeholder: str) -> list[tuple[str, str, str]]:
    """Return (key, value-name, value) entries owned by one Skill Magnet subtree."""
    entries: list[tuple[str, str, str]] = [
        (root, "", ""),
        (root, "MUIVerb", "Skill Magnet"),
        (root, "SubCommands", ""),
    ]
    leaves = windows_menu_leaves(config, placeholder)
    # Explorer has proven unreliable with nested static cascades on the target
    # Windows build. Each direct child fixes one skill; the user chooses the AI
    # in the confirmation UI before a launch contract is created.
    for leaf_index, leaf in enumerate(leaves):
        leaf_root = root + rf"\shell\leaf-{leaf_index:03d}"
        entries.extend(
            [
                (leaf_root, "MUIVerb", leaf.skill_label),
                (leaf_root + r"\command", "", windows_command(leaf.command)),
            ]
        )
    return entries


def windows_directory_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Build only Skill Magnet's Directory/%1 registry subtree."""
    root = prefix + r"\Software\Classes\Directory\shell\SkillMagnetClassic"
    return tuple(_windows_registry_entries(config, root, "%1"))


def windows_background_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Build only Skill Magnet's Directory/Background/%V registry subtree."""
    root = prefix + r"\Software\Classes\Directory\Background\shell\SkillMagnetClassic"
    return tuple(_windows_registry_entries(config, root, "%V"))


def render_registration(platform: str, config: Path) -> str:
    spec = context_menu_spec(platform, config)
    if platform == "windows":
        sections: list[str] = ["Windows Registry Editor Version 5.00\n"]
        for root, placeholder in _windows_menu_roots("HKEY_CURRENT_USER"):
            for key, name, value in _windows_registry_entries(config, root, placeholder):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                rendered_name = "@" if not name else f'"{name}"'
                sections.extend([f"[{key}]\n", f'{rendered_name}="{escaped}"\n\n'])
        return "\n".join(sections)
    payload = json.dumps(spec.as_dict(), ensure_ascii=False, sort_keys=True)
    return (
        "#!/bin/sh\n"
        "# Finder Quick Action adapter generated by Skill Magnet.\n"
        f"SKILL_MAGNET_SPEC='{payload}'\n"
        'SELECTED_PATH="$1"\n'
        + subprocess_command(spec.command)
        + "\n"
    )


def subprocess_command(parts: tuple[str, ...]) -> str:
    def quote(value: str) -> str:
        return '"' + value.replace('"', '\\"') + '"'

    return " ".join(quote(part) for part in parts)


def _notify_windows_shell_change() -> None:
    """Invalidate Explorer's cached context-menu tree after registry changes."""
    if sys.platform != "win32":
        return
    import ctypes

    # SHCNE_ASSOCCHANGED with SHCNF_IDLIST is the documented shell-wide
    # notification for association and verb changes.  No Explorer restart is
    # required, so existing windows and user state are preserved.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


def install_context_menu(
    platform: str,
    config: Path,
    *,
    services_dir: Path | None = None,
    run: object = subprocess.run,
) -> dict[str, object]:
    """Install only after an explicit CLI request; never activates a pack."""
    spec = context_menu_spec(platform, config)
    if platform == "windows":
        if os.name != "nt":
            raise SkillMagnetError("Windows context menu can only be installed on Windows")
        roots = _windows_menu_roots()
        registrations = (
            (roots[0][0], windows_directory_registry_entries(config)),
            (roots[1][0], windows_background_registry_entries(config)),
        )
        try:
            for legacy_root in _windows_legacy_menu_roots():
                stale = run(["reg", "delete", legacy_root, "/f"], capture_output=True, text=True)
                if stale.returncode not in (0, 1):
                    raise SkillMagnetError(
                        f"Cannot remove legacy Windows context menu: {stale.stderr.strip()}"
                    )
            for root, entries in registrations:
                stale = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
                if stale.returncode not in (0, 1):
                    raise SkillMagnetError(
                        f"Cannot remove stale Windows context menu: {stale.stderr.strip()}"
                    )
                for key, name, value in entries:
                    args = ["reg", "add", key]
                    args.extend(["/v", name] if name else ["/ve"])
                    args.extend(["/d", value, "/f"])
                    result = run(args, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise SkillMagnetError(
                            f"Cannot install Windows context menu: {result.stderr.strip()}"
                        )
        except Exception:
            for root, _ in roots:
                run(["reg", "delete", root, "/f"], capture_output=True, text=True)
            raise
        _notify_windows_shell_change()
        return {
            "installed": True,
            "platform": platform,
            "locations": [root for root, _ in roots],
            "packs": [f"Pack: {pack_id}" for pack_id in Config.load(config).packs],
            "reinstall_required_after_pack_change": True,
        }

    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action can only be installed on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    base_preexisting = base.exists()
    base.mkdir(parents=True, exist_ok=True)
    workflow_root = base / "Skill Magnet.workflow"
    if workflow_root.exists():
        raise SkillMagnetError(f"Finder Quick Action already exists: {workflow_root}")
    temporary_root = Path(tempfile.mkdtemp(prefix=".skill-magnet-workflow-", dir=base))
    workflow = temporary_root / "Contents"
    workflow.mkdir()
    shell_command = subprocess_command(spec.command).replace('"$SELECTED_PATH"', '"$1"')
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
                    "parameters": {
                        "COMMAND_STRING": shell_command,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": 1,
                        "shell": "/bin/zsh",
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
    try:
        with (workflow / "document.wflow").open("wb") as handle:
            plistlib.dump(document, handle)
        os.replace(temporary_root, workflow_root)
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        if not base_preexisting:
            try:
                base.rmdir()
            except OSError:
                pass
        raise
    return {
        "installed": True,
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
        _notify_windows_shell_change()
        return {"removed": True, "platform": platform, "locations": list(roots)}
    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action can only be removed on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    workflow = base / "Skill Magnet.workflow"
    if workflow.exists():
        shutil.rmtree(workflow)
    return {"removed": True, "platform": platform, "locations": [str(workflow)]}
