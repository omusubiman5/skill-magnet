from __future__ import annotations

import json
import io
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.activation import ActivationEngine
from skill_magnet.cli import exit_process, main as cli_main
from skill_magnet.core import Config, SafetyError, SkillMagnetError
from skill_magnet.platforms import (
    context_menu_spec,
    install_context_menu,
    install_windows_modern_context_menu,
    install_windows_context_menus,
    render_registration,
    uninstall_context_menu,
    uninstall_windows_modern_context_menu,
    windows_modern_context_menu_status,
    windows_background_registry_entries,
    windows_command,
    windows_directory_registry_entries,
    windows_leaf_command_argv,
    windows_menu_leaves,
    render_windows_modern_menu_manifest,
    rollback_windows_context_menus,
)
from skill_magnet.ui import (
    confirm_context_selection,
    context_error_message,
    context_selection_details,
    launch_context_leaf,
)
from tests.e2e_guard import E2ECycleTeardown, assert_e2e_clean


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


class ActivationEndToEndTest(unittest.TestCase):
    def test_pythonw_entrypoint_hard_exits_after_failure_ui_returns(self) -> None:
        if sys.platform != "win32":
            self.skipTest("real pythonw child regression requires Windows")
        import ctypes
        import time
        from ctypes import wintypes

        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "claude"
        )
        command = list(leaf.command)
        if Path(command[0]).name.casefold() != "pythonw.exe":
            self.skipTest("the active Windows Python installation has no pythonw.exe")
        context_index = command.index("context")
        command[context_index:context_index] = ["--state-dir", str(self.state)]
        target_root = self.root / ".e2e-target"
        project = target_root / "pythonw-rejected"
        project.mkdir(parents=True)
        teardown = E2ECycleTeardown(self, target_root=target_root)
        teardown.own_target(project)
        project_index = command.index("--project") + 1
        command[project_index] = str(project)
        command[command.index("--menu-commit") + 1] = "0" * 40
        exact_command_line = subprocess.list2cmdline(command)

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        teardown.track_process(process.pid, [exact_command_line])
        manually_terminated = False
        dialog_seen = False
        try:
            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )

            def close_owned_dialog(hwnd: int, _lparam: int) -> bool:
                nonlocal dialog_seen
                owner_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
                if owner_pid.value != process.pid or not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if title.value == "Skill Magnet":
                    dialog_seen = True
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                return True

            callback = callback_type(close_owned_dialog)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                user32.EnumWindows(callback, 0)
                time.sleep(0.05)
            self.assertTrue(dialog_seen, "pythonw failure dialog did not appear")
            self.assertEqual(
                process.wait(timeout=5),
                2,
                "pythonw did not reach the product hard-exit boundary",
            )
        finally:
            if process.poll() is None:
                manually_terminated = True
                process.terminate()
                process.wait(timeout=5)
        self.assertFalse(manually_terminated, "test had to terminate pythonw manually")

        rejected = list((self.state / "events").glob("*-rejected.json"))
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            json.loads(rejected[0].read_text(encoding="utf-8"))["reason"],
            "stale_menu_commit",
        )
        self.assertFalse((self.state / "launch-contracts").exists())
        self.assertFalse((self.state / "evidence").exists())
        self.assertFalse((self.state / "process-markers").exists())
        self.assertEqual(list(project.iterdir()), [])
        teardown.finish()

    def test_windows_context_failure_returns_without_console_output(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "claude"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("skill_magnet.cli.show_context_error") as error_ui,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "--state-dir",
                    str(self.state),
                    "context",
                    "--platform",
                    "windows",
                    "--project",
                    str(self.project),
                    "--pack",
                    leaf.pack_id,
                    "--skill",
                    leaf.skill_id,
                    "--runtime",
                    leaf.runtime,
                    "--menu-commit",
                    "0" * 40,
                    "--menu-skill-digest",
                    leaf.skill_ids_digest,
                    "--menu-instruction-digest",
                    leaf.instruction_digest,
                    "--menu-acceptance-digest",
                    leaf.acceptance_digest,
                ]
            )
        self.assertEqual(exit_code, 2)
        error_ui.assert_called_once()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_common_teardown_covers_real_terminal_outcome_paths(self) -> None:
        target_root = self.root / ".e2e-target"
        leaves = {
            runtime: next(
                item
                for item in windows_menu_leaves(self.config_path, "%V")
                if item.pack_id == "bounded-pack"
                and item.skill_id == "bounded-answer"
                and item.runtime == runtime
            )
            for runtime in ("codex", "claude")
        }

        def command_line(leaf: object, project: Path, state: Path) -> str:
            command = list(leaf.command)
            command[command.index("--project") + 1] = str(project)
            context_index = command.index("context")
            command[context_index:context_index] = ["--state-dir", str(state)]
            return subprocess.list2cmdline(command)

        for outcome in ("success", "rejected", "failure", "interruption"):
            with self.subTest(outcome=outcome):
                project = target_root / outcome
                project.mkdir(parents=True)
                teardown = E2ECycleTeardown(self, target_root=target_root)
                teardown.own_target(project)
                state = self.root / f"teardown-{outcome}"
                engine = ActivationEngine(self.config, state)
                leaf = leaves["claude" if outcome == "rejected" else "codex"]
                arguments = {
                    "platform": "windows",
                    "project": project,
                    "pack_id": leaf.pack_id,
                    "skill_id": leaf.skill_id,
                    "runtime": leaf.runtime,
                    "menu_commit": self.commit,
                    "menu_skill_digest": leaf.skill_ids_digest,
                    "menu_instruction_digest": leaf.instruction_digest,
                    "menu_acceptance_digest": leaf.acceptance_digest,
                }
                if outcome == "rejected":
                    arguments["menu_commit"] = "0" * 40
                if outcome == "success":
                    result = launch_context_leaf(
                        engine, **arguments, codex_executable=self.fake_codex
                    )
                    self.assertEqual(result["status"], "verified_applied")
                elif outcome == "rejected":
                    with self.assertRaises(Exception):
                        launch_context_leaf(
                            engine,
                            **arguments,
                            codex_executable=self.fake_codex,
                            error_ui=lambda _message: None,
                        )
                elif outcome == "failure":
                    with self.assertRaises(Exception):
                        launch_context_leaf(
                            engine,
                            **arguments,
                            codex_executable=str(self.root / "missing-codex.exe"),
                            error_ui=lambda _message: None,
                        )
                else:
                    original_run = subprocess.run

                    def interrupt_codex(*args: object, **kwargs: object) -> object:
                        command = args[0]
                        if isinstance(command, list) and command and command[0] == "git":
                            return original_run(*args, **kwargs)
                        raise KeyboardInterrupt("injected forced interruption")

                    with mock.patch(
                        "skill_magnet.activation.subprocess.run",
                        side_effect=interrupt_codex,
                    ):
                        with self.assertRaises(KeyboardInterrupt):
                            launch_context_leaf(
                                engine,
                                **arguments,
                                codex_executable=self.fake_codex,
                            )
                    recovered = ActivationEngine(self.config, state)
                    recovered.plan(
                        platform="windows",
                        project=project,
                        pack_id=leaf.pack_id,
                        runtime="codex",
                        purpose="common teardown recovery",
                    )
                    negative = list((state / "evidence").glob("*-not-guaranteed.json"))
                    self.assertEqual(len(negative), 1)
                    self.assertEqual(
                        json.loads(negative[0].read_text(encoding="utf-8"))["status"],
                        "interrupted",
                    )

                self.assertEqual(list(project.rglob("*")), [])
                teardown.command_lines.append(command_line(leaf, project, state))
                teardown.finish()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "separate-user-skill-repository"
        skill = self.repo / "bounded-answer"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: bounded-answer\n"
            "description: Return the bounded decision required by this test.\n"
            "---\n\n"
            "Always set result.decision to bounded.\n",
            encoding="utf-8",
        )
        (skill / "acceptance.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "assertions": [
                        {"path": "result.decision", "equals": "bounded"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        unused = self.repo / "unused-skill"
        unused.mkdir()
        (unused / "SKILL.md").write_text(
            "---\nname: unused-skill\ndescription: Must never enter the selected task.\n"
            "---\n\nUNUSED_SENTINEL\n",
            encoding="utf-8",
        )
        (unused / "acceptance.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "assertions": [{"path": "result.unused", "equals": True}],
                }
            ),
            encoding="utf-8",
        )
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Skill Magnet Test")
        git(self.repo, "config", "user.email", "skill-magnet@example.invalid")
        git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://github.com/my-owner/separate-skill-repo.git",
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "verified skill version")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        self.project = self.root / "target-project"
        self.project.mkdir()
        self.state = self.root / "state"
        self.config_path = self.root / "skill-magnet.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["my-owner"],
                    "state_dir": str(self.state),
                    "targets": {
                        "codex": str(self.root / "must-not-install-codex"),
                        "claude": str(self.root / "must-not-install-claude"),
                    },
                    "packs": [
                        {
                            "id": "bounded-pack",
                            "repo_url": "https://github.com/my-owner/separate-skill-repo.git",
                            "expected_commit": self.commit,
                            "source": str(self.repo),
                            "skills": ["bounded-answer"],
                            "approved_by": "test-user",
                            "approved_at": "2026-08-22T00:00:00+00:00",
                            "purpose": "Produce a machine-verifiable bounded decision.",
                        },
                        {
                            "id": "unused-pack",
                            "repo_url": "https://github.com/my-owner/separate-skill-repo.git",
                            "expected_commit": self.commit,
                            "source": str(self.repo),
                            "skills": ["unused-skill"],
                            "approved_by": "test-user",
                            "approved_at": "2026-08-22T00:00:00+00:00",
                            "purpose": "Remain unselected.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.config = Config.load(self.config_path)
        self.fake_codex = self._fake_codex()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _fake_codex(self) -> tuple[str, ...]:
        script = self.root / "fake_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "prompt = sys.stdin.read() if args[-1] == '-' else args[-1]\n"
            "assert 'UNUSED_SENTINEL' not in prompt\n"
            "line = next(x for x in prompt.splitlines() if x.startswith('PROVENANCE='))\n"
            "provenance = json.loads(line.split('=', 1)[1])\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output = {'evidence': {**provenance, 'applied_rules': "
            "['bounded-answer:result.decision=bounded']}, "
            "'result': {'decision': 'bounded'}}\n"
            "output_path.write_text(json.dumps(output), encoding='utf-8')\n"
            "print(json.dumps({'type': 'task.completed'}))\n",
            encoding="utf-8",
        )
        return (sys.executable, str(script))

    def _plan(self, engine: ActivationEngine, platform: str = "windows") -> dict:
        return engine.plan(
            platform=platform,
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="Make a bounded decision",
            ttl_minutes=30,
        )

    def test_cross_platform_manual_selection_to_verified_application_e2e(self) -> None:
        self.assertFalse(self.state.exists())
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform):
                engine = ActivationEngine(self.config, self.state)
                plan = self._plan(engine, platform)
                self.assertFalse(plan["writes"])
                self.assertFalse(plan["local_skill_placement"])
                contract = engine.confirm(plan, confirmed=True)
                result = engine.execute(
                    contract.contract_id, codex_executable=self.fake_codex
                )
                self.assertEqual(result["status"], "verified_applied")
                self.assertEqual(result["commit_sha"], self.commit)
                self.assertEqual(
                    result["output"]["result"]["decision"], "bounded"
                )
                with self.assertRaises(SafetyError):
                    engine.execute(contract.contract_id, codex_executable=self.fake_codex)
                self.assertEqual(
                    list((self.state / "evidence").glob("*-schema.json")), []
                )
                self.assertEqual(
                    result["terminal_event"],
                    {"status": "verified_applied", "terminal": True},
                )
                self.assertFalse(
                    (
                        self.state
                        / "evidence"
                        / f"{contract.contract_id}-output.json"
                    ).exists()
                )
                self.assertFalse(
                    (
                        self.state
                        / "evidence"
                        / f"{contract.contract_id}-events.jsonl"
                    ).exists()
                )
        self.assertFalse((self.root / "must-not-install-codex").exists())
        self.assertFalse((self.root / "must-not-install-claude").exists())

    def test_web_claude_leaf_delivers_one_skill_and_target_prompt_without_cli(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "claude"
        )
        delivered: list[tuple[str, str]] = []
        pack = self.config.packs[leaf.pack_id]
        with mock.patch.object(
            engine, "execute", side_effect=AssertionError("CLI execution is forbidden")
        ):
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime=leaf.runtime,
                menu_commit=pack.expected_commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                destination="web",
                web_delivery=lambda prompt, url: delivered.append((prompt, url)),
            )
        self.assertEqual(result["status"], "web_prompt_ready")
        self.assertEqual(result["runtime"], "claude")
        self.assertEqual(len(delivered), 1)
        prompt, url = delivered[0]
        self.assertEqual(url, "https://claude.ai/new")
        self.assertIn(f"TARGET_PROJECT={self.project.resolve()}", prompt)
        self.assertIn("bounded-answer", prompt)
        self.assertNotIn("UNUSED_SENTINEL", prompt)
        self.assertNotIn("prompt", result)
        with self.assertRaisesRegex(SafetyError, "already used"):
            engine.prepare_web_handoff(str(result["contract_id"]))

    def test_web_codex_leaf_isolated_fail_closed_without_contract_or_delivery(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "codex"
        )
        pack = self.config.packs[leaf.pack_id]
        delivered: list[tuple[str, str]] = []
        errors: list[str] = []
        with self.assertRaisesRegex(SkillMagnetError, "No ChatGPT or terminal fallback"):
            launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime=leaf.runtime,
                menu_commit=pack.expected_commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                destination="web",
                web_delivery=lambda prompt, url: delivered.append((prompt, url)),
                error_ui=errors.append,
            )
        self.assertEqual(delivered, [])
        self.assertEqual(len(errors), 1)
        self.assertFalse((self.state / "launch-contracts").exists())
        rejected = list((self.state / "events").glob("*-rejected.json"))
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            json.loads(rejected[0].read_text(encoding="utf-8"))["reason"],
            "web_codex_destination_unavailable",
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows cmd wrapper contract")
    def test_windows_cmd_runtime_preserves_special_project_as_one_argv(self) -> None:
        special_project = self.root / "SM INT 002 日本語 & ( ) ' ! ^ # %"
        special_project.mkdir()
        script = self.root / "fake_cmd_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"assert args[args.index('--cd') + 1] == {str(special_project)!r}\n"
            "prompt = sys.stdin.read() if args[-1] == '-' else args[-1]\n"
            "line = next(x for x in prompt.splitlines() if x.startswith('PROVENANCE='))\n"
            "provenance = json.loads(line.split('=', 1)[1])\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(json.dumps({'evidence': {**provenance, "
            "'applied_rules': ['bounded-answer:result.decision=bounded']}, "
            "'result': {'decision': 'bounded'}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        wrapper = self.root / "fake_codex.cmd"
        wrapper.write_text(
            f'@"{sys.executable}" "{script}" %*\n',
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=special_project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="Make a bounded decision",
            ttl_minutes=30,
        )
        contract = engine.confirm(plan, confirmed=True)
        result = engine.execute(contract.contract_id, codex_executable=str(wrapper))

        self.assertEqual(result["status"], "verified_applied")
        self.assertEqual(result["output"]["result"]["decision"], "bounded")

    def test_user_confirmation_is_mandatory(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        with self.assertRaises(SafetyError):
            engine.confirm(self._plan(engine), confirmed=False)
        self.assertFalse(self.state.exists())

    def test_legacy_persistent_sync_is_unreachable_by_default_cli(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "sync",
                    "--pack",
                    "bounded-pack",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("disabled by default", error.getvalue())
        self.assertFalse((self.root / "must-not-install-codex").exists())
        self.assertFalse((self.root / "must-not-install-claude").exists())

    def test_missing_acceptance_check_fails_closed_before_contract(self) -> None:
        (self.repo / "bounded-answer" / "acceptance.json").unlink()
        git(self.repo, "add", "-u")
        git(self.repo, "commit", "-m", "remove acceptance")
        self.config_path.write_text(
            self.config_path.read_text(encoding="utf-8").replace(
                self.commit, git(self.repo, "rev-parse", "HEAD")
            ),
            encoding="utf-8",
        )
        engine = ActivationEngine(Config.load(self.config_path), self.state)
        with self.assertRaises(SafetyError):
            self._plan(engine)
        self.assertFalse(self.state.exists())

    def test_expired_contract_fails_closed_without_running_codex(self) -> None:
        current = datetime(2026, 8, 22, tzinfo=timezone.utc)
        engine = ActivationEngine(self.config, self.state, now=lambda: current)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        expired = ActivationEngine(
            self.config,
            self.state,
            now=lambda: current + timedelta(minutes=31),
        )
        with self.assertRaises(SafetyError):
            expired.execute(contract.contract_id, codex_executable="does-not-exist")

    def test_codex_launch_failure_retains_negative_artifacts_only(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaisesRegex(SafetyError, "could not be started"):
            engine.execute(
                contract.contract_id,
                codex_executable=str(self.root / "missing-codex-executable"),
            )

        contract_path = (
            self.state / "launch-contracts" / f"{contract.contract_id}.json"
        )
        failure_path = (
            self.state
            / "evidence"
            / f"{contract.contract_id}-not-guaranteed.json"
        )
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertTrue(contract_path.is_file())
        self.assertIn(
            "consumed_at",
            json.loads(contract_path.read_text(encoding="utf-8")),
        )
        self.assertEqual(failure["status"], "launch_failed")
        self.assertEqual(
            failure["terminal_event"],
            {"status": "launch_failed", "terminal": True},
        )
        self.assertEqual(failure["output_schema_evidence"]["version"], 1)
        self.assertEqual(list((self.state / "evidence").glob("*-verified.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-schema.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-output.json")), [])

    def test_tampered_contract_fails_integrity_check(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        path = self.state / "launch-contracts" / f"{contract.contract_id}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["purpose"] = "tampered purpose"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(SafetyError):
            engine.execute(contract.contract_id, codex_executable=self.fake_codex)

    def test_application_mismatch_is_not_reported_as_success(self) -> None:
        bad_script = self.root / "bad_codex.py"
        bad_script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read() if a[-1]=='-' else a[-1]; line=next(x for x in p.splitlines() "
            "if x.startswith('PROVENANCE=')); e=json.loads(line.split('=',1)[1]); "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text(json.dumps({'evidence':{**e,'applied_rules':['claimed']},"
            "'result':{'decision':'unbounded'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        bad = (sys.executable, str(bad_script))
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaises(SafetyError):
            engine.execute(contract.contract_id, codex_executable=bad)
        self.assertFalse(
            (self.state / "evidence" / f"{contract.contract_id}-verified.json").exists()
        )
        failure = self.state / "evidence" / f"{contract.contract_id}-not-guaranteed.json"
        self.assertTrue(failure.is_file())
        self.assertEqual(
            json.loads(failure.read_text(encoding="utf-8"))["status"],
            "acceptance_failed",
        )
        self.assertEqual(list((self.state / "evidence").glob("*-output.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-events.jsonl")), [])

    def test_cleanup_failure_is_the_only_terminal_and_never_verified(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        original_cleanup = engine._cleanup_temporary_artifacts

        def fail_cleanup(paths: tuple[Path, ...]) -> None:
            original_cleanup(paths)
            paths[-1].mkdir()
            original_cleanup(paths)

        engine._cleanup_temporary_artifacts = fail_cleanup  # type: ignore[method-assign]
        with self.assertRaisesRegex(SafetyError, "cleanup failed"):
            engine.execute(contract.contract_id, codex_executable=self.fake_codex)

        verified_path = (
            self.state / "evidence" / f"{contract.contract_id}-verified.json"
        )
        failure_path = (
            self.state
            / "evidence"
            / f"{contract.contract_id}-not-guaranteed.json"
        )
        self.assertFalse(verified_path.exists())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["status"], "cleanup_failed")
        self.assertEqual(
            failure["terminal_event"],
            {"status": "cleanup_failed", "terminal": True},
        )
        terminals = [
            path
            for path in (verified_path, failure_path)
            if path.exists()
        ]
        self.assertEqual(terminals, [failure_path])

    def test_invalid_output_is_sanitized_and_raw_artifacts_are_deleted(self) -> None:
        script = self.root / "invalid_output_codex.py"
        script.write_text(
            "import pathlib, sys\n"
            "a=sys.argv[1:]; sys.stdin.read(); "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text('SECRET_RAW_OUTPUT:not-json', encoding='utf-8')\n",
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaisesRegex(SafetyError, "no valid evidence envelope"):
            engine.execute(
                contract.contract_id,
                codex_executable=(sys.executable, str(script)),
            )

        failure_path = (
            self.state
            / "evidence"
            / f"{contract.contract_id}-not-guaranteed.json"
        )
        failure_text = failure_path.read_text(encoding="utf-8")
        failure = json.loads(failure_text)
        self.assertEqual(failure["status"], "output_failed")
        self.assertEqual(
            failure["terminal_event"],
            {"status": "output_failed", "terminal": True},
        )
        self.assertNotIn("SECRET_RAW_OUTPUT", failure_text)
        self.assertEqual(list((self.state / "evidence").glob("*-verified.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-output.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-events.jsonl")), [])

    def test_new_public_entry_recovers_interruption_exactly_once(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "codex"
        )
        original_run = subprocess.run

        def interrupt_codex(*args: object, **kwargs: object) -> object:
            command = args[0]
            if isinstance(command, list) and command and command[0] == "git":
                return original_run(*args, **kwargs)
            raise KeyboardInterrupt("injected forced interruption")

        with mock.patch(
            "skill_magnet.activation.subprocess.run",
            side_effect=interrupt_codex,
        ):
            with self.assertRaises(KeyboardInterrupt):
                launch_context_leaf(
                    engine,
                    platform="windows",
                    project=self.project,
                    pack_id=leaf.pack_id,
                    skill_id=leaf.skill_id,
                    runtime=leaf.runtime,
                    menu_commit=self.commit,
                    menu_skill_digest=leaf.skill_ids_digest,
                    menu_instruction_digest=leaf.instruction_digest,
                    menu_acceptance_digest=leaf.acceptance_digest,
                    codex_executable=self.fake_codex,
                )

        contracts = list((self.state / "launch-contracts").glob("*.json"))
        self.assertEqual(len(contracts), 1)
        contract_id = contracts[0].stem

        marker = (
            self.state
            / "process-markers"
            / f"{contract_id}-process.json"
        )
        self.assertTrue(marker.is_file())
        self.assertTrue(
            (self.state / "evidence" / f"{contract_id}-schema.json").is_file()
        )

        recovered_engine = ActivationEngine(self.config, self.state)
        recovered_engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="trigger public recovery",
        )
        failure_path = (
            self.state
            / "evidence"
            / f"{contract_id}-not-guaranteed.json"
        )
        first_bytes = failure_path.read_bytes()
        failure = json.loads(first_bytes.decode("utf-8"))
        self.assertEqual(failure["status"], "interrupted")
        self.assertEqual(
            failure["terminal_event"],
            {"status": "interrupted", "terminal": True},
        )
        self.assertTrue(
            (
                self.state
                / "launch-contracts"
                / f"{contract_id}.json"
            ).is_file()
        )
        self.assertFalse(marker.exists())
        self.assertEqual(list((self.state / "process-markers").glob("*-process.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-schema.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-output.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-events.jsonl")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-verified.json")), [])
        self.assertEqual(
            list((self.state / "evidence").glob("*-not-guaranteed.json")),
            [failure_path],
        )
        lifecycle = self.state / "events" / f"{contract_id}-lifecycle.jsonl"
        lifecycle_events = [
            json.loads(line)
            for line in lifecycle.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(len(lifecycle_events), 1)
        self.assertEqual(lifecycle_events[0]["status"], "interrupted")
        self.assertEqual(lifecycle_events[0]["attempt_id"], failure["attempt_id"])
        self.assertEqual(
            lifecycle_events[0]["terminal_event_id"], failure["terminal_event_id"]
        )

        recovered_engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="prove idempotent recovery",
        )
        self.assertEqual(failure_path.read_bytes(), first_bytes)
        self.assertEqual(
            list((self.state / "evidence").glob("*-not-guaranteed.json")),
            [failure_path],
        )
        self.assertEqual(
            [
                json.loads(line)
                for line in lifecycle.read_text(encoding="utf-8").splitlines()
                if line
            ],
            lifecycle_events,
        )

    def test_artifact_retention_table_for_every_terminal_outcome(self) -> None:
        invalid_script = self.root / "table_invalid_output.py"
        invalid_script.write_text(
            "import pathlib, sys\n"
            "a=sys.argv[1:]; sys.stdin.read(); "
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text("
            "'raw-not-json', encoding='utf-8')\n",
            encoding="utf-8",
        )
        acceptance_script = self.root / "table_acceptance_failure.py"
        acceptance_script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read(); "
            "e=json.loads(next(x for x in p.splitlines() if x.startswith('PROVENANCE=')).split('=',1)[1]); "
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text("
            "json.dumps({'evidence':{**e,'applied_rules':['bounded-answer:wrong']},"
            "'result':{'decision':'wrong'}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        table = (
            ("success", "verified_applied", True, False, False, True),
            ("preflight", "rejected", False, False, True, True),
            ("launch", "launch_failed", True, True, False, True),
            ("output", "output_failed", True, True, False, True),
            ("acceptance", "acceptance_failed", True, True, False, True),
            ("cleanup", "cleanup_failed", True, True, False, False),
            ("interrupted", "interrupted", True, True, False, True),
        )
        original_run = subprocess.run

        for name, status, has_contract, has_negative, has_rejection, temp_clean in table:
            with self.subTest(outcome=name):
                state = self.root / f"artifact-table-{name}"
                engine = ActivationEngine(self.config, state)
                contract = None
                if name == "preflight":
                    with self.assertRaises(Exception):
                        context_selection_details(
                            engine,
                            project=self.project,
                            pack_id="bounded-pack",
                            runtime="codex",
                            menu_commit="0" * 40,
                        )
                else:
                    contract = engine.confirm(self._plan(engine), confirmed=True)
                    if name == "success":
                        engine.execute(
                            contract.contract_id, codex_executable=self.fake_codex
                        )
                    elif name == "launch":
                        with self.assertRaises(SafetyError):
                            engine.execute(
                                contract.contract_id,
                                codex_executable=str(self.root / "missing-table-codex"),
                            )
                    elif name == "output":
                        with self.assertRaises(SafetyError):
                            engine.execute(
                                contract.contract_id,
                                codex_executable=(sys.executable, str(invalid_script)),
                            )
                    elif name == "acceptance":
                        with self.assertRaises(SafetyError):
                            engine.execute(
                                contract.contract_id,
                                codex_executable=(sys.executable, str(acceptance_script)),
                            )
                    elif name == "cleanup":
                        original_cleanup = engine._cleanup_temporary_artifacts

                        def fail_cleanup(paths: tuple[Path, ...]) -> None:
                            original_cleanup(paths)
                            paths[-1].mkdir()
                            original_cleanup(paths)

                        engine._cleanup_temporary_artifacts = fail_cleanup  # type: ignore[method-assign]
                        with self.assertRaises(SafetyError):
                            engine.execute(
                                contract.contract_id,
                                codex_executable=self.fake_codex,
                            )
                    else:
                        def interrupt_codex(*args: object, **kwargs: object) -> object:
                            command = args[0]
                            if isinstance(command, list) and command and command[0] == "git":
                                return original_run(*args, **kwargs)
                            raise KeyboardInterrupt("table interruption")

                        with mock.patch(
                            "skill_magnet.activation.subprocess.run",
                            side_effect=interrupt_codex,
                        ):
                            with self.assertRaises(KeyboardInterrupt):
                                engine.execute(
                                    contract.contract_id,
                                    codex_executable=self.fake_codex,
                                )
                        ActivationEngine(self.config, state).plan(
                            platform="windows",
                            project=self.project,
                            pack_id="bounded-pack",
                            runtime="codex",
                            purpose="recover table interruption",
                        )

                contracts = list((state / "launch-contracts").glob("*.json"))
                verified = list((state / "evidence").glob("*-verified.json"))
                negative = list(
                    (state / "evidence").glob("*-not-guaranteed.json")
                )
                rejected = list((state / "events").glob("*-rejected.json"))
                temporary = [
                    *list((state / "evidence").glob("*-schema.json")),
                    *list((state / "evidence").glob("*-output.json")),
                    *list((state / "evidence").glob("*-events.jsonl")),
                    *list((state / "process-markers").glob("*-process.json")),
                ]
                self.assertEqual(bool(contracts), has_contract)
                self.assertEqual(bool(negative), has_negative)
                self.assertEqual(bool(rejected), has_rejection)
                self.assertEqual(not temporary, temp_clean)
                if name in {"preflight", "claude"}:
                    self.assertFalse((state / "evidence").exists())
                    self.assertFalse((state / "process-markers").exists())
                if name == "cleanup":
                    self.assertEqual(
                        list((state / "evidence").glob("*-output.json")), []
                    )
                    self.assertEqual(
                        list((state / "evidence").glob("*-events.jsonl")), []
                    )
                if status == "verified_applied":
                    self.assertEqual(len(verified), 1)
                    payload = json.loads(verified[0].read_text(encoding="utf-8"))
                elif status == "rejected":
                    self.assertEqual(verified, [])
                    payload = json.loads(rejected[0].read_text(encoding="utf-8"))
                else:
                    self.assertEqual(verified, [])
                    payload = json.loads(negative[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], status)

    def test_applied_rules_must_identify_every_selected_skill(self) -> None:
        script = self.root / "unidentified_rule_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read(); line=next(x for x in p.splitlines() "
            "if x.startswith('PROVENANCE=')); e=json.loads(line.split('=',1)[1]); "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text(json.dumps({'evidence':{**e,'applied_rules':['generic rule']},"
            "'result':{'decision':'bounded'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaisesRegex(SafetyError, "does not identify selected skill"):
            engine.execute(
                contract.contract_id,
                codex_executable=(sys.executable, str(script)),
            )
        self.assertTrue(
            (
                self.state
                / "evidence"
                / f"{contract.contract_id}-not-guaranteed.json"
            ).is_file()
        )

    def test_wrong_challenge_nonce_is_not_read_evidence(self) -> None:
        script = self.root / "wrong_nonce_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read(); line=next(x for x in p.splitlines() "
            "if x.startswith('PROVENANCE=')); e=json.loads(line.split('=',1)[1]); "
            "e['challenge_nonce']='stale'; "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text(json.dumps({'evidence':{**e,'applied_rules':['claimed']},"
            "'result':{'decision':'bounded'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaises(SafetyError):
            engine.execute(
                contract.contract_id,
                codex_executable=(sys.executable, str(script)),
            )
        self.assertTrue(
            (
                self.state
                / "evidence"
                / f"{contract.contract_id}-not-guaranteed.json"
            ).is_file()
        )

    def test_windows_and_macos_context_specs_have_identical_safety_flow(self) -> None:
        windows = context_menu_spec("windows", self.config_path).as_dict()
        macos = context_menu_spec("macos", self.config_path).as_dict()
        self.assertEqual(windows["required_flow"], macos["required_flow"])
        self.assertFalse(windows["automatic_activation"])
        self.assertFalse(macos["automatic_activation"])
        self.assertIn("windows_explorer", windows["integration"])
        self.assertIn("macos_finder", macos["integration"])
        registration = render_registration("windows", self.config_path)
        self.assertIn("HKEY_CURRENT_USER", registration)
        self.assertIn("Pack: bounded-pack", registration)
        self.assertIn("Skill: bounded-answer", registration)
        self.assertIn('"MUIVerb"="Codex"', registration)
        self.assertIn('"MUIVerb"="Claude"', registration)
        self.assertIn("Finder Quick Action", render_registration("macos", self.config_path))

    def test_windows_individual_skill_leaves_fix_skill_runtime_and_digests(self) -> None:
        leaves = windows_menu_leaves(self.config_path, "%V")
        self.assertEqual(len(leaves), 4)
        self.assertEqual(
            {(leaf.pack_id, leaf.skill_id, leaf.runtime) for leaf in leaves},
            {
                ("bounded-pack", "bounded-answer", "codex"),
                ("bounded-pack", "bounded-answer", "claude"),
                ("unused-pack", "unused-skill", "codex"),
                ("unused-pack", "unused-skill", "claude"),
            },
        )
        for leaf in leaves:
            self.assertIn("--pack", leaf.command)
            self.assertIn("--skill", leaf.command)
            self.assertIn("--runtime", leaf.command)
            self.assertIn("--menu-commit", leaf.command)
            self.assertIn("--menu-skill-digest", leaf.command)
            self.assertIn("--menu-instruction-digest", leaf.command)
            self.assertIn("--menu-acceptance-digest", leaf.command)
            self.assertEqual(leaf.pack_label, f"Pack: {leaf.pack_id}")
            self.assertEqual(leaf.skill_label, f"Skill: {leaf.skill_id}")

    def test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf(self) -> None:
        product_config = Path(__file__).resolve().parents[1] / "skill-magnet.json"
        leaves = windows_menu_leaves(product_config, "%1")
        expected_digests = {
            "codex-auth-boundary-selection": ("90c8229b22400e8e08f31cd4b7d808fafe808bd35fa1d72235f40da7db1f072e", "a993d08c8dfd9e4739bd9e6150772e2c45a67c40778d0051a35ceecdad27d92d"),
            "codex-bounded-subagents": ("48094148e2ff65d16636a69b40e6ece8ce23361b308547b867d40d50a3a40c15", "da393eec33b11f8d32dfb5fc68b744847f996e69d29cc9921708272d7eea1e47"),
            "codex-ci-patch-handoff": ("47bf82785f32845faefc6df75c8c0bec925e8b592b6d0eebd93264f833f404db", "8b888999ad3cda87853e1f346a5386ac2adeca1230ab0cc00da70ab618e2a4b8"),
            "codex-context-entry-routing": ("d2bf420e41bcd807761ddcdfb90f9f0580bbf362f6ea66290b28fb6ef7656220", "414b8020359e9524c99b6f33fcb90e97149fb8fff5ec936576acff0bd8ccdea6"),
            "codex-egress-surface-governance": ("d28c14f3d3b8ad732a9b65860f6712026988ec01c13da90725bade110ce4bf54", "928ab2a7112a4e3620fe373e170d64b2dc9490afcfe0822d66687bd404c178db"),
            "codex-exec-io-contract": ("0ea30bdcfa9b75fc5a2914d112f026c00d8b9619416f0adf9b46da36d36072bc", "4df5b00136f7456a67761f910cc93e31b57cf6b9fc7621b607dc2a3ac765862d"),
            "codex-execution-mode-routing": ("173efc6e251be04266dcea9ab44bb2390337f6dec267881a1f5565fecd0754c0", "f63c268df7677d9f051d47aec7476961e55694d50a7b738dbc02236cf838fea8"),
            "codex-mcp-control-plane": ("e48a4464a30a1f0c18dc81052353918b183b6d89f91f6b1f2569e0496a40ed53", "4decbc6a5661eba06007372c0969a88c61afc15fa57f7471c11b63b6c8f1483e"),
            "codex-sandbox-approval-boundary": ("e7a9def6a153946f6481412362ee7afaac99552ee7d7d9096b51a788887b0c05", "b4df1deeebf2a0cfa03e93c19a59a90457f39279c51bf56d3ecc5003592e386d"),
        }
        self.assertEqual(len(leaves), 18)
        self.assertEqual({leaf.skill_id for leaf in leaves}, set(expected_digests))
        self.assertTrue(
            all(
                {leaf.runtime for leaf in leaves if leaf.skill_id == skill_id}
                == {"codex", "claude"}
                for skill_id in {leaf.skill_id for leaf in leaves}
            )
        )
        for leaf in leaves:
            self.assertEqual(
                (leaf.instruction_digest, leaf.acceptance_digest),
                expected_digests[leaf.skill_id],
            )
            self.assertEqual(
                leaf.command[leaf.command.index("--skill") + 1], leaf.skill_id
            )
            self.assertEqual(
                leaf.command[leaf.command.index("--menu-instruction-digest") + 1],
                leaf.instruction_digest,
            )
            self.assertEqual(
                leaf.command[leaf.command.index("--menu-acceptance-digest") + 1],
                leaf.acceptance_digest,
            )
        for root_name, entry_builder in (
            ("Directory", windows_directory_registry_entries),
            ("Background", windows_background_registry_entries),
        ):
            with self.subTest(root=root_name):
                entries = entry_builder(product_config)
                command_keys = [
                    key for key, _, _ in entries if key.endswith(r"\command")
                ]
                self.assertEqual(len(command_keys), 18)
                self.assertTrue(
                    all(
                        r"\shell\skill-" in key and r"\shell\runtime-" in key
                        for key in command_keys
                    )
                )
                skill_labels = {
                    value
                    for _, name, value in entries
                    if name == "MUIVerb" and value.startswith("Skill: ")
                }
                self.assertEqual(
                    skill_labels,
                    {f"Skill: {skill_id}" for skill_id in expected_digests},
                )

    def test_both_roots_propagate_one_skill_contract_and_reject_leaf_tampering(self) -> None:
        product_config = Path(__file__).resolve().parents[1] / "skill-magnet.json"
        config = Config.load(product_config)
        purpose = config.packs["codex-pmo-skills"].purpose
        cases = (("Directory", "%1"), ("Background", "%V"))
        for root_name, placeholder in cases:
            with self.subTest(root=root_name):
                state = self.root / f"individual-contract-{root_name}"
                engine = ActivationEngine(config, state)
                leaf = next(
                    item
                    for item in windows_menu_leaves(product_config, placeholder)
                    if item.skill_id == "codex-auth-boundary-selection"
                    and item.runtime == "codex"
                )
                details = context_selection_details(
                    engine,
                    project=self.project,
                    pack_id=leaf.pack_id,
                    skill_id=leaf.skill_id,
                    runtime=leaf.runtime,
                    menu_commit=config.packs[leaf.pack_id].expected_commit,
                    menu_skill_digest=leaf.skill_ids_digest,
                    menu_instruction_digest=leaf.instruction_digest,
                    menu_acceptance_digest=leaf.acceptance_digest,
                )
                self.assertEqual(details["selection_kind"], "skill")
                self.assertEqual(details["selected_skill_id"], leaf.skill_id)
                self.assertEqual(details["skill_ids"], (leaf.skill_id,))
                self.assertEqual(details["instruction_digest"], leaf.instruction_digest)
                self.assertEqual(details["acceptance_digest"], leaf.acceptance_digest)
                self.assertEqual(details["runtime"], "codex")
                self.assertEqual(details["purpose"], purpose)

                contract = confirm_context_selection(
                    engine,
                    platform="windows",
                    details=details,
                    purpose=purpose,
                    confirmed=True,
                )
                self.assertIsNotNone(contract)
                assert contract is not None
                self.assertEqual(contract.selection_kind, "skill")
                self.assertEqual(contract.selected_skill_id, leaf.skill_id)
                self.assertEqual(contract.skill_ids, (leaf.skill_id,))
                self.assertEqual(contract.instruction_digest, leaf.instruction_digest)
                self.assertEqual(
                    contract.acceptance_digests,
                    {leaf.skill_id: leaf.acceptance_digest},
                )
                self.assertEqual(contract.runtime, "codex")
                self.assertEqual(contract.commit_sha, config.packs[leaf.pack_id].expected_commit)
                self.assertEqual(contract.purpose, purpose)

        selected, other = (
            item
            for item in windows_menu_leaves(product_config, "%1")
            if item.runtime == "codex"
            and item.skill_id
            in {"codex-auth-boundary-selection", "codex-bounded-subagents"}
        )
        engine = ActivationEngine(config, self.root / "tampered-individual-contract")
        common = {
            "project": self.project,
            "pack_id": selected.pack_id,
            "skill_id": selected.skill_id,
            "runtime": "codex",
            "menu_commit": config.packs[selected.pack_id].expected_commit,
            "menu_skill_digest": selected.skill_ids_digest,
        }
        for field, value, reason in (
            ("menu_instruction_digest", other.instruction_digest, "instructions"),
            ("menu_acceptance_digest", other.acceptance_digest, "acceptance"),
            ("menu_commit", "0" * 40, "version"),
        ):
            with self.subTest(tamper=field):
                values = {
                    **common,
                    "menu_instruction_digest": selected.instruction_digest,
                    "menu_acceptance_digest": selected.acceptance_digest,
                    field: value,
                }
                with self.assertRaisesRegex(Exception, reason):
                    context_selection_details(engine, **values)
        with self.assertRaisesRegex(Exception, "Unknown skill"):
            context_selection_details(
                engine,
                **{**common, "skill_id": "not-in-pack"},
                menu_instruction_digest=selected.instruction_digest,
                menu_acceptance_digest=selected.acceptance_digest,
            )
        claude = context_selection_details(
            engine,
            **{**common, "runtime": "claude"},
            menu_instruction_digest=selected.instruction_digest,
            menu_acceptance_digest=selected.acceptance_digest,
        )
        claude_contract = confirm_context_selection(
            engine,
            platform="windows",
            details=claude,
            purpose=purpose,
            confirmed=True,
        )
        self.assertIsNotNone(claude_contract)
        self.assertEqual(claude_contract.runtime, "claude")

    def test_explicit_individual_leaf_success_is_silent_verified_and_clean(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "codex"
        )
        engine = ActivationEngine(self.config, self.state)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime=leaf.runtime,
                menu_commit=self.commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                codex_executable=self.fake_codex,
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result["status"], "verified_applied")
        self.assertEqual(result["skill_read_evidence"]["skill_ids"], ["bounded-answer"])
        self.assertEqual(
            set(result["skill_specific_application_evidence"]), {"bounded-answer"}
        )
        self.assertEqual(result["terminal_event"]["status"], "verified_applied")
        contract_id = result["contract_id"]
        lifecycle = self.state / "events" / f"{contract_id}-lifecycle.jsonl"
        lifecycle_events = [
            json.loads(line)
            for line in lifecycle.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(len(lifecycle_events), 1)
        self.assertEqual(lifecycle_events[0]["attempt_id"], result["attempt_id"])
        self.assertEqual(
            lifecycle_events[0]["terminal_event_id"], result["terminal_event_id"]
        )
        self.assertEqual(lifecycle_events[0]["status"], "verified_applied")
        self.assertTrue(
            (self.state / "launch-contracts" / f"{contract_id}.json").is_file()
        )
        self.assertTrue(
            (self.state / "evidence" / f"{contract_id}-verified.json").is_file()
        )
        self.assertEqual(
            list((self.state / "evidence").glob(f"{contract_id}-schema.json")), []
        )
        self.assertEqual(
            list((self.state / "evidence").glob(f"{contract_id}-output.json")), []
        )
        self.assertEqual(
            list((self.state / "evidence").glob(f"{contract_id}-events.jsonl")), []
        )
        self.assertEqual(
            list((self.state / "process-markers").glob(f"{contract_id}-process.json")), []
        )

    def test_preflight_rejections_show_one_error_and_never_launch(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "codex"
        )
        base = {
            "platform": "windows",
            "project": self.project,
            "pack_id": leaf.pack_id,
            "skill_id": leaf.skill_id,
            "runtime": leaf.runtime,
            "menu_commit": self.commit,
            "menu_skill_digest": leaf.skill_ids_digest,
            "menu_instruction_digest": leaf.instruction_digest,
            "menu_acceptance_digest": leaf.acceptance_digest,
        }
        menu_cases = (
            ("fixed_sha", {"menu_commit": "0" * 40}),
            ("selected_skill", {"skill_id": "unknown-skill"}),
            ("instruction_digest", {"menu_instruction_digest": "0" * 64}),
            ("acceptance_digest", {"menu_acceptance_digest": "0" * 64}),
        )
        injected_plan_cases = (
            "owner",
            "origin",
            "approval",
            "secret",
            "symlink",
            "junction",
        )
        cases = tuple((name, updates, False) for name, updates in menu_cases) + tuple(
            (name, {}, True) for name in injected_plan_cases
        )
        for name, updates, inject_plan_failure in cases:
            with self.subTest(case=name):
                case_state = self.root / f"preflight-{name}"
                engine = ActivationEngine(self.config, case_state)
                shown: list[str] = []
                guard = (
                    mock.patch.object(
                        engine,
                        "plan",
                        side_effect=SafetyError(f"injected {name} validation failure"),
                    )
                    if inject_plan_failure
                    else mock.patch.object(engine, "execute", wraps=engine.execute)
                )
                with guard as guarded:
                    with self.assertRaises(Exception):
                        launch_context_leaf(
                            engine,
                            **(base | updates),
                            codex_executable=self.fake_codex,
                            error_ui=shown.append,
                        )
                    if inject_plan_failure:
                        guarded.assert_called_once()
                    else:
                        guarded.assert_not_called()
                self.assertEqual(len(shown), 1)
                rejected = list((case_state / "events").glob("*-rejected.json"))
                self.assertEqual(len(rejected), 1)
                self.assertEqual(
                    json.loads(rejected[0].read_text(encoding="utf-8"))["status"],
                    "rejected",
                )
                self.assertFalse((case_state / "launch-contracts").exists())
                self.assertFalse((case_state / "evidence").exists())
                self.assertFalse((case_state / "process-markers").exists())

    def test_source_head_drift_error_includes_safe_update_gate(self) -> None:
        message = context_error_message(
            "Pack HEAD is not the pinned expected_commit: expected old, got new"
        )
        for required in (
            "review and approve",
            "expected commit and skill digests",
            "reinstall the Explorer menu",
            "selected leaf matches",
            "clean source HEAD",
        ):
            self.assertIn(required, message)

    def test_runtime_failures_show_one_error_and_never_verify(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "codex"
        )
        schema_script = self.root / "schema_failure.py"
        schema_script.write_text(
            "import json,pathlib,sys\n"
            "a=sys.argv[1:];sys.stdin.read();"
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text(json.dumps({}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        output_script = self.root / "output_failure.py"
        output_script.write_text("import sys\nsys.stdin.read();sys.exit(2)\n", encoding="utf-8")
        acceptance_script = self.root / "acceptance_failure.py"
        acceptance_script.write_text(
            "import json,pathlib,sys\n"
            "a=sys.argv[1:];p=sys.stdin.read();"
            "e=json.loads(next(x for x in p.splitlines() if x.startswith('PROVENANCE=')).split('=',1)[1]);"
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text("
            "json.dumps({'evidence':{**e,'applied_rules':['bounded-answer:wrong']},'result':{'decision':'wrong'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        cases = (
            ("launch", str(self.root / "missing-codex"), "launch_failed"),
            ("schema", (sys.executable, str(schema_script)), "output_failed"),
            ("output", (sys.executable, str(output_script)), "output_failed"),
            ("acceptance", (sys.executable, str(acceptance_script)), "acceptance_failed"),
            ("cleanup", self.fake_codex, "cleanup_failed"),
        )
        for name, executable, expected_status in cases:
            with self.subTest(case=name):
                case_state = self.root / f"runtime-{name}"
                engine = ActivationEngine(self.config, case_state)
                if name == "cleanup":
                    original_cleanup = engine._cleanup_temporary_artifacts

                    def fail_cleanup(paths: tuple[Path, ...]) -> None:
                        original_cleanup(paths)
                        paths[-1].mkdir()
                        original_cleanup(paths)

                    engine._cleanup_temporary_artifacts = fail_cleanup  # type: ignore[method-assign]
                shown: list[str] = []
                with self.assertRaises(Exception):
                    launch_context_leaf(
                        engine,
                        platform="windows",
                        project=self.project,
                        pack_id=leaf.pack_id,
                        skill_id=leaf.skill_id,
                        runtime=leaf.runtime,
                        menu_commit=self.commit,
                        menu_skill_digest=leaf.skill_ids_digest,
                        menu_instruction_digest=leaf.instruction_digest,
                        menu_acceptance_digest=leaf.acceptance_digest,
                        codex_executable=executable,
                        error_ui=shown.append,
                    )
                self.assertEqual(len(shown), 1)
                self.assertEqual(
                    list((case_state / "evidence").glob("*-verified.json")), []
                )
                failures = list(
                    (case_state / "evidence").glob("*-not-guaranteed.json")
                )
                self.assertEqual(len(failures), 1)
                failure = json.loads(failures[0].read_text(encoding="utf-8"))
                self.assertEqual(failure["status"], expected_status)
                self.assertEqual(failure["terminal_event"]["status"], expected_status)
                self.assertEqual(
                    len(list((case_state / "launch-contracts").glob("*.json"))), 1
                )
                if name == "cleanup":
                    self.assertTrue(failure["unresolved_artifacts"])
                else:
                    temporary = [
                        *list((case_state / "evidence").glob("*-schema.json")),
                        *list((case_state / "evidence").glob("*-output.json")),
                        *list((case_state / "evidence").glob("*-events.jsonl")),
                        *list((case_state / "process-markers").glob("*-process.json")),
                    ]
                    self.assertEqual(temporary, [])

    def test_individual_claude_leaf_routes_without_project_side_effects(self) -> None:
        (self.project / ".agents").mkdir()
        (self.project / ".agents" / "existing.txt").write_text("agents-before", encoding="utf-8")
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "existing.txt").write_text("claude-before", encoding="utf-8")
        (self.project / ".skill-magnet-old-state.json").write_text(
            '{"status":"before"}', encoding="utf-8"
        )
        (self.project / "project.txt").write_text("project-before", encoding="utf-8")

        def snapshot() -> dict[str, bytes]:
            return {
                path.relative_to(self.project).as_posix(): path.read_bytes()
                for path in sorted(self.project.rglob("*"))
                if path.is_file()
            }

        before = snapshot()
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
            and item.runtime == "claude"
        )
        engine = ActivationEngine(self.config, self.state)
        shown: list[str] = []
        expected = {"status": "verified_applied", "interactive_handoff": {"runtime": "claude"}}
        with mock.patch.object(engine, "execute", return_value=expected) as runtime:
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime=leaf.runtime,
                menu_commit=self.commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                codex_executable=self.fake_codex,
                error_ui=shown.append,
            )
        self.assertEqual(result, expected)
        runtime.assert_called_once()
        self.assertEqual(shown, [])
        self.assertEqual(snapshot(), before)
        self.assertEqual(len(list((self.state / "launch-contracts").glob("*.json"))), 1)
        self.assertFalse((self.state / "evidence").exists())
        self.assertFalse((self.state / "process-markers").exists())
        self.assertFalse((self.root / "must-not-install-codex").exists())
        self.assertFalse((self.root / "must-not-install-claude").exists())

    def test_claude_adapter_verifies_structured_output(self) -> None:
        script = self.root / "fake_claude_adapter.py"
        script.write_text(
            "import json,sys\n"
            "p=sys.stdin.read()\n"
            "e=json.loads(next(x for x in p.splitlines() if x.startswith('PROVENANCE=')).split('=',1)[1])\n"
            "o={'evidence':{**e,'applied_rules':['bounded-answer:bounded']},'result':{'decision':'bounded'}}\n"
            "print(json.dumps({'session_id':'11111111-1111-4111-8111-111111111111','structured_output':o}))\n",
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="claude",
            purpose="verify Claude adapter",
            skill_id="bounded-answer",
        )
        contract = engine.confirm(plan, confirmed=True)
        result = engine.execute(
            contract.contract_id,
            runtime_executable=(sys.executable, str(script)),
        )
        self.assertEqual(result["status"], "verified_applied")
        self.assertEqual(result["interactive_handoff"]["runtime"], "claude")
        self.assertEqual(result["interactive_handoff"]["state"], "test_suppressed")

    def test_visible_handoff_routes_both_runtime_commands_through_terminal(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="verify visible handoff",
            skill_id="bounded-answer",
        )
        contract = engine.confirm(plan, confirmed=True)
        fake_process = mock.Mock(pid=43210)
        fake_process.poll.return_value = 0
        for runtime, executable in (
            ("codex", r"C:\runtime\codex.exe"),
            ("claude", r"C:\runtime\claude.exe"),
        ):
            with self.subTest(runtime=runtime):
                response = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ProcessId": 54321,
                            "ExecutablePath": executable,
                            "CommandLine": f"{executable} --resume session-123",
                        }
                    ),
                    stderr="",
                )
                with (
                    mock.patch(
                        "skill_magnet.activation.shutil.which",
                        return_value=r"C:\Windows\wt.exe",
                    ),
                    mock.patch(
                        "skill_magnet.activation.subprocess.Popen",
                        return_value=fake_process,
                    ) as popen,
                    mock.patch(
                        "skill_magnet.activation.subprocess.run",
                        return_value=response,
                    ),
                    mock.patch.object(
                        engine,
                        "_codex_interactive_executable",
                        return_value=executable,
                    ),
                ):
                    record = engine._launch_interactive_session(
                        contract,
                        runtime=runtime,
                        session_id="session-123",
                        resolved_executable=executable,
                    )
                command = popen.call_args.args[0]
                self.assertEqual(command[0], r"C:\Windows\wt.exe")
                self.assertIn(executable, command)
                self.assertIn("session-123", command)
                self.assertEqual(record["pid"], 54321)
                self.assertEqual(record["runtime"], runtime)

    @unittest.skipUnless(sys.platform == "win32", "real Windows runtime processes required")
    def test_failed_handoff_teardown_kills_real_codex_and_claude_process_trees(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        wrapper = shutil.which("codex.cmd") or "codex"
        codex = engine._codex_interactive_executable(wrapper)
        claude = shutil.which("claude.exe") or shutil.which("claude")
        self.assertIsNotNone(codex)
        self.assertIsNotNone(claude)
        commands = (
            ("codex", [str(codex), "app-server"]),
            (
                "claude",
                [
                    str(claude),
                    "--print",
                    "--input-format",
                    "stream-json",
                    "--output-format",
                    "stream-json",
                ],
            ),
        )
        for runtime, command in commands:
            with self.subTest(runtime=runtime):
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    time.sleep(0.25)
                    self.assertIsNone(process.poll(), f"real {runtime} process exited early")
                    launcher = mock.Mock()
                    launcher.poll.return_value = 0
                    engine._terminate_failed_handoff(
                        launcher,
                        f"skill-magnet-negative-{runtime}",
                        owned_runtime_pids=(process.pid,),
                    )
                    process.wait(timeout=5)
                    self.assertIsNotNone(process.returncode)
                    self.assertEqual(
                        engine._windows_handoff_processes(
                            f"skill-magnet-negative-{runtime}"
                        ),
                        [],
                    )
                finally:
                    if process.poll() is None:
                        subprocess.run(
                            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    if process.stdin is not None:
                        process.stdin.close()

    def test_pid_missing_handoff_invokes_identity_limited_teardown_for_both_runtimes(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="negative visible handoff",
            skill_id="bounded-answer",
        )
        contract = engine.confirm(plan, confirmed=True)
        for runtime, executable in (
            ("codex", r"C:\runtime\codex.exe"),
            ("claude", r"C:\runtime\claude.exe"),
        ):
            with self.subTest(runtime=runtime):
                launcher = mock.Mock(pid=43210)
                empty_probe = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
                with (
                    mock.patch(
                        "skill_magnet.activation.shutil.which",
                        return_value=r"C:\Windows\wt.exe",
                    ),
                    mock.patch(
                        "skill_magnet.activation.subprocess.Popen",
                        return_value=launcher,
                    ),
                    mock.patch(
                        "skill_magnet.activation.subprocess.run",
                        return_value=empty_probe,
                    ),
                    mock.patch.object(
                        engine, "_codex_interactive_executable", return_value=executable
                    ),
                    mock.patch.object(engine, "_terminate_failed_handoff") as teardown,
                    mock.patch(
                        "skill_magnet.activation.time.monotonic", side_effect=[0, 11]
                    ),
                ):
                    with self.assertRaisesRegex(
                        SafetyError, "did not report a live PID"
                    ):
                        engine._launch_interactive_session(
                            contract,
                            runtime=runtime,
                            session_id=f"negative-{runtime}-session",
                            resolved_executable=executable,
                        )
                teardown.assert_called_once_with(
                    launcher, f"negative-{runtime}-session"
                )

    def test_explorer_menu_cancel_before_leaf_has_zero_side_effects(self) -> None:
        (self.project / ".agents").mkdir()
        (self.project / ".agents" / "existing.txt").write_text(
            "agents-before", encoding="utf-8"
        )
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "existing.txt").write_text(
            "claude-before", encoding="utf-8"
        )
        (self.project / ".skill-magnet-old-state.json").write_text(
            '{"status":"before"}', encoding="utf-8"
        )
        (self.project / "project.txt").write_text(
            "project-before", encoding="utf-8"
        )

        def snapshot() -> dict[str, bytes]:
            return {
                path.relative_to(self.project).as_posix(): path.read_bytes()
                for path in sorted(self.project.rglob("*"))
                if path.is_file()
            }

        before = snapshot()
        runner = mock.Mock(name="leaf_runner")
        process = mock.Mock(name="codex_or_claude_process")
        error_ui = mock.Mock(name="error_ui")

        for root_name, entries in (
            ("Directory", windows_directory_registry_entries(self.config_path)),
            ("Background", windows_background_registry_entries(self.config_path)),
        ):
            with self.subTest(root=root_name):
                command_keys = [key for key, _, _ in entries if key.endswith(r"\command")]
                self.assertEqual(len(command_keys), 4)
                self.assertTrue(
                    all(
                        r"\shell\skill-" in key
                        and r"\shell\runtime-" in key
                        for key in command_keys
                    )
                )
                non_leaf_keys = {
                    key for key, _, _ in entries if not key.endswith(r"\command")
                }
                self.assertTrue(non_leaf_keys)
                self.assertFalse(any(key.endswith(r"\command") for key in non_leaf_keys))

                # Explorer owns menu opening. Closing it without choosing a runtime
                # leaf emits no command-selection event, so no runner is dispatched.
                selected_leaf_command = None
                if selected_leaf_command is not None:
                    runner(selected_leaf_command)

        runner.assert_not_called()
        process.assert_not_called()
        error_ui.assert_not_called()
        self.assertEqual(snapshot(), before)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "must-not-install-codex").exists())
        self.assertFalse((self.root / "must-not-install-claude").exists())

    def test_windows_leaf_command_builder_preserves_independent_argv(self) -> None:
        project = r"C:\projects\space & 日本語 (demo)\target"
        command = windows_leaf_command_argv(
            self.config_path, project, "bounded-pack", "bounded-answer", "codex"
        )
        expected_executable = Path(sys.executable)
        if sys.platform == "win32" and expected_executable.with_name("pythonw.exe").is_file():
            expected_executable = expected_executable.with_name("pythonw.exe")
        self.assertEqual(command[0], str(expected_executable))
        self.assertEqual(
            command[command.index("--config") + 1], str(self.config_path.resolve())
        )
        self.assertEqual(command[command.index("--project") + 1], project)
        self.assertEqual(command[command.index("--pack") + 1], "bounded-pack")
        self.assertEqual(command[command.index("--skill") + 1], "bounded-answer")
        self.assertEqual(command[command.index("--runtime") + 1], "codex")
        self.assertEqual(command[command.index("--menu-commit") + 1], self.commit)
        self.assertNotIn("cmd.exe", (part.lower() for part in command))

    def test_windows_leaf_command_builder_rejects_unknown_pack_and_runtime(self) -> None:
        with self.assertRaisesRegex(Exception, "Unknown pack"):
            windows_leaf_command_argv(
                self.config_path,
                r"C:\safe",
                "not-configured",
                "bounded-answer",
                "codex",
            )
        with self.assertRaisesRegex(Exception, "Unknown skill"):
            windows_leaf_command_argv(
                self.config_path,
                r"C:\safe",
                "bounded-pack",
                "not-configured",
                "codex",
            )
        with self.assertRaisesRegex(Exception, "Unsupported runtime"):
            windows_leaf_command_argv(
                self.config_path,
                r"C:\safe",
                "bounded-pack",
                "bounded-answer",
                "ambiguous",
            )

    def test_directory_registry_entries_cover_all_leaves_and_only_owned_subtree(self) -> None:
        root = r"HKCU\Software\Classes\Directory\shell\SkillMagnet"
        entries = windows_directory_registry_entries(self.config_path)
        self.assertTrue(entries)
        self.assertTrue(
            all(key == root or key.startswith(root + "\\") for key, _, _ in entries)
        )
        self.assertFalse(any("Directory\\Background" in key for key, _, _ in entries))

        commands = [
            value for key, name, value in entries if key.endswith(r"\command") and not name
        ]
        expected = [
            windows_command(leaf.command)
            for leaf in windows_menu_leaves(self.config_path, "%1")
        ]
        self.assertCountEqual(commands, expected)
        self.assertEqual(len(commands), 4)
        for pack_id, skill_id in (
            ("bounded-pack", "bounded-answer"),
            ("unused-pack", "unused-skill"),
        ):
            for runtime in ("codex", "claude"):
                command = windows_command(
                    windows_leaf_command_argv(
                        self.config_path, "%1", pack_id, skill_id, runtime
                    )
                )
                self.assertIn(command, commands)

    def test_background_registry_entries_cover_all_leaves_and_only_owned_subtree(self) -> None:
        root = r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet"
        entries = windows_background_registry_entries(self.config_path)
        self.assertTrue(entries)
        self.assertTrue(
            all(key == root or key.startswith(root + "\\") for key, _, _ in entries)
        )
        directory_root = r"HKCU\Software\Classes\Directory\shell\SkillMagnet"
        self.assertFalse(
            any(key == directory_root or key.startswith(directory_root + "\\") for key, _, _ in entries)
        )

        commands = [
            value for key, name, value in entries if key.endswith(r"\command") and not name
        ]
        expected = [
            windows_command(leaf.command)
            for leaf in windows_menu_leaves(self.config_path, "%V")
        ]
        self.assertCountEqual(commands, expected)
        self.assertEqual(len(commands), 4)
        for pack_id, skill_id in (
            ("bounded-pack", "bounded-answer"),
            ("unused-pack", "unused-skill"),
        ):
            for runtime in ("codex", "claude"):
                command = windows_command(
                    windows_leaf_command_argv(
                        self.config_path, "%V", pack_id, skill_id, runtime
                    )
                )
                self.assertIn(command, commands)

    def test_both_registry_roots_preserve_special_absolute_paths_as_single_argv(self) -> None:
        config = self.root / "config 空白 日本語 & ( ) ' ! ^ # %.json"
        config.write_bytes(self.config_path.read_bytes())
        project = self.root / "project 空白 日本語 & ( ) ' ! ^ # %"
        project.mkdir()
        project_path = str(project.resolve())

        cases = (
            ("Directory", "%1", windows_directory_registry_entries),
            ("Background", "%V", windows_background_registry_entries),
        )
        for root_name, placeholder, entry_builder in cases:
            with self.subTest(root=root_name):
                entries = entry_builder(config)
                commands = [
                    value
                    for key, name, value in entries
                    if key.endswith(r"\command") and not name
                ]
                leaves = windows_menu_leaves(config, placeholder)
                self.assertEqual(len(commands), len(leaves))
                for registered, leaf in zip(commands, leaves, strict=True):
                    quoted_placeholder = f'"{placeholder}"'
                    self.assertEqual(registered.count(quoted_placeholder), 1)
                    substituted = registered.replace(
                        quoted_placeholder, windows_command((project_path,)), 1
                    )
                    expected_argv = windows_leaf_command_argv(
                        config,
                        project_path,
                        leaf.pack_id,
                        leaf.skill_id,
                        leaf.runtime,
                    )
                    self.assertEqual(substituted, windows_command(expected_argv))
                    self.assertEqual(
                        expected_argv[expected_argv.index("--config") + 1],
                        str(config.resolve()),
                    )
                    self.assertEqual(
                        expected_argv[expected_argv.index("--project") + 1],
                        project_path,
                    )

    def test_context_cancel_and_supported_claude_contract(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        details = context_selection_details(
            engine,
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
        )
        self.assertEqual(details["selection_kind"], "pack")
        self.assertEqual(details["skill_count"], 1)
        self.assertEqual(details["skill_ids"], ("bounded-answer",))
        self.assertIsNone(
            confirm_context_selection(
                engine,
                platform="windows",
                details=details,
                purpose="cancelled",
                confirmed=False,
            )
        )
        self.assertFalse(self.state.exists())
        claude = context_selection_details(
            engine,
            project=self.project,
            pack_id="bounded-pack",
            runtime="claude",
        )
        contract = confirm_context_selection(
            engine,
            platform="windows",
            details=claude,
            purpose="verified Claude handoff",
            confirmed=True,
        )
        self.assertEqual(contract.runtime, "claude")
        self.assertEqual(len(list((self.state / "launch-contracts").glob("*.json"))), 1)
        self.assertFalse((self.state / "evidence").exists())

    def test_context_contract_preserves_pack_all_skills_and_codex_runtime(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack" and item.runtime == "codex"
        )
        details = context_selection_details(
            engine,
            project=self.project,
            pack_id=leaf.pack_id,
            runtime=leaf.runtime,
            menu_commit=self.commit,
            menu_skill_digest=leaf.skill_ids_digest,
        )
        contract = confirm_context_selection(
            engine,
            platform="windows",
            details=details,
            purpose="verify immutable selection",
            confirmed=True,
        )
        self.assertIsNotNone(contract)
        self.assertEqual(contract.pack_id, "bounded-pack")
        self.assertEqual(contract.runtime, "codex")
        self.assertEqual(contract.skill_ids, ("bounded-answer",))

    def test_context_rejects_stale_installed_menu_without_state(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        with self.assertRaises(Exception):
            context_selection_details(
                engine,
                project=self.project,
                pack_id="bounded-pack",
                runtime="codex",
                menu_commit="0" * 40,
                menu_skill_digest="0" * 64,
            )
        rejected = list((self.state / "events").glob("*-rejected.json"))
        self.assertEqual(len(rejected), 1)
        event = json.loads(rejected[0].read_text(encoding="utf-8"))
        self.assertEqual(event["status"], "rejected")
        self.assertEqual(event["reason"], "stale_menu_commit")
        self.assertFalse((self.state / "launch-contracts").exists())
        self.assertFalse((self.state / "evidence").exists())

    def test_windows_installer_registers_both_folder_contexts_without_activation(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            return SimpleNamespace(returncode=0, stderr="")

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            result = install_context_menu(
                "windows", self.config_path, run=fake_run
            )
        self.assertTrue(result["installed"])
        roots = {
            r"HKCU\Software\Classes\Directory\shell\SkillMagnet",
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet",
        }
        stale_deletes = [call for call in calls if call[:2] == ["reg", "delete"]]
        self.assertEqual({call[2] for call in stale_deletes}, roots)
        adds = [call for call in calls if call[:2] == ["reg", "add"]]
        self.assertTrue(adds)
        self.assertTrue(all(any(call[2].startswith(root) for root in roots) for call in adds))
        commands = [call[call.index("/d") + 1] for call in adds if "\\command" in call[2]]
        self.assertEqual(len(commands), 8)
        self.assertTrue(all("--pack" in command for command in commands))
        self.assertTrue(all("--runtime" in command for command in commands))
        self.assertTrue(result["reinstall_required_after_pack_change"])
        self.assertFalse(self.state.exists())

    def test_windows_public_cli_installs_and_rolls_back_modern_menu_by_default(self) -> None:
        install_result = {"installed": True, "modern": {"installed": True}}
        rollback_result = {"rolled_back": True, "rollback_point_removed": True}
        stdout = io.StringIO()
        with (
            mock.patch(
                "skill_magnet.cli.install_windows_context_menus",
                return_value=install_result,
            ) as install,
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "install-context-menu",
                    "--platform",
                    "windows",
                    "--confirm",
                ]
            )
        self.assertEqual(exit_code, 0)
        install.assert_called_once_with(self.config_path)
        self.assertEqual(json.loads(stdout.getvalue()), install_result)

        stdout = io.StringIO()
        with (
            mock.patch(
                "skill_magnet.cli.rollback_windows_context_menus",
                return_value=rollback_result,
            ) as rollback,
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "uninstall-context-menu",
                    "--platform",
                    "windows",
                    "--confirm",
                ]
            )
        self.assertEqual(exit_code, 0)
        rollback.assert_called_once_with()
        self.assertEqual(json.loads(stdout.getvalue()), rollback_result)

    def test_windows_commands_quote_special_config_path_and_placeholders(self) -> None:
        special = self.root / "config & (日本語) ' quoted.json"
        special.write_bytes(self.config_path.read_bytes())
        leaves = windows_menu_leaves(special, "%1")
        for leaf in leaves:
            command = __import__("subprocess").list2cmdline(list(leaf.command))
            self.assertIn(f'"{special}"', command)
            self.assertIn("%1", command)
            self.assertIn("--menu-skill-digest", command)

    def test_windows_modern_manifest_has_individual_immutable_runtime_leaves(self) -> None:
        rendered = render_windows_modern_menu_manifest(self.config_path)
        lines = rendered.splitlines()
        self.assertEqual(lines[0], "skill-magnet-menu-v2")
        self.assertEqual(len(lines), 5)
        records = [line.split("\t") for line in lines[1:]]
        self.assertTrue(all(len(record) == 6 for record in records))
        self.assertCountEqual(
            [(record[0], record[3], record[4]) for record in records],
            [
                ("bounded-pack", "bounded-answer", "Codex"),
                ("bounded-pack", "bounded-answer", "Claude"),
                ("unused-pack", "unused-skill", "Codex"),
                ("unused-pack", "unused-skill", "Claude"),
            ],
        )
        for _, menu_label, selection_kind, _, _, command in records:
            self.assertTrue(menu_label)
            self.assertIn(selection_kind, {"package", "skill"})
            self.assertEqual(command.count("__SKILL_MAGNET_PROJECT__"), 1)
            self.assertIn("--skill", command)
            self.assertIn("--menu-commit", command)
            self.assertIn("--menu-instruction-digest", command)
            self.assertIn("--menu-acceptance-digest", command)

    def test_windows_modern_appx_registers_both_explorer_contexts(self) -> None:
        manifest = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "AppxManifest.xml"
        )
        root = ET.parse(manifest).getroot()
        namespace = {
            "desktop5": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/5"
        }
        item_types = root.findall(".//desktop5:ItemType", namespace)
        self.assertEqual(
            [(item.get("Type"), item.find("desktop5:Verb", namespace).get("Clsid")) for item in item_types],
            [
                ("Directory", "13E2A9DD-4378-4F9D-A385-973C61B19E63"),
                (r"Directory\Background", "13E2A9DD-4378-4F9D-A385-973C61B19E63"),
            ],
        )

    def test_windows_modern_cli_files_and_package_status_are_reproducible(self) -> None:
        root = self.root / "modern install 空白 & 日本語"
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            action = args[args.index("-Action") + 1]
            installed = action != "uninstall"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": installed,
                        "name": "SkillMagnet.ContextMenu",
                        "package_full_name": "SkillMagnet.ContextMenu_1.0.0.0_x64_test",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            installed = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=False
            )
            status = windows_modern_context_menu_status(install_root=root, run=fake_run)
            removed = uninstall_windows_modern_context_menu(install_root=root, run=fake_run)
        self.assertEqual(installed["contexts"], ["Directory", r"Directory\Background"])
        self.assertTrue(status["dll_exists"])
        self.assertTrue(status["menu_manifest_exists"])
        self.assertTrue(removed["removed"])
        self.assertFalse(root.exists())
        self.assertEqual(
            [call[call.index("-Action") + 1] for call in calls],
            ["install", "status", "uninstall", "cleanup-certificate"],
        )
        install_call = calls[0]
        self.assertEqual(install_call[install_call.index("-ExternalLocation") + 1], str(root.resolve()))

    def test_windows_product_install_skips_unsigned_development_contract_executable(self) -> None:
        root = self.root / "policy-safe-modern-install"
        output = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "out"
        )
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            if any(str(item).endswith("build.ps1") for item in args):
                output.mkdir(exist_ok=True)
                for name in ("SkillMagnetCommand.dll", "SkillMagnetLauncher.exe"):
                    (output / name).touch()
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if any(str(item).endswith("build-package.ps1") for item in args):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"installed": True, "name": "SkillMagnet.ContextMenu"}
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            result = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=True
            )
        self.assertTrue(result["installed"])
        build_call = next(
            call for call in calls if any(str(item).endswith("build.ps1") for item in call)
        )
        self.assertIn("-SkipContractTest", build_call)

    @unittest.skipUnless(sys.platform == "win32", "Windows certificate provider required")
    def test_windows_certificate_cleanup_resume_skips_missing_machine_certificate(self) -> None:
        external = self.root / "certificate-cleanup-resume"
        external.mkdir()
        (external / "certificate-state.json").write_text(
            json.dumps(
                {
                    "thumbprint": "0000000000000000000000000000000000000000",
                    "created_my": False,
                    "created_trusted_people": False,
                    "created_machine_trusted_people": True,
                }
            ),
            encoding="utf-8",
        )
        package_script = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "package.ps1"
        )
        result = subprocess.run(
            [
                "pwsh.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(package_script),
                "-Action",
                "cleanup-certificate",
                "-ExternalLocation",
                str(external),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"name":"SkillMagnet.ContextMenu"', result.stdout)

    def test_windows_combined_install_and_rollback_restore_classic_and_package_state(self) -> None:
        root = self.root / "combined-modern"
        registry = {
            r"HKCU\Software\Classes\Directory\shell\SkillMagnet": True,
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet": True,
        }
        package_installed = False

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal package_installed
            if args[0] == "reg":
                action, target = args[1], args[2]
                if action == "query":
                    return SimpleNamespace(returncode=0 if registry.get(target) else 1, stdout="", stderr="")
                if action == "export":
                    Path(args[3]).write_text(target, encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "delete":
                    registry[target] = False
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "add":
                    registry[target.split(r"\shell", 1)[0] + r"\shell\SkillMagnet"] = True
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "import":
                    registry[Path(target).read_text(encoding="utf-8")] = True
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
            action = args[args.index("-Action") + 1]
            if action == "install":
                package_installed = True
            elif action == "uninstall":
                package_installed = False
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"installed": package_installed, "name": "SkillMagnet.ContextMenu"}),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            installed = install_windows_context_menus(
                self.config_path, install_root=root, run=fake_run, build=False
            )
            self.assertTrue(installed["installed"])
            self.assertTrue(package_installed)
            rolled_back = rollback_windows_context_menus(install_root=root, run=fake_run)
        self.assertTrue(rolled_back["rolled_back"])
        self.assertFalse(package_installed)
        self.assertTrue(all(registry.values()))
        self.assertFalse(root.exists())
        self.assertFalse(root.with_name(root.name + ".rollback").exists())

    def test_windows_menu_command_bootstraps_outside_project_directory(self) -> None:
        leaf = windows_menu_leaves(self.config_path, "%V")[0]
        context_index = leaf.command.index("context")
        with tempfile.TemporaryDirectory() as unrelated:
            result = subprocess.run(
                (*leaf.command[:context_index], "--help"),
                cwd=unrelated,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skill Magnet", result.stdout)

    def test_windows_installer_failure_rolls_back_context_entries(self) -> None:
        calls: list[list[str]] = []
        add_count = 0

        def failing_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal add_count
            calls.append(args)
            if args[:2] == ["reg", "add"]:
                add_count += 1
                if add_count == 2:
                    return SimpleNamespace(returncode=5, stderr="injected failure")
            return SimpleNamespace(returncode=0, stderr="")

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            with self.assertRaises(Exception):
                install_context_menu(
                    "windows", self.config_path, run=failing_run
                )
        deleted = [call for call in calls if call[:2] == ["reg", "delete"]]
        self.assertGreaterEqual(len(deleted), 2)
        self.assertEqual(
            {call[2] for call in deleted[-2:]},
            {
                r"HKCU\Software\Classes\Directory\shell\SkillMagnet",
                r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet",
            },
        )
        self.assertFalse(self.state.exists())

    def test_windows_uninstall_removes_only_owned_subtrees(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            return SimpleNamespace(returncode=0, stderr="")

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            result = uninstall_context_menu("windows", run=fake_run)
        self.assertTrue(result["removed"])
        self.assertEqual(
            calls,
            [
                [
                    "reg",
                    "delete",
                    r"HKCU\Software\Classes\Directory\shell\SkillMagnet",
                    "/f",
                ],
                [
                    "reg",
                    "delete",
                    r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet",
                    "/f",
                ],
            ],
        )
        self.assertFalse(self.state.exists())

    def test_windows_reinstall_and_uninstall_preserve_registry_neighbors_and_config(self) -> None:
        special_config = self.root / "config 空白 日本語 & ( ) ' ! ^ # %.json"
        special_config.write_bytes(self.config_path.read_bytes())
        original_config = special_config.read_bytes()
        directory = r"HKCU\Software\Classes\Directory\shell\SkillMagnet"
        background = (
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet"
        )
        protected = {
            r"HKCU\Software\Classes\Directory\shell",
            r"HKCU\Software\Classes\Directory\shell\OtherProduct",
            r"HKCU\Software\Classes\Directory\Background\shell",
            r"HKCU\Software\Classes\Directory\Background\shell\OtherProduct",
        }
        registry = protected | {
            directory,
            directory + r"\shell\stale-pack",
            background,
            background + r"\shell\stale-pack",
        }

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            target = args[2]
            if args[:2] == ["reg", "delete"]:
                removed = {
                    key
                    for key in registry
                    if key == target or key.startswith(target + "\\")
                }
                registry.difference_update(removed)
                return SimpleNamespace(returncode=0 if removed else 1, stderr="")
            if args[:2] == ["reg", "add"]:
                registry.add(target)
                return SimpleNamespace(returncode=0, stderr="")
            raise AssertionError(args)

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            install_context_menu("windows", special_config, run=fake_run)
        self.assertFalse(any(key.endswith("stale-pack") for key in registry))
        self.assertTrue(protected.issubset(registry))
        self.assertTrue(any(key.startswith(directory + "\\") for key in registry))
        self.assertTrue(any(key.startswith(background + "\\") for key in registry))
        self.assertEqual(special_config.read_bytes(), original_config)

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            uninstall_context_menu("windows", run=fake_run)
        self.assertEqual(registry, protected)
        self.assertEqual(special_config.read_bytes(), original_config)

    def test_macos_installer_creates_and_removes_finder_quick_action(self) -> None:
        services = self.root / "Library" / "Services"
        result = install_context_menu(
            "macos", self.config_path, services_dir=services
        )
        workflow = services / "Skill Magnet.workflow" / "Contents" / "document.wflow"
        self.assertTrue(result["installed"])
        self.assertTrue(workflow.is_file())
        self.assertIn(b"com.apple.RunShellScript", workflow.read_bytes())
        removed = uninstall_context_menu("macos", services_dir=services)
        self.assertTrue(removed["removed"])
        self.assertFalse(workflow.parent.parent.exists())
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
