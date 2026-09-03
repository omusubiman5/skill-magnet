from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .activation import ActivationEngine
from .core import Config, Engine, SkillMagnetError
from .library_manager import (
    DEFAULT_REPOSITORY_NAME,
    LibraryTransaction,
    add_skill,
    delete_pack,
    delete_skill,
    initialize_library,
    library_inventory,
    list_transactions,
    update_pack_source,
    update_skill_source,
    validate_library,
)
from .library_ui import show_library_manager
from .platforms import (
    context_menu_spec,
    finder_context_menu_status,
    install_context_menu,
    install_windows_context_menus,
    install_windows_modern_context_menu,
    render_registration,
    uninstall_context_menu,
    uninstall_windows_modern_context_menu,
    uninstall_windows_context_menus,
    rollback_windows_context_menus,
    windows_modern_context_menu_status,
)
from .ui import (
    context_failure_message,
    deliver_prepared_codex_handoff,
    deliver_prepared_claude_handoff,
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
    context.add_argument("--release-probe", type=Path, help=argparse.SUPPRESS)
    context.add_argument(
        "--release-probe-runtime",
        choices=("codex", "claude"),
        help=argparse.SUPPRESS,
    )
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
    library = commands.add_parser(
        "library", help="Create, validate, publish and activate a skill library."
    )
    library_commands = library.add_subparsers(dest="library_command", required=True)
    library_ui = library_commands.add_parser(
        "ui", help="Open the compact Skill Library Manager."
    )
    library_ui.add_argument(
        "--repository",
        type=Path,
        help="Offer a selected SKILL.md folder as the import source.",
    )
    library_ui.add_argument(
        "--register-selected",
        action="store_true",
        help="Validate and register the selected Explorer/Finder folder on launch.",
    )
    library_init = library_commands.add_parser("init")
    library_init.add_argument("--repository", required=True, type=Path)
    library_init.add_argument("--name", default=DEFAULT_REPOSITORY_NAME)
    library_add = library_commands.add_parser("add")
    library_add.add_argument("--repository", required=True, type=Path)
    library_add.add_argument("--skill-id", required=True)
    library_add.add_argument("--display-name", required=True)
    library_add.add_argument("--purpose", required=True)
    library_add.add_argument("--pack-id", required=True)
    library_add.add_argument("--pack-display-name")
    library_add.add_argument("--source", type=Path)
    library_validate = library_commands.add_parser("validate")
    library_validate.add_argument("--repository", required=True, type=Path)
    library_list = library_commands.add_parser("list", help="List registered packs and skills.")
    library_list.add_argument("--repository", required=True, type=Path)
    library_update = library_commands.add_parser("update", help="Update a selected pack or skill.")
    library_update.add_argument("--repository", required=True, type=Path)
    library_update.add_argument("--kind", required=True, choices=("pack", "skill"))
    library_update.add_argument("--id", required=True)
    library_update.add_argument("--source", required=True, type=Path)
    library_delete = library_commands.add_parser("delete", help="Delete a selected pack or skill.")
    library_delete.add_argument("--repository", required=True, type=Path)
    library_delete.add_argument("--kind", required=True, choices=("pack", "skill"))
    library_delete.add_argument("--id", required=True)
    library_delete.add_argument("--confirm", action="store_true")
    library_prepare = library_commands.add_parser("prepare")
    library_prepare.add_argument("--library", "--draft", dest="draft", required=True, type=Path)
    library_prepare.add_argument("--remote", required=True)
    library_prepare.add_argument("--transaction-id")
    library_prepare.add_argument("--branch")
    library_publish = library_commands.add_parser("publish")
    library_publish.add_argument("--transaction-id", required=True)
    library_publish.add_argument("--confirm", action="store_true")
    library_publish.add_argument("--direct", action="store_true")
    library_publish.add_argument("--no-pr", action="store_true")
    library_verify = library_commands.add_parser("verify-merged")
    library_verify.add_argument("--transaction-id", required=True)
    library_activate = library_commands.add_parser("activate")
    library_activate.add_argument("--transaction-id", required=True)
    library_activate.add_argument("--confirm", action="store_true")
    library_activate.add_argument(
        "--platform",
        choices=("windows", "macos"),
        default="windows" if os.name == "nt" else "macos",
    )
    library_status = library_commands.add_parser("status")
    library_status.add_argument("--transaction-id")
    library_recover = library_commands.add_parser(
        "recover", help="Recover an interrupted local transaction."
    )
    library_recover.add_argument("--transaction-id", required=True)
    library_abandon = library_commands.add_parser(
        "abandon", help="Abandon local work without deleting remote GitHub state."
    )
    library_abandon.add_argument("--transaction-id", required=True)
    library_abandon.add_argument("--confirm", action="store_true")
    return parser


def _library_state_dir(args: argparse.Namespace) -> Path:
    return (args.state_dir or Path.home() / ".skill-magnet").resolve()


def _show_library_manager_ui(
    args: argparse.Namespace,
    initial_repository: Path | None = None,
    *,
    register_selected: bool = False,
) -> dict[str, object]:
    def update_menu(config_path: Path, platform: str) -> object:
        if platform == "windows":
            return install_windows_context_menus(config_path)
        return install_context_menu("macos", config_path)

    return show_library_manager(
        config_path=args.config,
        state_dir=_library_state_dir(args),
        initial_repository=initial_repository,
        register_selected=register_selected,
        menu_update=update_menu,
    )


def _run_library_command(args: argparse.Namespace) -> dict[str, object]:
    command = args.library_command
    if command == "ui":
        return _show_library_manager_ui(
            args,
            args.repository,
            register_selected=args.register_selected,
        )
    if command == "init":
        return initialize_library(args.repository, args.name)
    if command == "add":
        return add_skill(
            args.repository,
            skill_id=args.skill_id,
            display_name=args.display_name,
            purpose=args.purpose,
            pack_id=args.pack_id,
            pack_display_name=args.pack_display_name,
            skill_source=args.source,
        )
    if command == "validate":
        return validate_library(args.repository).as_dict()
    if command == "list":
        return library_inventory(args.repository)
    if command == "update":
        if args.kind == "pack":
            return update_pack_source(args.repository, args.id, args.source)
        return update_skill_source(args.repository, args.id, args.source)
    if command == "delete":
        if args.kind == "pack":
            return delete_pack(args.repository, args.id, confirmed=args.confirm)
        return delete_skill(args.repository, args.id, confirmed=args.confirm)
    state_dir = _library_state_dir(args)
    if command == "status" and args.transaction_id is None:
        return list_transactions(state_dir, args.config)
    transaction = LibraryTransaction(state_dir, args.transaction_id)
    if command == "prepare":
        return transaction.prepare(
            draft=args.draft, remote=args.remote, branch=args.branch
        )
    if command == "publish":
        return transaction.publish(
            confirmed=args.confirm,
            direct=args.direct,
            create_pr=not args.no_pr,
        )
    if command == "verify-merged":
        return transaction.mark_merged()
    if command == "recover":
        return transaction.recover()
    if command == "abandon":
        return transaction.abandon(confirmed=args.confirm)
    if command == "activate":
        def update_menu(config_path: Path) -> object:
            if args.platform == "windows":
                return install_windows_context_menus(config_path)
            return install_context_menu("macos", config_path)

        return transaction.activate(
            config_path=args.config,
            confirmed=args.confirm,
            menu_update=update_menu,
        )
    return transaction.status(args.config)


def main(argv: list[str] | None = None) -> int:
    _configure_console_streams()
    args = _parser().parse_args(argv)
    try:
        if args.command == "library":
            result = _run_library_command(args)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        config = Config.load(args.config)
        engine = Engine(config, args.state_dir)
        # Every CLI command is a public product entry. Recover abandoned
        # attempts and expire immutable Desktop handoffs before doing any new
        # work, including read-only status commands.
        ActivationEngine(config, args.state_dir).recover_interrupted_attempts()
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
            raise SkillMagnetError(
                "Persistent skill sync is permanently disabled; skill content is "
                "stored only in the configured GitHub repository"
            )
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
            if args.release_probe is not None:
                if args.platform != "macos":
                    raise SkillMagnetError("Release probe is limited to the macOS adapter")
                probe = args.release_probe.resolve()
                if not probe.is_absolute() or not args.project.resolve().is_dir():
                    raise SkillMagnetError("Invalid macOS release probe request")
                if not config.packs:
                    raise SkillMagnetError("macOS release probe requires a configured pack")
                pack = next(iter(config.packs.values()))
                activation = ActivationEngine(config, args.state_dir)
                probe_runtime = args.release_probe_runtime or "codex"
                purpose = (
                    "Verify the Finder adapter carries the selected project, complete "
                    "package, INDEX, launch contract, and selected runtime handoff semantics."
                )
                plan = activation.plan(
                    platform="macos",
                    project=args.project.resolve(),
                    pack_id=pack.pack_id,
                    runtime=probe_runtime,
                    purpose=purpose,
                )
                contract = activation.confirm(plan, confirmed=True)
                delivered: dict[str, str] = {}

                def capture_delivery(
                    prompt: str, project: str, destination: str
                ) -> None:
                    delivered.update(
                        prompt=prompt,
                        project=project,
                        destination=destination,
                    )

                if probe_runtime == "codex":
                    handoff = deliver_prepared_codex_handoff(
                        activation,
                        contract.contract_id,
                        delivery=capture_delivery,
                    )
                    result_verification = handoff["desktop_result_verification"]
                    answer_completion_claimed = handoff[
                        "answer_completion_claimed"
                    ]
                else:
                    handoff = activation.prepare_claude_desktop_handoff(
                        contract.contract_id
                    )
                    capture_delivery(
                        str(handoff["prompt"]),
                        str(handoff["project"]),
                        str(handoff["destination"]),
                    )
                    result_verification = "not_claimed_by_design"
                    answer_completion_claimed = False
                record = {
                    "schema_version": 1,
                    "adapter": "macos_finder_quick_action",
                    "selected_path": str(args.project.resolve()),
                    "pack_id": contract.pack_id,
                    "commit_sha": contract.commit_sha,
                    "skill_ids": list(contract.skill_ids),
                    "selection_kind": contract.selection_kind,
                    "runtime": contract.runtime,
                    "contract_id": contract.contract_id,
                    "attempt_id": contract.attempt_id,
                    "actual_request_sha256": hashlib.sha256(
                        contract.purpose.encode("utf-8")
                    ).hexdigest(),
                    "instruction_digest": contract.instruction_digest,
                    "index_digest": contract.index_digest,
                    "prompt_sha256": handoff["prompt_sha256"],
                    "status": handoff["status"],
                    "result_verification": result_verification,
                    "handoff_completed": True,
                    "answer_completion_claimed": answer_completion_claimed,
                    "billing_boundary": "existing_plan_no_api_key",
                    "delivery": {
                        "project": delivered.get("project"),
                        "destination": delivered.get("destination"),
                        "prompt_present": bool(delivered.get("prompt")),
                    },
                }
                probe.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with probe.open("x", encoding="utf-8") as handle:
                        json.dump(record, handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                except FileExistsError as exc:
                    raise SkillMagnetError("macOS release probe already exists") from exc
                return 0
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
                        result = deliver_prepared_claude_handoff(
                            activation, contract.contract_id
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
                        library_manager=lambda selected: _show_library_manager_ui(
                            args, selected
                        ),
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
                        result = deliver_prepared_claude_handoff(
                            activation, contract.contract_id
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
                result = uninstall_windows_context_menus()
            else:
                result["classic"] = uninstall_context_menu(args.platform)
        elif args.command == "context-menu-status":
            if args.platform == "windows":
                result = windows_modern_context_menu_status(config=args.config)
            else:
                result = finder_context_menu_status()
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
