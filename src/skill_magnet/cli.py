from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import Config, Engine, SkillMagnetError


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
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--pack", required=True)
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
                        "skills": len(pack.skills),
                    }
                    for pack in config.packs.values()
                ]
            }
        elif args.command in {"dry-run", "status"}:
            result = engine.status(args.pack, args.target)
        elif args.command == "sync":
            result = engine.sync(args.pack, args.target)
        else:
            result = engine.rollback(args.pack)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SkillMagnetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

