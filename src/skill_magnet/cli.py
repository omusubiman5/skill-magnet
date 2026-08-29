from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .activation import ActivationEngine
from .core import Config, Engine, SkillMagnetError
from .platforms import (
    context_menu_spec,
    install_context_menu,
    install_windows_context_menus,
    install_windows_modern_context_menu,
    render_registration,
    uninstall_context_menu,
    uninstall_windows_modern_context_menu,
    rollback_windows_context_menus,
    windows_modern_context_menu_status,
)
from .ui import (
    context_failure_message,
    deliver_prepared_codex_handoff,
    show_context_error,
    show_context_result,
    show_context_selection,
)


def _default_config_path() -> Path:
    packaged = Path(__file__).resolve().parent / "skill-magnet.json"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "skill-magnet.json"


def exit_process(exit_code: int, *, executable: str | None = None) -> None:
    """End Explorer's windowless Python leaf without retaining Tk threads."""
    executable_name = Path(executable or sys.executable).name.casefold()
    if os.name == "nt" and executable_name == "pythonw.exe":
        os._exit(exit_code)
        return
    raise SystemExit(exit_code)


def _configure_console_streams() -> None:
    """Keep diagnostics printable on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-magnet")
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help="Path to the Skill Magnet JSON configuration.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="Override the local state directory (primarily for testing).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("packs", help="List selectable skill packs.")
    for name in ("dry-run", "sync", "status"):
        command = commands.add_parser(name)
        command.add_argument("--pack", required=True)
        command.add_argument(
            "--target",
            action="append",
            choices=("codex", "claude"),
            help="Limit the operation to one target. May be repeated.",
        )
        if name == "sync":
            command.add_argument(
                "--allow-legacy-persistent-sync",
                action="store_true",
                help="Explicitly opt in to the legacy persistent-copy engine.",
            )
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--pack", required=True)
    for name in ("activation-plan", "activation-launch"):
        command = commands.add_parser(name)
        command.add_argument("--platform", required=True, choices=("windows", "macos"))
        command.add_argument("--project", required=True, type=Path)
        command.add_argument("--pack", required=True)
        command.add_argument("--purpose", required=True)
        command.add_argument("--ttl-minutes", type=int, default=30)
        if name == "activation-launch":
            command.add_argument(
                "--confirm",
                action="store_true",
                help="Confirm the displayed target, version, purpose and Desktop handoff.",
            )
    context = commands.add_parser("context")
    context.add_argument("--platform", required=True, choices=("windows", "macos"))
    context.add_argument("--project", required=True, type=Path)
    context.add_argument("--pack")
    context.add_argument("--skill")
    context.add_argument("--runtime", choices=("codex", "claude"))
    context.add_argument("--menu-commit")
    context.add_argument("--menu-skill-digest")
    context.add_argument("--menu-instruction-digest")
    context.add_argument("--menu-acceptance-digest")
    spec = commands.add_parser("context-menu-spec")
    spec.add_argument("--platform", required=True, choices=("windows", "macos"))
    render = commands.add_parser("render-context-menu")
    render.add_argument("--platform", required=True, choices=("windows", "macos"))
    install = commands.add_parser("install-context-menu")
    install.add_argument("--platform", required=True, choices=("windows", "macos"))
    install.add_argument(
        "--confirm",
        action="store_true",
        help="Explicitly install the OS context-menu entry. Does not activate a pack.",
    )
    install.add_argument(
        "--modern",
        action="store_true",
        help="Deprecated compatibility flag; Windows prefers modern and uses classic only as fallback.",
    )
    remove = commands.add_parser("uninstall-context-menu")
    remove.add_argument("--platform", required=True, choices=("windows", "macos"))
    remove.add_argument("--confirm", action="store_true")
    remove.add_argument("--modern", action="store_true")
    menu_status = commands.add_parser("context-menu-status")
    menu_status.add_argument("--platform", required=True, choices=("windows", "macos"))
    menu_rollback = commands.add_parser("rollback-context-menu")
    menu_rollback.add_argument("--platform", required=True, choices=("windows",))
    menu_rollback.add_argument("--confirm", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console_streams()
    args = _parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        engine = Engine(config, args.state_dir)
        if args.command == "packs":
            result = {
                "packs": [
                    {
                        "id": pack.pack_id,
                        "repo_url": pack.repo_url,
                        "expected_commit": pack.expected_commit,
                        "purpose": pack.purpose,
                        "approved_by": pack.approved_by,
                        "approved_at": pack.approved_at,
                        "skills": len(pack.skills),
                    }
                    for pack in config.packs.values()
                ]
            }
        elif args.command in {"dry-run", "status"}:
            result = engine.status(args.pack, args.target)
        elif args.command == "sync":
            if not args.allow_legacy_persistent_sync:
                raise SkillMagnetError(
                    "Legacy persistent sync is disabled by default; use the verified "
                    "activation path instead"
                )
            result = engine.sync(args.pack, args.target)
        elif args.command == "rollback":
            result = engine.rollback(args.pack)
        elif args.command in {"activation-plan", "activation-launch"}:
            activation = ActivationEngine(config, args.state_dir)
            plan = activation.plan(
                platform=args.platform,
                project=args.project,
                pack_id=args.pack,
                runtime="codex",
                purpose=args.purpose,
                ttl_minutes=args.ttl_minutes,
            )
            if args.command == "activation-plan":
                result = plan
            else:
                contract = activation.confirm(plan, confirmed=args.confirm)
                result = deliver_prepared_codex_handoff(
                    activation, contract.contract_id
                )
        elif args.command == "context":
            activation = ActivationEngine(config, args.state_dir)
            if args.platform == "windows":
                required = (
                    args.pack,
                    args.menu_commit,
                    args.menu_skill_digest,
                    args.menu_instruction_digest,
                    args.menu_acceptance_digest,
                )
                if any(value is None for value in required):
                    raise SkillMagnetError(
                        "Windows context leaf requires pack, commit and digests"
                    )
                try:
                    contract = show_context_selection(
                        activation,
                        platform=args.platform,
                        project=args.project,
                        pack_id=args.pack,
                        skill_id=args.skill,
                        runtime=args.runtime,
                        menu_commit=args.menu_commit,
                        menu_skill_digest=args.menu_skill_digest,
                        menu_instruction_digest=args.menu_instruction_digest,
                        menu_acceptance_digest=args.menu_acceptance_digest,
                    )
                except SkillMagnetError as exc:
                    show_context_error(context_failure_message(exc))
                    raise
                if contract is None:
                    return 0
                try:
                    if contract.runtime == "codex":
                        result = deliver_prepared_codex_handoff(
                            activation, contract.contract_id
                        )
                    else:
                        result = activation.execute(
                            contract.contract_id, interactive_handoff=True
                        )
                except SkillMagnetError as exc:
                    show_context_error(context_failure_message(exc))
                    raise
                if result.get("status") == "verified_completed":
                    show_context_result(result)
                    return 0
                if result.get("status") == "desktop_handoff_ready":
                    return 0
            else:
                try:
                    contract = show_context_selection(
                        activation,
                        platform=args.platform,
                        project=args.project,
                        pack_id=args.pack,
                        skill_id=args.skill,
                        runtime=args.runtime,
                        menu_commit=args.menu_commit,
                        menu_skill_digest=args.menu_skill_digest,
                        menu_instruction_digest=args.menu_instruction_digest,
                        menu_acceptance_digest=args.menu_acceptance_digest,
                    )
                except SkillMagnetError as exc:
                    show_context_error(context_failure_message(exc))
                    raise
                if contract is None:
                    return 0
                try:
                    if contract.runtime == "codex":
                        result = deliver_prepared_codex_handoff(
                            activation, contract.contract_id
                        )
                    else:
                        result = activation.execute(
                            contract.contract_id, interactive_handoff=True
                        )
                except SkillMagnetError as exc:
                    show_context_error(context_failure_message(exc))
                    raise
                if result.get("status") == "verified_completed":
                    show_context_result(result)
                    return 0
                if result.get("status") == "desktop_handoff_ready":
                    return 0
        elif args.command == "context-menu-spec":
            result = context_menu_spec(args.platform, args.config).as_dict()
        elif args.command == "render-context-menu":
            print(render_registration(args.platform, args.config))
            return 0
        elif args.command == "install-context-menu":
            if not args.confirm:
                raise SkillMagnetError("Context-menu installation requires --confirm")
            if args.platform == "windows":
                result = install_windows_context_menus(args.config)
            else:
                result = {"classic": install_context_menu(args.platform, args.config)}
        elif args.command == "uninstall-context-menu":
            if not args.confirm:
                raise SkillMagnetError("Context-menu removal requires --confirm")
            result = {}
            if args.platform == "windows":
                result = rollback_windows_context_menus()
            else:
                result["classic"] = uninstall_context_menu(args.platform)
        elif args.command == "context-menu-status":
            if args.platform != "windows":
                raise SkillMagnetError("Modern context-menu status is available on Windows")
            result = windows_modern_context_menu_status(config=args.config)
        else:
            if not args.confirm:
                raise SkillMagnetError("Context-menu rollback requires --confirm")
            result = rollback_windows_context_menus()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SkillMagnetError as exc:
        if args.command == "context":
            # The OS context leaf already displayed the one permitted result UI.
            # Do not expose runtime diagnostics or structured JSON on stderr.
            return 2
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
