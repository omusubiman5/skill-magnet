from __future__ import annotations

import json
import hashlib
import base64
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .core import Config, Pack, SkillMagnetError


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
    skill_id: str
    instruction_digest: str
    acceptance_digest: str
    runtime: str
    command: tuple[str, ...]

    @property
    def pack_label(self) -> str:
        return f"Pack: {self.pack_id}"

    @property
    def skill_label(self) -> str:
        return f"Skill: {self.skill_id}"

    @property
    def runtime_label(self) -> str:
        return self.runtime.title()

    @property
    def skill_ids_digest(self) -> str:
        payload = json.dumps(self.skill_ids, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cli_prefix(config: Path) -> tuple[str, ...]:
    """Return a command that works from Explorer's unrelated working directory."""
    source_root = Path(__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(source_root)!r});"
        "runpy.run_module('skill_magnet',run_name='__main__')"
    )
    executable = Path(sys.executable)
    if sys.platform == "win32":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
    return (str(executable), "-c", bootstrap, "--config", str(config.resolve()))


def context_menu_spec(platform: str, config: Path) -> ContextMenuSpec:
    prefix = _cli_prefix(config)
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
    skill_id: str,
    runtime: str,
) -> tuple[str, ...]:
    """Build one Explorer leaf argv without invoking or composing a shell."""
    loaded = Config.load(config)
    if pack_id not in loaded.packs:
        raise SkillMagnetError(f"Unknown pack: {pack_id}")
    pack = loaded.packs[pack_id]
    if skill_id not in pack.skills:
        raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
    if runtime not in ("codex", "claude"):
        raise SkillMagnetError(f"Unsupported runtime: {runtime}")
    instruction_digest = _fixed_blob_digest(pack, skill_id, "SKILL.md")
    acceptance_digest = _fixed_blob_digest(pack, skill_id, "acceptance.json")
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
    skill_id: str,
    runtime: str,
    instruction_digest: str,
    acceptance_digest: str,
) -> tuple[str, ...]:
    skill_ids_digest = hashlib.sha256(
        json.dumps(pack.skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return (
        *_cli_prefix(config),
        "context",
        "--platform",
        "windows",
        "--project",
        project,
        "--pack",
        pack.pack_id,
        "--skill",
        skill_id,
        "--runtime",
        runtime,
        "--menu-instruction-digest",
        instruction_digest,
        "--menu-acceptance-digest",
        acceptance_digest,
        "--menu-commit",
        pack.expected_commit,
        "--menu-skill-digest",
        skill_ids_digest,
    )


def _fixed_blob_digest(pack: Pack, skill_id: str, filename: str) -> str:
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


def windows_menu_leaves(config: Path, placeholder: str) -> tuple[WindowsMenuLeaf, ...]:
    """Build the static Pack/Skill/runtime Explorer leaves without activation."""
    loaded = Config.load(config)
    leaves: list[WindowsMenuLeaf] = []
    for pack in loaded.packs.values():
        for skill_id in pack.skills:
            instruction_digest = _fixed_blob_digest(pack, skill_id, "SKILL.md")
            acceptance_digest = _fixed_blob_digest(pack, skill_id, "acceptance.json")
            for runtime in ("codex", "claude"):
                leaves.append(
                    WindowsMenuLeaf(
                        pack_id=pack.pack_id,
                        skill_ids=pack.skills,
                        skill_id=skill_id,
                        instruction_digest=instruction_digest,
                        acceptance_digest=acceptance_digest,
                        runtime=runtime,
                        command=_windows_leaf_argv(
                            config,
                            placeholder,
                            pack,
                            skill_id,
                            runtime,
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
    lines = ["skill-magnet-menu-v2"]
    for leaf in windows_menu_leaves(config, WINDOWS_MODERN_PROJECT_MARKER):
        pack = loaded.packs[leaf.pack_id]
        fields = (
            leaf.pack_id,
            pack.menu_label,
            pack.selection_kind,
            leaf.skill_id,
            leaf.runtime_label,
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
    source_root = Path(__file__).resolve().parents[2]
    native_root = source_root / "native" / "windows-modern-context-menu"
    if install_root is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise SkillMagnetError("LOCALAPPDATA is required for modern context-menu installation")
        install_root = Path(local_app_data) / "SkillMagnet" / "ContextMenu"
    return native_root, install_root.resolve(), native_root / "package.ps1"


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
    result = run(command, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown package error").strip()
        raise SkillMagnetError(f"Windows modern context-menu {action} failed: {detail}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SkillMagnetError("Windows package command returned invalid status") from exc


def windows_modern_context_menu_status(
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    _, root, script = _windows_modern_paths(install_root)
    status = _package_action("status", script, install_root=root, run=run)
    status.update(
        {
            "platform": "windows",
            "integration": "windows_11_modern_context_menu",
            "external_location": str(root),
            "dll_exists": (root / "SkillMagnetCommand.dll").is_file(),
            "menu_manifest_exists": (root / "SkillMagnetMenu.tsv").is_file(),
            "contexts": ["Directory", r"Directory\Background"],
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
    *, install_root: Path | None = None, run: object = subprocess.run
) -> dict[str, object]:
    if os.name != "nt":
        raise SkillMagnetError("Windows modern context menu can only be removed on Windows")
    _, root, script = _windows_modern_paths(install_root)
    status = _package_action("uninstall", script, install_root=root, run=run)
    if status.get("installed"):
        raise SkillMagnetError("Windows modern context-menu package remains registered")
    if root.exists():
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

    for index, (root, _) in enumerate(_windows_menu_roots()):
        deleted = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
        if deleted.returncode not in (0, 1):
            raise SkillMagnetError(f"Cannot clear context-menu root during rollback: {root}")
        if metadata["classic_roots"][index]:
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
    """Install classic+modern menus transactionally and retain one rollback point."""
    if os.name != "nt":
        raise SkillMagnetError("Windows context menus can only be installed on Windows")
    _, root, _ = _windows_modern_paths(install_root)
    backup = _windows_context_backup_root(root)
    if backup.exists():
        raise SkillMagnetError("A Windows context-menu rollback point already exists")
    backup.mkdir(parents=True)
    package_status = windows_modern_context_menu_status(install_root=root, run=run)
    roots_present: list[bool] = []
    try:
        for index, (registry_root, _) in enumerate(_windows_menu_roots()):
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
        external_existed = root.exists()
        if external_existed:
            shutil.copytree(root, backup / "external")
        (backup / "backup.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "classic_roots": roots_present,
                    "package_installed": bool(package_status.get("installed")),
                    "external_existed": external_existed,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        classic = install_context_menu("windows", config, run=run)
        modern = install_windows_modern_context_menu(
            config, install_root=root, run=run, build=build
        )
        return {
            "installed": True,
            "platform": "windows",
            "classic": classic,
            "modern": modern,
            "rollback_point": str(backup),
        }
    except Exception:
        if (backup / "backup.json").is_file():
            _restore_windows_context_backup(backup, install_root=root, run=run)
        if backup.exists():
            shutil.rmtree(backup)
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
    return {
        "rolled_back": True,
        "platform": "windows",
        "external_location": str(root),
        "rollback_point_removed": True,
    }


def _windows_menu_roots(prefix: str = "HKCU") -> tuple[tuple[str, str], ...]:
    return (
        (prefix + r"\Software\Classes\Directory\shell\SkillMagnet", "%1"),
        (prefix + r"\Software\Classes\Directory\Background\shell\SkillMagnet", "%V"),
    )


def _windows_registry_entries(config: Path, root: str, placeholder: str) -> list[tuple[str, str, str]]:
    """Return (key, value-name, value) entries owned by one Skill Magnet subtree."""
    entries: list[tuple[str, str, str]] = [
        (root, "", ""),
        (root, "MUIVerb", "Skill Magnet"),
        (root, "SubCommands", ""),
    ]
    leaves = windows_menu_leaves(config, placeholder)
    pack_order: list[str] = []
    for leaf in leaves:
        if leaf.pack_id not in pack_order:
            pack_order.append(leaf.pack_id)
    for pack_index, pack_id in enumerate(pack_order):
        pack_leaves = [leaf for leaf in leaves if leaf.pack_id == pack_id]
        pack_root = root + rf"\shell\pack-{pack_index:03d}"
        entries.extend(
            [
                (pack_root, "", ""),
                (pack_root, "MUIVerb", pack_leaves[0].pack_label),
                (pack_root, "SubCommands", ""),
            ]
        )
        skill_order = tuple(dict.fromkeys(leaf.skill_id for leaf in pack_leaves))
        for skill_index, skill_id in enumerate(skill_order):
            skill_leaves = [leaf for leaf in pack_leaves if leaf.skill_id == skill_id]
            skill_root = pack_root + rf"\shell\skill-{skill_index:03d}"
            entries.extend(
                [
                    (skill_root, "MUIVerb", skill_leaves[0].skill_label),
                    (skill_root, "SubCommands", ""),
                ]
            )
            for runtime_index, leaf in enumerate(skill_leaves):
                runtime_root = skill_root + rf"\shell\runtime-{runtime_index:03d}"
                entries.extend(
                    [
                        (runtime_root, "MUIVerb", leaf.runtime_label),
                        (
                            runtime_root + r"\command",
                            "",
                            windows_command(leaf.command),
                        ),
                    ]
                )
    return entries


def windows_directory_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Build only Skill Magnet's Directory/%1 registry subtree."""
    root = prefix + r"\Software\Classes\Directory\shell\SkillMagnet"
    return tuple(_windows_registry_entries(config, root, "%1"))


def windows_background_registry_entries(
    config: Path, prefix: str = "HKCU"
) -> tuple[tuple[str, str, str], ...]:
    """Build only Skill Magnet's Directory/Background/%V registry subtree."""
    root = prefix + r"\Software\Classes\Directory\Background\shell\SkillMagnet"
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
        roots = tuple(root for root, _ in _windows_menu_roots())
        for root in roots:
            result = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
            if result.returncode not in (0, 1):
                raise SkillMagnetError(
                    f"Cannot remove Windows context menu: {result.stderr.strip()}"
                )
        return {"removed": True, "platform": platform, "locations": list(roots)}
    if sys.platform != "darwin" and services_dir is None:
        raise SkillMagnetError("Finder Quick Action can only be removed on macOS")
    base = services_dir or (Path.home() / "Library" / "Services")
    workflow = base / "Skill Magnet.workflow"
    if workflow.exists():
        shutil.rmtree(workflow)
    return {"removed": True, "platform": platform, "locations": [str(workflow)]}
