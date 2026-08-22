from __future__ import annotations

import json
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .core import Config, SkillMagnetError


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
    runtime: str
    command: tuple[str, ...]

    @property
    def pack_label(self) -> str:
        return f"Pack: {self.pack_id} ({len(self.skill_ids)} skills)"

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
    return (sys.executable, "-c", bootstrap, "--config", str(config.resolve()))


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


def windows_menu_leaves(config: Path, placeholder: str) -> tuple[WindowsMenuLeaf, ...]:
    """Build the static pack-only Explorer tree without activating anything."""
    loaded = Config.load(config)
    prefix = _cli_prefix(config)
    return tuple(
        WindowsMenuLeaf(
            pack_id=pack.pack_id,
            skill_ids=pack.skills,
            runtime=runtime,
            command=(
                *prefix,
                "context",
                "--platform",
                "windows",
                "--project",
                placeholder,
                "--pack",
                pack.pack_id,
                "--runtime",
                runtime,
                "--menu-commit",
                pack.expected_commit,
                "--menu-skill-digest",
                hashlib.sha256(
                    json.dumps(
                        pack.skills, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
            ),
        )
        for pack in loaded.packs.values()
        for runtime in ("codex", "claude")
    )


def windows_command(parts: tuple[str, ...]) -> str:
    """Quote an argv vector with the Windows command-line parsing contract."""
    return subprocess.list2cmdline(list(parts))


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
        for runtime_index, leaf in enumerate(pack_leaves):
            runtime_root = (
                pack_root + rf"\shell\runtime-{runtime_index:03d}"
            )
            entries.extend(
                [
                    (runtime_root, "MUIVerb", leaf.runtime_label),
                    (runtime_root + r"\command", "", windows_command(leaf.command)),
                ]
            )
    return entries


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
        try:
            for root, placeholder in roots:
                stale = run(["reg", "delete", root, "/f"], capture_output=True, text=True)
                if stale.returncode not in (0, 1):
                    raise SkillMagnetError(
                        f"Cannot remove stale Windows context menu: {stale.stderr.strip()}"
                    )
                for key, name, value in _windows_registry_entries(config, root, placeholder):
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
            "packs": [
                leaf.pack_label
                for leaf in windows_menu_leaves(config, "%V")[::2]
            ],
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
        roots = (
            r"HKCU\Software\Classes\Directory\shell\SkillMagnet",
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet",
        )
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
