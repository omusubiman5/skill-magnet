from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .activation import ActivationEngine
from .core import Config, Engine, SkillMagnetError
from .platforms import (
    context_menu_spec,
    install_context_menu,
    render_registration,
    uninstall_context_menu,
)
from .ui import show_context_selection


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-magnet")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("skill-magnet.json"),
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
                help="Confirm the displayed target, version, purpose and verification plan.",
            )
            command.add_argument("--codex-executable", default="codex")
    context = commands.add_parser("context")
    context.add_argument("--platform", required=True, choices=("windows", "macos"))
    context.add_argument("--project", required=True, type=Path)
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
    remove = commands.add_parser("uninstall-context-menu")
    remove.add_argument("--platform", required=True, choices=("windows", "macos"))
    remove.add_argument("--confirm", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
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
                result = activation.execute(
                    contract.contract_id, codex_executable=args.codex_executable
                )
        elif args.command == "context":
            activation = ActivationEngine(config, args.state_dir)
            contract = show_context_selection(
                activation, platform=args.platform, project=args.project
            )
            if contract is None:
                result = {"status": "cancelled"}
            else:
                result = activation.execute(contract.contract_id)
        elif args.command == "context-menu-spec":
            result = context_menu_spec(args.platform, args.config).as_dict()
        elif args.command == "render-context-menu":
            print(render_registration(args.platform, args.config))
            return 0
        elif args.command == "install-context-menu":
            if not args.confirm:
                raise SkillMagnetError("Context-menu installation requires --confirm")
            result = install_context_menu(args.platform, args.config)
        else:
            if not args.confirm:
                raise SkillMagnetError("Context-menu removal requires --confirm")
            result = uninstall_context_menu(args.platform)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SkillMagnetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
