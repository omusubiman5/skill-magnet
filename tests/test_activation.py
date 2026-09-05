from __future__ import annotations

import ast
import hashlib
import json
import io
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.activation import (
    CODEX_PROCESS_CONFIG_OVERRIDES,
    ActivationEngine,
    _AcceptanceFailed,
    _LaunchFailed,
    _OutputFailed,
    _RuntimeFailed,
    _runtime_failure_diagnostic,
    codex_process_config_args,
    validate_task_workspace,
)
from skill_magnet.cli import exit_process, main as cli_main
from skill_magnet.core import Config, Pack, SafetyError, SkillMagnetError
from skill_magnet.platforms import (
    _capture_windows_context_backup,
    _recover_windows_rollback_rotation,
    _restore_windows_context_backup,
    _rotate_windows_context_backup,
    _windows_registry_entries,
    _windows_owned_menu_roots,
    _windows_native_source_manifest,
    context_menu_spec,
    finder_context_menu_status,
    install_context_menu,
    install_windows_modern_context_menu,
    install_windows_context_menus,
    render_registration,
    uninstall_context_menu,
    uninstall_windows_modern_context_menu,
    uninstall_windows_context_menus,
    windows_modern_context_menu_status,
    windows_background_registry_entries,
    windows_command,
    windows_directory_registry_entries,
    windows_leaf_command_argv,
    windows_library_manager_command_argv,
    windows_menu_leaves,
    windows_root_launcher_command_argv,
    render_windows_modern_menu_manifest,
    rollback_windows_context_menus,
    validate_isolated_menu_runtime,
)
from skill_magnet.ui import (
    ContextUiAction,
    UiWidgetSpec,
    _initial_context_selection,
    _atomic_write_ui_owner_record,
    _owner_json_loads,
    _read_ui_owner_record,
    acquire_context_ui_lease,
    build_tk_ui_surface,
    codex_desktop_deep_link,
    confirm_context_selection,
    context_error_message,
    context_failure_message,
    context_failure_surface,
    context_result_surface,
    context_selection_details,
    context_selection_choice_map,
    context_ui_confirmation,
    context_ui_details,
    context_ui_request_error,
    context_ui_text,
    launch_context_leaf,
    publish_tk_ui_surface,
    start_context_background_operation,
    deliver_codex_desktop_prompt,
    deliver_prepared_codex_handoff,
    claude_desktop_deep_link,
    deliver_claude_desktop_prompt,
    ui_surface_owner_identity,
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


class ContextBackgroundOperationTest(unittest.TestCase):
    def test_operation_is_non_blocking_and_close_signal_is_observed(self) -> None:
        entered = threading.Event()

        def operation(cancel_event: threading.Event) -> str:
            entered.set()
            cancel_event.wait(2)
            return "late result"

        started = time.monotonic()
        cancel_event, worker, outcome = start_context_background_operation(
            operation,
            name="context-background-cancel-test",
        )
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        self.assertTrue(worker.daemon)
        cancel_event.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("value", outcome)
        self.assertIsInstance(outcome.get("error"), SkillMagnetError)

    def test_success_is_returned_without_touching_tk(self) -> None:
        cancel_event, worker, outcome = start_context_background_operation(
            lambda event: "ok" if not event.is_set() else "cancelled",
            name="context-background-success-test",
        )
        worker.join(2)
        self.assertFalse(cancel_event.is_set())
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, {"value": "ok"})


class ActivationEndToEndTest(unittest.TestCase):
    def test_windowless_python_entrypoint_exits_after_failure_ui_returns(self) -> None:
        if sys.platform != "win32":
            self.skipTest("real CREATE_NO_WINDOW child regression requires Windows")
        import ctypes
        import time
        from ctypes import wintypes

        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        command = list(leaf.command)
        self.assertEqual(
            Path(command[0]).name.casefold(),
            "python.exe",
            "the native leaf must use the installed console interpreter",
        )
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
            creationflags=subprocess.CREATE_NO_WINDOW,
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
                if title.value == context_ui_text("ja", "error_title"):
                    dialog_seen = True
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                return True

            callback = callback_type(close_owned_dialog)
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                user32.EnumWindows(callback, 0)
                time.sleep(0.05)
            self.assertTrue(dialog_seen, "windowless Python failure dialog did not appear")
            self.assertEqual(
                process.wait(timeout=5),
                2,
                "windowless Python did not exit after the failure UI returned",
            )
        finally:
            if process.poll() is None:
                manually_terminated = True
                process.terminate()
                process.wait(timeout=5)
        self.assertFalse(manually_terminated, "test had to terminate Python manually")

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
                    "claude",
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
        failure_message = error_ui.call_args.args[0]
        self.assertIn("原因", failure_message)
        self.assertIn("未実行・未確認の範囲", failure_message)
        self.assertIn("次の操作", failure_message)
        self.assertNotIn("stale_menu_commit", failure_message)
        self.assertIn("Pack version changed", failure_message)
        self.assertIn("close and reopen Skill Magnet", failure_message)
        self.assertNotIn("install-context-menu", failure_message)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_common_teardown_covers_real_terminal_outcome_paths(self) -> None:
        target_root = self.root / ".e2e-target"
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack" and item.skill_id == "bounded-answer"
        )

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
                selected_runtime = "claude" if outcome == "rejected" else "codex"
                arguments = {
                    "platform": "windows",
                    "project": project,
                    "pack_id": leaf.pack_id,
                    "skill_id": leaf.skill_id,
                    "runtime": selected_runtime,
                    "menu_commit": self.commit,
                    "menu_skill_digest": leaf.skill_ids_digest,
                    "menu_instruction_digest": leaf.instruction_digest,
                    "menu_acceptance_digest": leaf.acceptance_digest,
                }
                if outcome == "rejected":
                    arguments["menu_commit"] = "0" * 40
                if outcome == "success":
                    contract = engine.confirm(
                        engine.plan(
                            platform="windows",
                            project=project,
                            pack_id=leaf.pack_id,
                            runtime="codex",
                            purpose="common teardown success",
                            skill_id=leaf.skill_id,
                        ),
                        confirmed=True,
                    )
                    result = engine.execute(
                        contract.contract_id, codex_executable=self.fake_codex
                    )
                    self.assertEqual(result["status"], "verified_completed")
                elif outcome == "rejected":
                    with self.assertRaises(Exception):
                        launch_context_leaf(
                            engine,
                            **arguments,
                            codex_executable=self.fake_codex,
                            error_ui=lambda _message: None,
                        )
                elif outcome == "failure":
                    contract = engine.confirm(
                        engine.plan(
                            platform="windows",
                            project=project,
                            pack_id=leaf.pack_id,
                            runtime="codex",
                            purpose="common teardown failure",
                            skill_id=leaf.skill_id,
                        ),
                        confirmed=True,
                    )
                    with self.assertRaises(Exception):
                        engine.execute(
                            contract.contract_id,
                            codex_executable=str(self.root / "missing-codex.exe"),
                        )
                else:
                    contract = engine.confirm(
                        engine.plan(
                            platform="windows",
                            project=project,
                            pack_id=leaf.pack_id,
                            runtime="codex",
                            purpose="common teardown interruption",
                            skill_id=leaf.skill_id,
                        ),
                        confirmed=True,
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
                            engine.execute(
                                contract.contract_id,
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
        self.previous_local_app_data = os.environ.get("LOCALAPPDATA")
        os.environ.setdefault("LOCALAPPDATA", str(self.root / "local-app-data"))
        native_output = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "out"
        )
        native_output.mkdir(parents=True, exist_ok=True)
        self._native_output = native_output
        native_root = native_output.parent
        source_manifest = _windows_native_source_manifest(native_root)
        source_digest = str(source_manifest["source_tree_sha256"])
        test_artifacts = {
            "SkillMagnetCommand.dll": (
                b"unit-test-placeholder\0"
                + (
                    "skill-magnet-native-source-v1:" + source_digest
                ).encode("utf-16-le")
            ),
            "SkillMagnetIdentity.exe": b"unit-test-identity-placeholder",
        }
        tracked_names = (*test_artifacts, "SkillMagnetNativeSource.json")
        self._native_output_backup = {
            name: (native_output / name).read_bytes()
            if (native_output / name).is_file()
            else None
            for name in tracked_names
        }
        for name, payload in test_artifacts.items():
            (native_output / name).write_bytes(payload)
        source_manifest["artifacts"] = [
            {
                "path": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in test_artifacts.items()
        ]
        (native_output / "SkillMagnetNativeSource.json").write_text(
            json.dumps(source_manifest, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
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
        (self.repo / "INDEX.md").write_text(
            "# Test pack\n\n"
            "```mermaid\n"
            "graph LR\n"
            "UNUSED[\"unused-skill\"] -->|depends-on| BOUNDED[\"bounded-answer\"]\n"
            "BOUNDED -.->|contrasts-with| UNUSED\n"
            "```\n",
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
                            "selection_kind": "skill",
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
                            "selection_kind": "skill",
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
        for name, payload in self._native_output_backup.items():
            path = self._native_output / name
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)
        if self.previous_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.previous_local_app_data
        self.temporary.cleanup()

    def _fake_codex(self) -> tuple[str, ...]:
        script = self.root / "fake_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "prompt = sys.stdin.read() if args[-1] == '-' else args[-1]\n"
            "assert 'UNUSED_SENTINEL' not in prompt\n"
            "line = next(x for x in prompt.splitlines() if x.startswith('PROVENANCE='))\n"
            "purpose = next(x for x in prompt.splitlines() if x.startswith('PURPOSE=')).split('=', 1)[1]\n"
            "provenance = json.loads(line.split('=', 1)[1])\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output = {'evidence': {**provenance, 'completed_skill_ids': provenance['skill_ids'], 'skill_execution_status': 'completed', 'applied_rules': "
            "['bounded-answer:result.decision=bounded']}, "
            "'result': {'task_output': purpose, 'decision': 'bounded'}}\n"
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

    def test_prepared_confirmation_has_no_state_until_atomic_persist(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.prepare_confirmation(self._plan(engine), confirmed=True)
        contract_path = engine.contract_dir / f"{contract.contract_id}.json"
        self.assertFalse(contract_path.exists())
        self.assertFalse(engine.contract_dir.exists())

        persisted = engine.persist_confirmation(contract)
        self.assertEqual(persisted, contract)
        self.assertTrue(contract_path.is_file())
        with self.assertRaisesRegex(SafetyError, "identity already exists"):
            engine.persist_confirmation(contract)

    def test_cancellable_gui_preflight_does_not_persist_rejection(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        with self.assertRaisesRegex(SkillMagnetError, "version changed"):
            context_selection_details(
                engine,
                project=self.project,
                pack_id="bounded-pack",
                skill_id="bounded-answer",
                runtime="codex",
                menu_commit="0" * 40,
                record_rejections=False,
            )
        self.assertFalse(engine.events_dir.exists())

    def _rewrite_as_legacy_entity_contract(
        self, engine: ActivationEngine, contract_id: str, purpose: str
    ) -> None:
        """Simulate a valid contract signed by a pre-canonicalization release."""
        path = engine.contract_dir / f"{contract_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["purpose"] = purpose
        unsigned = dict(record)
        unsigned.pop("contract_digest")
        record["contract_digest"] = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def test_desktop_handoff_requires_pack_skill_application_without_metered_api(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        prepared = engine.prepare_codex_desktop_handoff(contract.contract_id)
        result = engine.record_desktop_handoff(prepared)

        prompt = prepared["prompt"]
        self.assertIn("最低1つのスキルを必ず", prompt)
        self.assertIn("説明、一覧、準備確認だけで終了", prompt)
        self.assertIn("INDEX.md", prompt)
        self.assertIn("SKILL.md", prompt)
        self.assertIn("OpenAIまたはAnthropicのAPI key", prompt)
        self.assertIn("追加支払い", prompt)
        self.assertNotIn("activation-complete", prompt)
        self.assertNotIn("desktop-output", prompt)
        self.assertNotIn("JSON Schema", prompt)
        self.assertEqual(result["status"], "desktop_handoff_ready")
        self.assertTrue(result["handoff_completed"])
        self.assertFalse(result["answer_completion_claimed"])
        self.assertEqual(
            result["billing_boundary"], "existing_desktop_plan_no_api_key"
        )
        self.assertNotIn("verified_completed", result)
        self.assertFalse((self.state / "desktop-completion-receipts").exists())
        self.assertEqual(list((self.state / "evidence").glob("*-desktop-schema.json")), [])
        self.assertEqual(list((self.state / "evidence").glob("*-desktop-output.json")), [])

    def test_desktop_prompt_uses_pinned_github_urls_without_local_skill_paths(self) -> None:
        hidden_state = self.root / ".skill-magnet"
        engine = ActivationEngine(self.config, hidden_state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        prepared = engine.prepare_codex_desktop_handoff(contract.contract_id)

        prompt = prepared["prompt"]
        raw_root = f"https://raw.githubusercontent.com/my-owner/separate-skill-repo/{self.commit}"
        self.assertIn(f"{raw_root}/INDEX.md", prompt)
        self.assertIn(f"{raw_root}/bounded-answer/SKILL.md", prompt)
        self.assertNotIn("desktop-materializations", prompt)
        self.assertNotIn("SKILL.md`", prompt)
        self.assertIn("作業対象フォルダー:", prompt)
        self.assertNotIn("対象プロジェクト:", prompt)

    def test_runtime_skill_directories_select_projectless_mode(self) -> None:
        home = self.root / "user-home"
        reserved = home / ".codex" / "skills"
        selected = reserved / "cma-004"
        selected.mkdir(parents=True)
        self.assertEqual(
            validate_task_workspace(self.project, home=home),
            self.project.resolve(),
        )
        for candidate in (reserved, selected):
            with self.subTest(candidate=candidate):
                with mock.patch(
                    "skill_magnet.activation.reserved_skill_content_roots",
                    return_value=(reserved.resolve(),),
                ):
                    plan = ActivationEngine(self.config, self.state).plan(
                        platform="windows",
                        project=candidate,
                        pack_id="bounded-pack",
                        runtime="codex",
                        purpose="Do not install or work inside the skill store",
                    )
                self.assertIsNone(plan["project"])
        self.assertFalse((self.state / "launch-contracts").exists())
        self.assertFalse((self.state / "evidence").exists())

    def test_context_selection_converts_runtime_skill_directory_to_projectless(self) -> None:
        reserved = self.root / "runtime-home" / ".codex" / "skills"
        reserved.mkdir(parents=True)
        engine = ActivationEngine(self.config, self.state)
        with mock.patch(
            "skill_magnet.activation.reserved_skill_content_roots",
            return_value=(reserved.resolve(),),
        ):
            details = context_selection_details(
                engine,
                project=reserved,
                pack_id="bounded-pack",
                runtime="codex",
            )
        self.assertIsNone(details["project"])
        contract = confirm_context_selection(
            engine,
            platform="windows",
            details=details,
            purpose="Continue without treating the skill store as the workspace",
            confirmed=True,
        )
        self.assertIsNone(contract.project)

    def test_runtime_skill_directory_handoff_recovers_without_user_action(self) -> None:
        reserved = self.root / "runtime-home" / ".codex" / "skills"
        selected = reserved / "bounded-answer"
        selected.mkdir(parents=True)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%V")
            if item.pack_id == "bounded-pack" and item.skill_id == "bounded-answer"
        )
        delivered: dict[str, object] = {}
        with mock.patch(
            "skill_magnet.activation.reserved_skill_content_roots",
            return_value=(reserved.resolve(),),
        ):
            result = launch_context_leaf(
                ActivationEngine(self.config, self.state),
                platform="windows",
                project=selected,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime="codex",
                menu_commit=self.commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                desktop_delivery=lambda prompt, project, destination: delivered.update(
                    prompt=prompt, project=project, destination=destination
                ),
            )
        self.assertEqual(result["status"], "desktop_handoff_ready")
        self.assertIsNone(delivered["project"])
        self.assertNotIn(str(selected.resolve()), str(delivered["prompt"]))
        self.assertIn("作業対象フォルダー: なし", str(delivered["prompt"]))
        self.assertEqual(list(selected.iterdir()), [])

    def test_desktop_and_claude_prompts_accept_a_pack_without_index(self) -> None:
        (self.repo / "INDEX.md").unlink()
        git(self.repo, "add", "-u")
        git(self.repo, "commit", "-m", "remove optional index")
        commit = git(self.repo, "rev-parse", "HEAD")
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        for configured_pack in config_data["packs"]:
            configured_pack["expected_commit"] = commit
        config_data["packs"][0]["selection_kind"] = "package"
        self.config_path.write_text(
            json.dumps(config_data),
            encoding="utf-8",
        )
        config = Config.load(self.config_path)
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime):
                engine = ActivationEngine(config, self.root / f"no-index-{runtime}")
                plan = engine.plan(
                    platform="windows",
                    project=self.project,
                    pack_id="bounded-pack",
                    runtime=runtime,
                    purpose="Apply the skill to the request",
                )
                contract = engine.confirm(plan, confirmed=True)
                prepared = (
                    engine.prepare_codex_desktop_handoff(contract.contract_id)
                    if runtime == "codex"
                    else engine.prepare_claude_desktop_handoff(contract.contract_id)
                )
                prompt = prepared["prompt"]
                self.assertNotIn("INDEX.md", prompt)
                self.assertIn("読む、要約する、適用候補を挙げるだけでは実行と認めません", prompt)
                self.assertIn("自然文、JSON、コード、ファイル", prompt)

    def test_desktop_delivery_failure_retains_negative_evidence_without_skill_storage(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaisesRegex(SkillMagnetError, "protocol rejected"):
            deliver_prepared_codex_handoff(
                engine,
                contract.contract_id,
                delivery=lambda *_args: (_ for _ in ()).throw(
                    SkillMagnetError("protocol rejected")
                ),
            )
        failure_path = (
            self.state / "evidence" / f"{contract.contract_id}-not-guaranteed.json"
        )
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["status"], "launch_failed")
        self.assertFalse(
            (self.state / "desktop-materializations" / contract.contract_id).exists()
        )

    def test_cross_platform_manual_selection_to_verified_application_e2e(self) -> None:
        self.assertFalse(self.state.exists())

    def test_menu_mismatch_recovery_uses_an_existing_cli_action(self) -> None:
        message = context_failure_message(
            SkillMagnetError("reinstall required after menu installation"),
            config_path=self.config_path,
            state_dir=self.state,
        )
        self.assertIn("install-context-menu --platform windows --confirm", message)
        self.assertNotIn("Skill Magnetへ反映", message)

        mac_message = context_failure_message(
            SkillMagnetError("reinstall required after menu installation"),
            config_path=self.config_path,
            state_dir=self.state,
            platform="macos",
        )
        self.assertIn("install-context-menu --platform macos --confirm", mac_message)
        self.assertNotIn("Windows Terminal", mac_message)

        selection_message = context_failure_message(
            SkillMagnetError(
                "Pack membership changed after the selection screen opened; "
                "close and reopen Skill Magnet"
            ),
            config_path=self.config_path,
            state_dir=self.state,
            platform="windows",
        )
        self.assertIn("close and reopen Skill Magnet", selection_message)
        self.assertNotIn("install-context-menu", selection_message)
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
                self.assertEqual(result["status"], "verified_completed")
                self.assertEqual(result["commit_sha"], self.commit)
                self.assertEqual(
                    result["output"]["result"]["decision"], "bounded"
                )
                self.assertEqual(
                    result["output"]["result"]["task_output"],
                    "Make a bounded decision",
                )
                with self.assertRaises(SafetyError):
                    engine.execute(contract.contract_id, codex_executable=self.fake_codex)
                self.assertEqual(
                    list((self.state / "evidence").glob("*-schema.json")), []
                )
                self.assertEqual(
                    result["terminal_event"],
                    {"status": "verified_completed", "terminal": True},
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

    def test_product_pack_cross_platform_runtime_handoff_e2e(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        product_config = Config.load(project_root / "skill-magnet.json")
        pack = product_config.packs["codex-cli"]
        purpose = (
            "Design a CI delivery workflow that combines execution mode, sandbox, "
            "egress, MCP, bounded subagents, and patch handoff controls."
        )
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform, runtime="codex"):
                state = self.root / f"product-{platform}-codex"
                engine = ActivationEngine(product_config, state)
                plan = engine.plan(
                    platform=platform,
                    project=self.project,
                    pack_id=pack.pack_id,
                    runtime="codex",
                    purpose=purpose,
                )
                contract = engine.confirm(plan, confirmed=True)
                delivered: list[tuple[str, str, str]] = []
                handoff = deliver_prepared_codex_handoff(
                    engine,
                    contract.contract_id,
                    delivery=lambda prompt, project, destination: delivered.append(
                        (prompt, project, destination)
                    ),
                )
                self.assertEqual(handoff["status"], "desktop_handoff_ready")
                self.assertTrue(handoff["handoff_completed"])
                self.assertFalse(handoff["answer_completion_claimed"])
                self.assertNotIn("verified_completed", handoff)
                self.assertEqual(len(delivered), 1)
                prompt, delivered_project, destination = delivered[0]
                self.assertEqual(delivered_project, str(self.project.resolve()))
                self.assertEqual(destination, "codex://threads/new")
                self.assertIn("composes-with", prompt)
                self.assertIn("最低1つのスキルを必ず", prompt)
                self.assertIn("説明、一覧、準備確認だけで終了", prompt)
                self.assertIn("API key", prompt)
                self.assertNotIn("activation-complete", prompt)
                self.assertEqual(handoff["skill_content_storage"], "github_only")
                self.assertIn("raw.githubusercontent.com", prompt)
                self.assertFalse((state / "desktop-materializations").exists())
            with self.subTest(platform=platform, runtime="claude"):
                state = self.root / f"product-{platform}-claude"
                engine = ActivationEngine(product_config, state)
                plan = engine.plan(
                    platform=platform,
                    project=self.project,
                    pack_id=pack.pack_id,
                    runtime="claude",
                    purpose=purpose,
                )
                contract = engine.confirm(plan, confirmed=True)
                handoff = engine.prepare_claude_desktop_handoff(contract.contract_id)
                self.assertEqual(handoff["status"], "desktop_handoff_prepared")
                self.assertEqual(handoff["destination"], "claude://code/new")
                self.assertEqual(handoff["skill_ids"], list(pack.skills))
                self.assertIn("composes-with", handoff["prompt"])

    def test_claude_desktop_leaf_hands_one_prompt_to_delivery_adapter(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        delivered: list[tuple[str, str, str]] = []
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
                runtime="claude",
                menu_commit=pack.expected_commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                destination="desktop",
                claude_desktop_delivery=lambda prompt, project, url: delivered.append(
                    (prompt, project, url)
                ),
            )
        self.assertEqual(result["status"], "desktop_handoff_ready")
        self.assertEqual(result["runtime"], "claude")
        self.assertEqual(len(delivered), 1)
        prompt, project, url = delivered[0]
        self.assertEqual(project, str(self.project.resolve()))
        self.assertEqual(url, "claude://code/new")
        self.assertIn(self.project.resolve().as_posix(), prompt)
        self.assertIn("bounded-answer", prompt)
        self.assertIn("読む、要約する、適用候補を挙げるだけでは実行と認めません", prompt)
        self.assertIn("自然文、JSON、コード、ファイル", prompt)
        self.assertNotIn("Return only the JSON evidence envelope", prompt)
        self.assertNotIn("PROVENANCE=", prompt)
        self.assertNotIn("UNUSED_SENTINEL", prompt)
        self.assertNotIn("prompt", result)
        with self.assertRaisesRegex(SafetyError, "already used"):
            engine.prepare_claude_desktop_handoff(str(result["contract_id"]))

    def test_claude_desktop_deep_link_prefills_code_session_and_folder(self) -> None:
        prompt = "selected-skill\nTARGET_PROJECT=C:\\safe project"
        project = "C:\\safe project"
        url = claude_desktop_deep_link(prompt, project, "claude://code/new")
        self.assertEqual(
            url,
            "claude://code/new?q=selected-skill%0ATARGET_PROJECT%3DC%3A%5Csafe%20project&folder=C%3A%5Csafe%20project",
        )
        with (
            mock.patch("skill_magnet.ui.os.name", "nt"),
            mock.patch("skill_magnet.ui.os.startfile", create=True) as opened,
        ):
            deliver_claude_desktop_prompt(prompt, project, "claude://code/new")
        opened.assert_called_once_with(url)

    def test_claude_desktop_deep_link_fails_closed_on_invalid_input(self) -> None:
        with self.assertRaisesRegex(SkillMagnetError, "Unexpected Claude Desktop destination"):
            claude_desktop_deep_link("prompt", "C:\\repo", "https://example.invalid")
        with self.assertRaisesRegex(SkillMagnetError, "safe handoff limit"):
            claude_desktop_deep_link("x" * 12_001, "C:\\repo", "claude://code/new")
        with self.assertRaisesRegex(SkillMagnetError, "safe URL limit"):
            claude_desktop_deep_link(
                "\U0001f680" * 8_000, "C:\\repo", "claude://code/new"
            )
        with (
            mock.patch("skill_magnet.ui.os.name", "nt"),
            mock.patch(
                "skill_magnet.ui.os.startfile",
                create=True,
                side_effect=OSError("no handler"),
            ),
            self.assertRaisesRegex(SkillMagnetError, "could not be opened"),
        ):
            deliver_claude_desktop_prompt(
                "prompt", "C:\\repo", "claude://code/new"
            )

    def test_codex_leaf_hands_one_human_prompt_to_desktop_and_never_runs_cli(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        pack = self.config.packs[leaf.pack_id]
        delivered: list[tuple[str, str, str]] = []
        with mock.patch.object(
            engine, "execute", side_effect=AssertionError("CLI execution is forbidden")
        ):
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime="codex",
                menu_commit=pack.expected_commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                desktop_delivery=lambda prompt, project, url: delivered.append(
                    (prompt, project, url)
                ),
            )
        self.assertEqual(result["status"], "desktop_handoff_ready")
        self.assertTrue(result["handoff_completed"])
        self.assertFalse(result["answer_completion_claimed"])
        self.assertNotIn("verified_completed", result)
        self.assertEqual(len(delivered), 1)
        prompt, project, destination = delivered[0]
        self.assertEqual(project, str(self.project.resolve()))
        self.assertEqual(destination, "codex://threads/new")
        self.assertIn("選択パックID: bounded-pack", prompt)
        self.assertIn("適用スキルID: bounded-answer", prompt)
        self.assertIn("実際の依頼:", prompt)
        self.assertIn("Produce a machine-verifiable bounded decision.", prompt)
        remote_skill = (
            f"https://raw.githubusercontent.com/my-owner/separate-skill-repo/"
            f"{self.commit}/bounded-answer/SKILL.md"
        )
        self.assertIn(remote_skill, prompt)
        source_skill = self.repo / "bounded-answer" / "SKILL.md"
        source_skill.write_text(
            source_skill.read_text(encoding="utf-8") + "\nSOURCE_MUTATED_AFTER_HANDOFF\n",
            encoding="utf-8",
        )
        self.assertNotIn("SOURCE_MUTATED_AFTER_HANDOFF", prompt)
        self.assertNotIn("Always set result.decision to bounded.", prompt)
        self.assertNotIn("PROVENANCE=", prompt)
        self.assertNotIn("UNUSED_SENTINEL", prompt)
        self.assertNotIn("prompt", result)
        evidence = self.state / "evidence" / f"{result['contract_id']}-desktop-handoff.json"
        self.assertTrue(evidence.is_file())

    def test_legacy_contract_u0020_is_canonicalized_at_desktop_handoff(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        legacy_request = "（私が最も見たくない結論）&#x20;"
        canonical_request = "（私が最も見たくない結論） "
        self._rewrite_as_legacy_entity_contract(
            engine, contract.contract_id, legacy_request
        )

        prepared = engine.prepare_codex_desktop_handoff(contract.contract_id)

        self.assertIn(canonical_request, prepared["prompt"])
        self.assertNotIn("&#x20;", prepared["prompt"])
        self.assertEqual(
            prepared["actual_request_sha256"],
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        )

    def test_legacy_contract_u0020_uses_one_value_through_verified_execution(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        legacy_request = "（私が最も見たくない結論）&#x20;"
        canonical_request = "（私が最も見たくない結論） "
        self._rewrite_as_legacy_entity_contract(
            engine, contract.contract_id, legacy_request
        )

        utf8_test_runtime = (
            self.fake_codex[0],
            "-X",
            "utf8",
            self.fake_codex[1],
        )
        result = engine.execute(
            contract.contract_id, codex_executable=utf8_test_runtime
        )

        user_result = result["user_result"]
        self.assertEqual(user_result["request"], canonical_request)
        self.assertEqual(user_result["result"], canonical_request)
        self.assertEqual(
            user_result["details"]["verification_status"], "verified_completed"
        )

    def test_codex_handoff_rejects_source_change_after_validation(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="Use the pack composition map.",
        )
        contract = engine.confirm(plan, confirmed=True)
        index = self.repo / "INDEX.md"
        original_index = index.read_bytes()
        try:
            index.write_text("# injected after validation\n", encoding="utf-8")
            with self.assertRaisesRegex(
                SafetyError, "content changed|uncommitted changes"
            ):
                engine.prepare_codex_desktop_handoff(contract.contract_id)
        finally:
            index.write_bytes(original_index)
        stored_contract = json.loads(
            (engine.contract_dir / f"{contract.contract_id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored_contract["index_digest"], plan["index_digest"])

    def test_any_cli_entry_removes_all_legacy_desktop_skill_storage(self) -> None:
        expired = self.state / "desktop-materializations" / ("a" * 32)
        expired.mkdir(parents=True)
        (expired / "materialization.json").write_text(
            json.dumps(
                {
                    "expires_at": (
                        datetime.now(timezone.utc) - timedelta(minutes=1)
                    ).isoformat()
                }
            ),
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()):
            result = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "--state-dir",
                    str(self.state),
                    "packs",
                ]
            )
        self.assertEqual(result, 0)
        self.assertFalse(expired.exists())

    def test_codex_desktop_deep_link_encodes_japanese_newlines_reserved_and_long_text(self) -> None:
        prompt = "日本語\n空 白 & # ? = " + ("長文" * 1_000)
        project = r"C:\Projects\日本語 & # folder"
        url = codex_desktop_deep_link(prompt, project, "codex://threads/new")
        self.assertTrue(url.startswith("codex://threads/new?path="))
        self.assertIn("%0A", url)
        self.assertIn("%26", url)
        self.assertIn("%23", url)
        self.assertIn("%20", url)
        from urllib.parse import parse_qs, urlsplit

        decoded = parse_qs(urlsplit(url).query)
        self.assertEqual(decoded["path"], [project])
        self.assertEqual(decoded["prompt"], [prompt])

    def test_codex_desktop_projectless_link_omits_path(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        url = codex_desktop_deep_link(
            "プロジェクトなしで続行", None, "codex://threads/new"
        )
        decoded = parse_qs(urlsplit(url).query)
        self.assertNotIn("path", decoded)
        self.assertEqual(decoded["prompt"], ["プロジェクトなしで続行"])

    def test_claude_desktop_projectless_link_omits_folder(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        url = claude_desktop_deep_link(
            "Continue without a folder", None, "claude://code/new"
        )
        decoded = parse_qs(urlsplit(url).query)
        self.assertNotIn("folder", decoded)
        self.assertEqual(decoded["q"], ["Continue without a folder"])

    def test_codex_desktop_delivery_uses_windows_protocol_handler(self) -> None:
        with (
            mock.patch("skill_magnet.ui.os.name", "nt"),
            mock.patch("skill_magnet.ui.os.startfile", create=True) as opened,
        ):
            deliver_codex_desktop_prompt(
                "依頼 & #", r"C:\Projects\対象 path", "codex://threads/new"
            )
        url = opened.call_args.args[0]
        self.assertIn("path=C%3A%5CProjects%5C", url)
        self.assertIn("prompt=%E4%BE%9D%E9%A0%BC%20%26%20%23", url)

    @unittest.skipUnless(sys.platform == "win32", "Windows cmd wrapper contract")
    def test_windows_cmd_runtime_preserves_special_project_as_one_argv(self) -> None:
        special_project = self.root / "SM INT 002 日本語 & ( ) ' ! ^ # %"
        special_project.mkdir()
        script = self.root / "fake_cmd_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"assert args[args.index('--cd') + 1] == {str(special_project.resolve())!r}\n"
            "prompt = sys.stdin.read() if args[-1] == '-' else args[-1]\n"
            "line = next(x for x in prompt.splitlines() if x.startswith('PROVENANCE='))\n"
            "provenance = json.loads(line.split('=', 1)[1])\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(json.dumps({'evidence': {**provenance, 'completed_skill_ids': provenance['skill_ids'], 'skill_execution_status': 'completed', "
            "'applied_rules': ['bounded-answer:result.decision=bounded']}, "
            "'result': {'task_output': 'deliverable', 'decision': 'bounded'}}), encoding='utf-8')\n",
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

        self.assertEqual(result["status"], "verified_completed")
        self.assertEqual(result["output"]["result"]["decision"], "bounded")

    def test_user_confirmation_is_mandatory(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        with self.assertRaises(SafetyError):
            engine.confirm(self._plan(engine), confirmed=False)
        self.assertFalse(self.state.exists())

    def test_legacy_persistent_sync_is_permanently_unreachable_by_cli(self) -> None:
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
        self.assertIn("permanently disabled", error.getvalue())
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
            "o.write_text(json.dumps({'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['claimed']},"
            "'result':{'task_output':'deliverable','decision':'unbounded'}}),encoding='utf-8')\n",
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
        contract = engine.confirm(
            engine.plan(
                platform="windows",
                project=self.project,
                pack_id="bounded-pack",
                runtime="codex",
                purpose="forced interruption",
                skill_id="bounded-answer",
            ),
            confirmed=True,
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
                engine.execute(
                    contract.contract_id,
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
            "json.dumps({'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['bounded-answer:wrong']},"
            "'result':{'task_output':'deliverable','decision':'wrong'}}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        table = (
            ("success", "verified_completed", True, False, False, True),
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
                if status == "verified_completed":
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
            "o.write_text(json.dumps({'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['generic rule']},"
            "'result':{'task_output':'deliverable','decision':'bounded'}}),encoding='utf-8')\n",
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

    def test_missing_skill_completion_evidence_never_reaches_terminal_success(self) -> None:
        script = self.root / "missing_completion_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read(); e=json.loads(next(x for x in p.splitlines() "
            "if x.startswith('PROVENANCE=')).split('=',1)[1]); "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text(json.dumps({'evidence':{**e,'applied_rules':['bounded-answer:bounded']},"
            "'result':{'task_output':'deliverable','decision':'bounded'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        with self.assertRaisesRegex(SafetyError, "Completed skill IDs"):
            engine.execute(
                contract.contract_id,
                codex_executable=(sys.executable, str(script)),
            )
        lifecycle = self.state / "events" / f"{contract.contract_id}-lifecycle.jsonl"
        self.assertNotIn("verified_completed", lifecycle.read_text(encoding="utf-8"))

    def test_completion_contract_rejects_each_mismatched_claim(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        pack, _, _, _ = engine._validated_pack(contract.pack_id)
        checks = engine._load_acceptance(pack, contract.skill_ids)
        request_sha256 = hashlib.sha256(contract.purpose.encode("utf-8")).hexdigest()
        base = {
            "evidence": {
                "pack_id": contract.pack_id,
                "repository_url": contract.repository_url,
                "commit_sha": contract.commit_sha,
                "approved_by": contract.approved_by,
                "approved_at": contract.approved_at,
                "skill_ids": list(contract.skill_ids),
                "instruction_digest": contract.instruction_digest,
                "challenge_nonce": contract.nonce,
                "actual_request_sha256": request_sha256,
                "completed_skill_ids": list(contract.skill_ids),
                "skill_execution_status": "completed",
                "applied_rules": ["bounded-answer:result.decision=bounded"],
            },
            "result": {"task_output": "deliverable", "decision": "bounded"},
        }

        cases = (
            (
                "selected skill",
                "Completed skill IDs",
                lambda value: value["evidence"].update(completed_skill_ids=[]),
            ),
            (
                "completed status",
                "did not report completion",
                lambda value: value["evidence"].update(
                    skill_execution_status="pending"
                ),
            ),
            (
                "actual request",
                "targets a different request",
                lambda value: value["evidence"].update(
                    actual_request_sha256="0" * 64
                ),
            ),
            (
                "task output",
                "deliverable is empty",
                lambda value: value["result"].update(task_output="   "),
            ),
            (
                "skill acceptance",
                "Request-aware acceptance failed",
                lambda value: value["result"].update(decision=42),
            ),
            (
                "applied rule identity",
                "does not identify selected skill",
                lambda value: value["evidence"].update(
                    applied_rules=["rule mentions bounded-answer"]
                ),
            ),
        )
        for label, message, mutate in cases:
            with self.subTest(label=label):
                output = json.loads(json.dumps(base))
                mutate(output)
                with self.assertRaisesRegex(SafetyError, message):
                    engine._verify(contract, output, checks, "prompt-digest")

    def test_codex_output_schema_requires_every_declared_property(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(self._plan(engine), confirmed=True)
        pack, _, _, _ = engine._validated_pack(contract.pack_id)
        checks = engine._load_acceptance(pack, contract.skill_ids)
        schema = engine._output_schema(contract, checks)

        for section in ("evidence", "result"):
            object_schema = schema["properties"][section]
            self.assertEqual(
                set(object_schema["required"]), set(object_schema["properties"])
            )
        self.assertIn("saved_paths", schema["properties"]["result"]["required"])
        self.assertIn("changes", schema["properties"]["result"]["required"])
        diagnostic = _runtime_failure_diagnostic(
            1,
            "invalid_json_schema token=sk-secret-value Missing 'changes'",
        )
        self.assertEqual(diagnostic["failure_class"], "invalid_output_schema")
        self.assertEqual(
            diagnostic["stderr_summary"],
            "Codex rejected the verification output schema.",
        )
        self.assertNotIn("sk-secret-value", json.dumps(diagnostic))

    def test_package_reads_all_skills_but_verifies_only_applied_subset(self) -> None:
        raw_config = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw_config["packs"].append(
            {
                "id": "composition-pack",
                "selection_kind": "package",
                "repo_url": "https://github.com/my-owner/separate-skill-repo.git",
                "expected_commit": self.commit,
                "source": str(self.repo),
                "skills": ["bounded-answer", "unused-skill"],
                "approved_by": "test-user",
                "approved_at": "2026-08-22T00:00:00+00:00",
                "purpose": "Route the request through the applicable subset.",
            }
        )
        package_config_path = self.root / "package-config.json"
        package_config_path.write_text(json.dumps(raw_config), encoding="utf-8")
        engine = ActivationEngine(Config.load(package_config_path), self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="composition-pack",
            runtime="codex",
            purpose="Make only the bounded decision",
        )
        contract = engine.confirm(plan, confirmed=True)
        pack, _, _, _ = engine._validated_pack(contract.pack_id)
        checks = engine._load_acceptance(pack, contract.skill_ids)
        schema = engine._output_schema(contract, checks)
        self.assertEqual(schema["properties"]["evidence"]["properties"]
                         ["completed_skill_ids"]["minItems"], 1)
        self.assertIn(
            {"type": "null"},
            schema["properties"]["result"]["properties"]["unused"]["anyOf"],
        )
        self.assertNotIn(
            "const",
            schema["properties"]["result"]["properties"]["decision"]["anyOf"][0],
        )
        output = {
            "evidence": {
                "pack_id": contract.pack_id,
                "repository_url": contract.repository_url,
                "commit_sha": contract.commit_sha,
                "approved_by": contract.approved_by,
                "approved_at": contract.approved_at,
                "skill_ids": list(contract.skill_ids),
                "instruction_digest": contract.instruction_digest,
                "challenge_nonce": contract.nonce,
                "actual_request_sha256": hashlib.sha256(
                    contract.purpose.encode("utf-8")
                ).hexdigest(),
                "completed_skill_ids": ["bounded-answer"],
                "skill_execution_status": "completed",
                "applied_rules": ["bounded-answer:result.decision=bounded"],
            },
            "result": {
                "task_output": "bounded",
                "saved_paths": [],
                "changes": [],
                "decision": "bounded",
                "unused": None,
            },
        }
        verified = engine._verify(contract, output, checks, "prompt-digest")
        self.assertEqual(
            verified["skill_execution_completion_evidence"]["completed_skill_ids"],
            ["bounded-answer"],
        )
        self.assertEqual(
            engine._user_result(contract, pack, output)["executed_skill"],
            "bounded-answer",
        )
        output["result"]["decision"] = "request-specific-alternative"
        output["evidence"]["applied_rules"] = [
            "bounded-answer:result.decision=request-specific-alternative"
        ]
        engine._verify(contract, output, checks, "prompt-digest")
        output["result"]["decision"] = "bounded"
        output["evidence"]["applied_rules"] = [
            "bounded-answer:result.decision=bounded"
        ]
        output["result"]["unused"] = True
        with self.assertRaisesRegex(SafetyError, "Unapplied skill claimed"):
            engine._verify(contract, output, checks, "prompt-digest")
        output["evidence"]["completed_skill_ids"] = ["unused-skill"]
        output["evidence"]["applied_rules"] = ["unused-skill:result.unused=true"]
        output["result"]["decision"] = None
        with self.assertRaisesRegex(SafetyError, "dependency is missing"):
            engine._verify(contract, output, checks, "prompt-digest")
        output["evidence"]["completed_skill_ids"] = [
            "bounded-answer",
            "unused-skill",
        ]
        output["evidence"]["applied_rules"] = [
            "bounded-answer:result.decision=bounded",
            "unused-skill:result.unused=true",
        ]
        output["result"]["decision"] = "bounded"
        with self.assertRaisesRegex(SafetyError, "Contrasting skills"):
            engine._verify(contract, output, checks, "prompt-digest")

    def test_package_composition_requires_request_specific_relationship_evidence(self) -> None:
        source = self.root / "composition-relations"
        source.mkdir()
        (source / "INDEX.md").write_text(
            "```mermaid\n"
            "graph LR\n"
            'LEFT["left-skill"] ===>|composes-with| RIGHT["right-skill"]\n'
            "```\n",
            encoding="utf-8",
        )
        pack = Pack(
            pack_id="relations",
            repo_url="https://github.com/my-owner/relations.git",
            expected_commit="0" * 40,
            source=source,
            skills=("left-skill", "right-skill"),
        )
        relation_engine = ActivationEngine(self.config, self.state)
        relations = relation_engine._pack_relations(pack)
        self.assertEqual(
            relations["composes-with"], {("left-skill", "right-skill")}
        )
        with self.assertRaisesRegex(
            SafetyError, "composition has no relationship evidence"
        ):
            relation_engine._verify_pack_relations(
                pack,
                ["left-skill", "right-skill"],
                ["left-skill: applied", "right-skill: applied"],
            )
        relation_engine._verify_pack_relations(
            pack,
            ["left-skill", "right-skill"],
            [
                "left-skill: applied",
                "right-skill: applied",
                "left-skill composes-with right-skill: needed for this request",
            ],
        )

    def test_wrong_challenge_nonce_is_not_read_evidence(self) -> None:
        script = self.root / "wrong_nonce_codex.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "a=sys.argv[1:]; p=sys.stdin.read(); line=next(x for x in p.splitlines() "
            "if x.startswith('PROVENANCE=')); e=json.loads(line.split('=',1)[1]); "
            "e['challenge_nonce']='stale'; "
            "o=pathlib.Path(a[a.index('--output-last-message')+1]); "
            "o.write_text(json.dumps({'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['claimed']},"
            "'result':{'task_output':'deliverable','decision':'bounded'}}),encoding='utf-8')\n",
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
        with self.assertRaisesRegex(
            SkillMagnetError, "classic context-menu registration is disabled"
        ):
            render_registration("windows", self.config_path)
        self.assertIn("--launcher", windows["command"])
        self.assertIn("Finder Quick Action", render_registration("macos", self.config_path))

    def test_windows_individual_skill_leaves_fix_skill_runtime_and_digests(self) -> None:
        leaves = windows_menu_leaves(self.config_path, "%V")
        self.assertEqual(len(leaves), 2)
        self.assertEqual(
            {(leaf.pack_id, leaf.skill_id) for leaf in leaves},
            {
                ("bounded-pack", "bounded-answer"),
                ("unused-pack", "unused-skill"),
            },
        )
        for leaf in leaves:
            self.assertIn("--pack", leaf.command)
            self.assertIn("--skill", leaf.command)
            self.assertNotIn("--runtime", leaf.command)
            self.assertIn("--menu-commit", leaf.command)
            self.assertIn("--menu-skill-digest", leaf.command)
            self.assertIn("--menu-instruction-digest", leaf.command)
            self.assertIn("--menu-acceptance-digest", leaf.command)
            self.assertEqual(leaf.pack_label, f"Pack: {leaf.pack_id}")
            self.assertEqual(leaf.skill_label, f"Skill: {leaf.skill_id}")

    def test_product_menu_has_one_leaf_per_active_pack(self) -> None:
        product_config = Path(__file__).resolve().parents[1] / "skill-magnet.json"
        leaves = windows_menu_leaves(product_config, "%1")
        self.assertEqual(len(leaves), 3)
        self.assertEqual(
            {leaf.pack_id for leaf in leaves},
            {"codex-cli", "conflict-clarity", "custom-skills"},
        )
        self.assertEqual(sorted(len(leaf.skill_ids) for leaf in leaves), [1, 9, 12])
        for leaf in leaves:
            if leaf.pack_id == "custom-skills":
                self.assertEqual(leaf.skill_id, "cma-004")
                self.assertEqual(
                    leaf.skill_label,
                    "Skill: CMA004 — CMA001を1ニュース1Markdownで出力する",
                )
                self.assertIn("--skill", leaf.command)
            else:
                self.assertIsNone(leaf.skill_id)
                self.assertTrue(leaf.skill_label.startswith("Skill Pack: "))
                self.assertNotIn("--skill", leaf.command)
            self.assertNotIn("--runtime", leaf.command)
            self.assertIn("--menu-instruction-digest", leaf.command)
            self.assertIn("--menu-acceptance-digest", leaf.command)
        for root_name, entry_builder in (
            ("Directory", windows_directory_registry_entries),
            ("Background", windows_background_registry_entries),
        ):
            with self.subTest(root=root_name):
                with self.assertRaisesRegex(
                    SkillMagnetError, "classic context-menu registration is disabled"
                ):
                    entry_builder(product_config)

    def test_both_roots_propagate_complete_pack_contract_and_reject_tampering(self) -> None:
        product_config = Path(__file__).resolve().parents[1] / "skill-magnet.json"
        config = Config.load(product_config)
        purpose = config.packs["codex-cli"].purpose
        cases = (("Directory", "%1"), ("Background", "%V"))
        for root_name, placeholder in cases:
            with self.subTest(root=root_name):
                state = self.root / f"pack-contract-{root_name}"
                engine = ActivationEngine(config, state)
                leaf = windows_menu_leaves(product_config, placeholder)[0]
                details = context_selection_details(
                    engine,
                    project=self.project,
                    pack_id=leaf.pack_id,
                    runtime="codex",
                    menu_commit=config.packs[leaf.pack_id].expected_commit,
                    menu_skill_digest=leaf.skill_ids_digest,
                    menu_instruction_digest=leaf.instruction_digest,
                    menu_acceptance_digest=leaf.acceptance_digest,
                )
                self.assertEqual(details["selection_kind"], "pack")
                self.assertIsNone(details["selected_skill_id"])
                self.assertEqual(details["skill_ids"], config.packs[leaf.pack_id].skills)
                self.assertEqual(details["skill_display_name"], "Codex CLI Official Documentation")
                self.assertEqual(len(details["all_skill_ids"]), 9)
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
                self.assertEqual(contract.selection_kind, "pack")
                self.assertIsNone(contract.selected_skill_id)
                self.assertEqual(contract.skill_ids, config.packs[leaf.pack_id].skills)
                self.assertEqual(len(contract.acceptance_digests), 9)
                self.assertEqual(contract.runtime, "codex")
                self.assertEqual(contract.commit_sha, config.packs[leaf.pack_id].expected_commit)
                self.assertEqual(contract.purpose, purpose)

        selected = windows_menu_leaves(product_config, "%1")[0]
        engine = ActivationEngine(config, self.root / "tampered-pack-contract")
        common = {
            "project": self.project,
            "pack_id": selected.pack_id,
            "runtime": "codex",
            "menu_commit": config.packs[selected.pack_id].expected_commit,
            "menu_skill_digest": selected.skill_ids_digest,
        }
        for field, value, reason in (
            ("menu_instruction_digest", "0" * 64, "instructions"),
            ("menu_acceptance_digest", "0" * 64, "acceptance"),
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
        with self.assertRaisesRegex(Exception, "complete package"):
            context_selection_details(
                engine,
                **{**common, "skill_id": "codex-auth-boundary-selection"},
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

    def test_explicit_individual_leaf_is_silent_desktop_handoff_and_clean(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        engine = ActivationEngine(self.config, self.state)
        stdout = io.StringIO()
        stderr = io.StringIO()
        delivered: list[tuple[str, str, str]] = []
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime="codex",
                menu_commit=self.commit,
                menu_skill_digest=leaf.skill_ids_digest,
                menu_instruction_digest=leaf.instruction_digest,
                menu_acceptance_digest=leaf.acceptance_digest,
                desktop_delivery=lambda prompt, project, destination: delivered.append(
                    (prompt, project, destination)
                ),
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result["status"], "desktop_handoff_ready")
        self.assertTrue(result["handoff_completed"])
        self.assertFalse(result["answer_completion_claimed"])
        self.assertNotIn("verified_completed", result)
        self.assertEqual(result["skill_ids"], ["bounded-answer"])
        self.assertEqual(
            result["terminal_event"],
            {"status": "desktop_handoff_ready", "terminal": False},
        )
        self.assertEqual(len(delivered), 1)
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
        self.assertEqual(lifecycle_events[0]["status"], "desktop_handoff_ready")
        self.assertTrue(
            (self.state / "launch-contracts" / f"{contract_id}.json").is_file()
        )
        self.assertTrue(
            (self.state / "evidence" / f"{contract_id}-desktop-handoff.json").is_file()
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
        )
        base = {
            "platform": "windows",
            "project": self.project,
            "pack_id": leaf.pack_id,
            "skill_id": leaf.skill_id,
            "runtime": "codex",
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
            "Pack HEAD is not the pinned expected_commit: expected old, got new",
            language="en",
        )
        for required in (
            "review and approve",
            "expected commit and skill digests",
            "Library Manager",
            "close and reopen the Skill Magnet selection screen",
            "clean source HEAD",
        ):
            self.assertIn(required, message)
        self.assertNotIn("reinstall the Explorer menu", message)

    def test_context_ui_defaults_to_japanese_and_switches_to_english(self) -> None:
        self.assertEqual(context_ui_text("unknown", "language"), "言語")
        self.assertEqual(context_ui_text("ja", "target_ai"), "実行先AI")
        self.assertEqual(context_ui_text("en", "target_ai"), "Target AI")
        self.assertIn("作業対象フォルダー", context_ui_text("ja", "project", project="X"))
        self.assertIn("Task workspace", context_ui_text("en", "project", project="X"))
        self.assertIn("空欄", context_ui_request_error("ja", "  "))
        self.assertIn("empty", context_ui_request_error("en", "\t"))
        self.assertIsNone(context_ui_request_error("ja", "同じ依頼"))

    def test_context_ui_confirmation_preserves_request_and_internal_values(self) -> None:
        details = {
            "selection_kind": "skill",
            "selected_skill_id": "bounded-answer",
            "pack_id": "bounded-pack",
            "skill_ids": ("bounded-answer",),
            "repository_url": "https://example.test/pack.git",
            "expected_commit": "abc123",
            "runtime": "codex",
            "project": r"C:\Projects\対象",
        }
        purpose = "入力中の actual request / keep exactly"
        japanese = context_ui_confirmation("ja", details, purpose)
        english = context_ui_confirmation("en", details, purpose)
        for rendered in (japanese, english):
            self.assertIn(purpose, rendered)
            self.assertIn("bounded-answer", rendered)
            self.assertIn(r"C:\Projects\対象", rendered)
        self.assertIn("実際の依頼", japanese)
        self.assertIn("Actual request", english)

    def test_dynamic_selector_uses_normalized_unique_labels_without_internal_ids(self) -> None:
        value = json.loads(self.config_path.read_text(encoding="utf-8"))
        for pack in value["packs"]:
            skill_id = pack["skills"][0]
            pack["skill_metadata"] = {
                skill_id: {
                    "display_name": "同名&#x20;スキル",
                    "purpose": "同じ表示名でも内部選択を保持する",
                }
            }
        suffix_pack = dict(value["packs"][0])
        suffix_pack["id"] = "suffix-pack"
        suffix_pack["skills"] = ["suffix-skill"]
        suffix_pack["skill_metadata"] = {
            "suffix-skill": {
                "display_name": "同名 スキル （同名 1）",
                "purpose": "接尾辞風の実表示名も保持する",
            }
        }
        value["packs"].append(suffix_pack)
        duplicate_config = self.root / "duplicate-labels.json"
        duplicate_config.write_text(json.dumps(value), encoding="utf-8")
        engine = ActivationEngine(Config.load(duplicate_config), self.state)
        choices = context_selection_choice_map(engine)
        self.assertEqual(len(choices), 3)
        self.assertEqual(len(set(choices)), 3)
        self.assertTrue(all("&#x20;" not in label for label in choices))
        self.assertTrue(all("bounded-pack" not in label for label in choices))
        self.assertTrue(all("unused-pack" not in label for label in choices))
        self.assertTrue(all("suffix-pack" not in label for label in choices))
        first_label = next(iter(choices))
        selected_pack, selected_skill, selected_label = _initial_context_selection(
            choices, pack_id=None, skill_id=None
        )
        self.assertEqual(selected_label, first_label)
        self.assertEqual((selected_pack, selected_skill or None), choices[first_label])
        self.assertEqual(
            set(choices.values()),
            {
                ("bounded-pack", "bounded-answer"),
                ("unused-pack", "unused-skill"),
                ("suffix-pack", "suffix-skill"),
            },
        )

    def test_verified_details_show_only_selected_skill_and_real_digests(self) -> None:
        value = json.loads(self.config_path.read_text(encoding="utf-8"))
        bounded = value["packs"][0]
        bounded["skills"] = ["bounded-answer", "unused-skill"]
        expanded_config = self.root / "expanded-pack.json"
        expanded_config.write_text(json.dumps(value), encoding="utf-8")
        engine = ActivationEngine(Config.load(expanded_config), self.state)
        details = context_selection_details(
            engine,
            project=self.project,
            pack_id="bounded-pack",
            skill_id="bounded-answer",
            runtime="codex",
        )
        self.assertEqual(details["skill_count"], 1)
        self.assertEqual(details["skill_ids"], ("bounded-answer",))
        rendered = context_ui_details("ja", details)
        self.assertIn("bounded-answer", rendered)
        self.assertNotIn("unused-skill", rendered)
        self.assertNotIn(" / 指示 -", rendered)
        for key in ("skill_ids_digest", "instruction_digest", "acceptance_digest"):
            self.assertRegex(str(details[key]), r"^[0-9a-f]{64}$")

    def test_context_display_normalizes_only_u0020_numeric_references(self) -> None:
        details = {
            "selection_kind": "skill",
            "selected_skill_id": "bounded-answer",
            "pack_id": "bounded-pack",
            "skill_ids": ("bounded-answer",),
            "repository_url": "https://example.test/pack.git",
            "expected_commit": "abc123",
            "runtime": "codex",
            "project": r"C:\Projects\target",
        }
        actual_request = "（鬼レビュー対応）&#x20;&lt;保持&gt;&amp;#x20;"

        rendered = context_ui_confirmation("ja", details, actual_request)

        self.assertNotIn("&#x20;", rendered)
        self.assertIn("（鬼レビュー対応） &lt;保持&gt;&amp;#x20;", rendered)
        self.assertEqual(
            actual_request, "（鬼レビュー対応）&#x20;&lt;保持&gt;&amp;#x20;"
        )

    def test_context_contract_and_handoff_canonicalize_only_u0020_references(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, str(self.project))
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        details = context_selection_details(
            engine,
            project=self.project,
            pack_id=leaf.pack_id,
            skill_id=leaf.skill_id,
            runtime="codex",
            menu_commit=self.commit,
            menu_skill_digest=leaf.skill_ids_digest,
            menu_instruction_digest=leaf.instruction_digest,
            menu_acceptance_digest=leaf.acceptance_digest,
        )
        actual_request = "（**上記**重大な未完了事項  ）&#x20;&lt;保持&gt;&amp;#x20;"

        contract = confirm_context_selection(
            engine,
            platform="windows",
            details=details,
            purpose=actual_request,
            confirmed=True,
        )

        self.assertIsNotNone(contract)
        canonical_request = "（**上記**重大な未完了事項  ） &lt;保持&gt;&amp;#x20;"
        self.assertEqual(contract.purpose, canonical_request)
        prepared = engine.prepare_codex_desktop_handoff(contract.contract_id)
        self.assertIn(canonical_request, prepared["prompt"])
        self.assertNotIn("）&#x20;", prepared["prompt"])
        self.assertEqual(
            prepared["actual_request_sha256"],
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        )

    def test_menu_display_normalizes_space_reference_without_decoding_markup(self) -> None:
        raw_config = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw_config["packs"][0]["skill_metadata"] = {
            "bounded-answer": {
                "display_name": "境界&#x20;&lt;表示名&gt;",
                "purpose": "用途&#32;&lt;保持&gt;",
            }
        }
        self.config_path.write_text(
            json.dumps(raw_config, ensure_ascii=False), encoding="utf-8"
        )

        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, str(self.project))
            if item.skill_id == "bounded-answer"
        )

        self.assertEqual(leaf.skill_label, "Skill: 境界 &lt;表示名&gt;")
        self.assertEqual(leaf.purpose, "用途 &lt;保持&gt;")
        persisted = self.config_path.read_text(encoding="utf-8")
        self.assertIn("境界&#x20;&lt;表示名&gt;", persisted)
        self.assertIn("用途&#32;&lt;保持&gt;", persisted)

    def test_runtime_failures_show_one_error_and_never_verify(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, "%1")
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        schema_script = self.root / "schema_failure.py"
        schema_script.write_text(
            "import json,pathlib,sys\n"
            "a=sys.argv[1:];sys.stdin.read();"
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text(json.dumps({}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        output_script = self.root / "output_failure.py"
        output_script.write_text(
            "import json,sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps({'type':'error','error':{'code':'invalid_json_schema'}}))\n"
            "sys.stderr.write(\"error: unexpected argument '--legacy-flag' token=sk-secret-value\\n\")\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        acceptance_script = self.root / "acceptance_failure.py"
        acceptance_script.write_text(
            "import json,pathlib,sys\n"
            "a=sys.argv[1:];p=sys.stdin.read();"
            "e=json.loads(next(x for x in p.splitlines() if x.startswith('PROVENANCE=')).split('=',1)[1]);"
            "pathlib.Path(a[a.index('--output-last-message')+1]).write_text("
            "json.dumps({'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['bounded-answer:wrong']},'result':{'task_output':'deliverable','decision':'wrong'}}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        cases = (
            ("launch", str(self.root / "missing-codex"), "launch_failed"),
            ("schema", (sys.executable, str(schema_script)), "output_failed"),
            ("output", (sys.executable, str(output_script)), "runtime_failed"),
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
                contract = engine.confirm(
                    engine.plan(
                        platform="windows",
                        project=self.project,
                        pack_id=leaf.pack_id,
                        runtime="codex",
                        purpose=f"runtime failure {name}",
                        skill_id=leaf.skill_id,
                    ),
                    confirmed=True,
                )
                with self.assertRaises(Exception):
                    engine.execute(
                        contract.contract_id,
                        codex_executable=executable,
                    )
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
                if name == "output":
                    diagnostic = failure["runtime_failure_evidence"]
                    self.assertEqual(diagnostic["exit_code"], 2)
                    self.assertEqual(
                        diagnostic["failure_class"], "invalid_output_schema"
                    )
                    self.assertTrue(diagnostic["stderr_present"])
                    self.assertEqual(len(diagnostic["stderr_sha256"]), 64)
                    self.assertEqual(
                        diagnostic["stderr_summary"],
                        "Codex rejected the verification output schema.",
                    )
                    serialized = json.dumps(failure, ensure_ascii=False)
                    self.assertNotIn("sk-secret-value", serialized)
                    self.assertNotIn("--legacy-flag", serialized)
                    self.assertNotIn("invalid_json_schema", serialized)
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
        )
        engine = ActivationEngine(self.config, self.state)
        shown: list[str] = []
        expected = {"status": "verified_completed", "interactive_handoff": {"runtime": "claude"}}
        with mock.patch.object(engine, "execute", return_value=expected) as runtime:
            result = launch_context_leaf(
                engine,
                platform="windows",
                project=self.project,
                pack_id=leaf.pack_id,
                skill_id=leaf.skill_id,
                runtime="claude",
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
            "o={'evidence':{**e,'completed_skill_ids':e['skill_ids'],'skill_execution_status':'completed','applied_rules':['bounded-answer:bounded']},'result':{'task_output':'deliverable','decision':'bounded'}}\n"
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
        self.assertEqual(result["status"], "verified_completed")
        self.assertEqual(result["interactive_handoff"]["runtime"], "claude")
        self.assertEqual(result["interactive_handoff"]["state"], "test_suppressed")

    def test_codex_verification_uses_process_local_mcp_overrides(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="verify process-local MCP isolation",
            skill_id="bounded-answer",
        )
        contract = engine.confirm(plan, confirmed=True)
        original_run = subprocess.run
        captured: list[str] = []
        runtime_kwargs: dict[str, object] = {}

        def capture_runtime(*args: object, **kwargs: object) -> object:
            command = args[0]
            if (
                isinstance(command, list)
                and len(command) >= 2
                and tuple(command[:2]) == self.fake_codex
            ):
                captured.extend(str(value) for value in command)
                runtime_kwargs.update(kwargs)
            return original_run(*args, **kwargs)

        with mock.patch(
            "skill_magnet.activation.subprocess.run", side_effect=capture_runtime
        ):
            result = engine.execute(
                contract.contract_id,
                codex_executable=self.fake_codex,
            )

        self.assertEqual(result["status"], "verified_completed")
        self.assertTrue(captured)
        self.assertNotIn("--ignore-user-config", captured)
        self.assertIn("--ephemeral", captured)
        self.assertIn("--ignore-rules", captured)
        self.assertEqual(
            [
                captured[index + 1]
                for index, value in enumerate(captured[:-1])
                if value == "-c"
            ],
            list(CODEX_PROCESS_CONFIG_OVERRIDES),
        )
        self.assertEqual(
            codex_process_config_args(),
            [
                item
                for override in CODEX_PROCESS_CONFIG_OVERRIDES
                for item in ("-c", override)
            ],
        )
        self.assertLess(captured.index("-c"), captured.index("exec"))
        if sys.platform == "win32":
            self.assertEqual(
                runtime_kwargs["creationflags"], subprocess.CREATE_NO_WINDOW
            )

    def test_same_actual_request_fixture_reaches_verified_completed(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        contract = engine.confirm(
            engine.plan(
                platform="windows",
                project=self.project,
                pack_id="bounded-pack",
                runtime="codex",
                purpose="うんこ",
                skill_id="bounded-answer",
            ),
            confirmed=True,
        )
        result = engine.execute(
            contract.contract_id,
            codex_executable=(
                sys.executable,
                "-X",
                "utf8",
                str(self.fake_codex[1]),
            ),
        )
        self.assertEqual(result["status"], "verified_completed")
        self.assertEqual(result["user_result"]["request"], "うんこ")
        self.assertEqual(
            result["skill_execution_completion_evidence"]["actual_request_sha256"],
            hashlib.sha256("うんこ".encode("utf-8")).hexdigest(),
        )

    def test_verification_session_is_not_resumed_and_surface_hides_raw_json(self) -> None:
        engine = ActivationEngine(self.config, self.state)
        plan = engine.plan(
            platform="windows",
            project=self.project,
            pack_id="bounded-pack",
            runtime="codex",
            purpose="show only the user result",
            skill_id="bounded-answer",
        )
        contract = engine.confirm(plan, confirmed=True)
        original_run = subprocess.run
        captured: list[str] = []

        def capture_runtime(*args: object, **kwargs: object) -> object:
            command = args[0]
            if (
                isinstance(command, list)
                and len(command) >= 2
                and tuple(command[:2]) == self.fake_codex
            ):
                captured.extend(str(value) for value in command)
            return original_run(*args, **kwargs)

        with mock.patch(
            "skill_magnet.activation.subprocess.run", side_effect=capture_runtime
        ):
            result = engine.execute(
                contract.contract_id,
                codex_executable=self.fake_codex,
                interactive_handoff=True,
            )

        handoff = result["interactive_handoff"]
        self.assertEqual(handoff["state"], "result_surface_ready")
        self.assertFalse(handoff["verification_session_resumed"])
        self.assertNotIn("resume", captured)
        self.assertNotIn("session_id", handoff)
        surface = context_result_surface(result)
        self.assertEqual(surface["title"], "完了")
        self.assertEqual(surface["executed_skill"], "bounded-answer")
        self.assertEqual(surface["request"], "show only the user result")
        self.assertEqual(surface["result"], "show only the user result")
        self.assertIn("保存先/変更の申告なし", surface["saved_or_changed"])
        main_surface = {
            key: surface[key]
            for key in ("title", "executed_skill", "request", "result", "saved_or_changed")
        }
        rendered = json.dumps(main_surface, ensure_ascii=False)
        for raw_field in (
            "evidence",
            "contract_id",
            "instruction_digest",
            "actual_request_sha256",
            "thread.started",
        ):
            self.assertNotIn(raw_field, rendered)
        with_changes = engine._user_result(
            contract,
            self.config.packs["bounded-pack"],
            {
                "evidence": {"completed_skill_ids": ["bounded-answer"]},
                "result": {
                    "task_output": "完了結果",
                    "saved_paths": ["docs/result.md"],
                    "changes": ["結果文書を更新"],
                }
            },
        )
        changed_surface = context_result_surface(
            {"status": "verified_completed", "user_result": with_changes}
        )
        self.assertIn("保存先: docs/result.md", changed_surface["saved_or_changed"])
        self.assertIn("変更: 結果文書を更新", changed_surface["saved_or_changed"])

    def test_failed_and_blocked_surfaces_are_japanese_and_never_success(self) -> None:
        failed = context_failure_surface(_LaunchFailed("raw runtime diagnostic"))
        runtime_failed = context_failure_surface(
            _RuntimeFailed(exit_code=1, stderr="raw runtime diagnostic")
        )
        blocked_output = context_failure_surface(
            _OutputFailed("cloudflare-builds warning and raw JSON")
        )
        blocked_acceptance = context_failure_surface(
            _AcceptanceFailed("digest mismatch detail")
        )

        self.assertEqual(failed["state"], "failed")
        self.assertEqual(runtime_failed["state"], "failed")
        self.assertEqual(blocked_output["state"], "blocked")
        self.assertEqual(blocked_acceptance["state"], "blocked")
        for surface in (failed, runtime_failed, blocked_output, blocked_acceptance):
            self.assertNotEqual(surface["title"], "完了")
            self.assertTrue(surface["cause"])
            self.assertTrue(surface["not_completed"])
            self.assertTrue(surface["next_action"])
            rendered = json.dumps(surface, ensure_ascii=False)
            self.assertNotIn("cloudflare-builds", rendered)
            self.assertNotIn("digest mismatch detail", rendered)
        message = context_failure_message(_OutputFailed("raw JSON"))
        self.assertIn("原因", message)
        self.assertIn("未実行・未確認の範囲", message)
        self.assertIn("次の操作", message)

    def _obsolete_visible_handoff_routes_both_runtime_commands_through_terminal(self) -> None:
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

    def _obsolete_failed_handoff_teardown_kills_real_codex_and_claude_process_trees(self) -> None:
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

    def _obsolete_pid_missing_handoff_invokes_identity_limited_teardown_for_both_runtimes(self) -> None:
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

        for root_name in ("Directory", "Background"):
            with self.subTest(root=root_name):
                # Explorer owns menu opening. Closing it without pressing the
                # direct root emits no command, so no runner is dispatched.
                selected_root_command = None
                if selected_root_command is not None:
                    runner(selected_root_command)

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
        self.assertEqual(command[0], str(Path(sys.executable)))
        self.assertNotIn(
            "skillmagnetlauncher.exe", (part.casefold() for part in command)
        )
        self.assertEqual(
            command[command.index("--config") + 1],
            os.path.abspath(str(self.config_path)),
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

    def test_windows_classic_registry_entry_builders_are_disabled(self) -> None:
        for root_name, entry_builder in (
            ("Directory", windows_directory_registry_entries),
            ("Background", windows_background_registry_entries),
        ):
            with self.subTest(root=root_name):
                with self.assertRaisesRegex(
                    SkillMagnetError, "classic context-menu registration is disabled"
                ):
                    entry_builder(self.config_path)
        with self.assertRaisesRegex(
            SkillMagnetError, "classic context-menu registration is disabled"
        ):
            _windows_registry_entries(
                self.config_path,
                r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic",
                "%1",
            )

    def test_python_product_source_has_no_classic_registry_creation_payload(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "skill_magnet"
        reg_add_literals: list[tuple[str, int]] = []
        registry_headers: list[tuple[str, int]] = []
        registry_create_calls: list[tuple[str, int, str]] = []
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.List, ast.Tuple)):
                    values = [
                        item.value if isinstance(item, ast.Constant) else None
                        for item in node.elts[:2]
                    ]
                    if values == ["reg", "add"]:
                        reg_add_literals.append((path.name, node.lineno))
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "Windows Registry Editor Version" in node.value
                ):
                    registry_headers.append((path.name, node.lineno))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"CreateKey", "CreateKeyEx", "SetValue", "SetValueEx"}:
                        registry_create_calls.append(
                            (path.name, node.lineno, node.func.attr)
                        )
        self.assertEqual(reg_add_literals, [])
        self.assertEqual(registry_headers, [])
        self.assertEqual(registry_create_calls, [])

    def test_windows_classic_registry_builders_reject_special_paths_without_output(self) -> None:
        config = self.root / "config 空白 日本語 & ( ) ' ! ^ # %.json"
        config.write_bytes(self.config_path.read_bytes())
        original = config.read_bytes()
        for root_name, entry_builder in (
            ("Directory", windows_directory_registry_entries),
            ("Background", windows_background_registry_entries),
        ):
            with self.subTest(root=root_name):
                with self.assertRaisesRegex(
                    SkillMagnetError, "classic context-menu registration is disabled"
                ):
                    entry_builder(config)
        self.assertEqual(config.read_bytes(), original)

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
            if item.pack_id == "bounded-pack"
        )
        details = context_selection_details(
            engine,
            project=self.project,
            pack_id=leaf.pack_id,
            runtime="codex",
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

    def test_windows_context_routes_codex_contract_to_desktop_handoff(self) -> None:
        leaf = next(
            item
            for item in windows_menu_leaves(self.config_path, str(self.project))
            if item.pack_id == "bounded-pack"
            and item.skill_id == "bounded-answer"
        )
        activation = mock.Mock()
        contract = SimpleNamespace(
            contract_id="actual-request-contract", runtime="codex"
        )
        handoff_result = {
            "status": "desktop_handoff_ready",
            "verified_completed": False,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = list(leaf.command[leaf.command.index("--config") :])
        argv[argv.index("--config") + 1] = str(self.config_path)
        with (
            mock.patch("skill_magnet.cli.ActivationEngine", return_value=activation),
            mock.patch(
                "skill_magnet.cli.show_context_selection", return_value=contract
            ) as selection,
            mock.patch(
                "skill_magnet.cli.deliver_prepared_codex_handoff",
                return_value=handoff_result,
            ) as desktop_handoff,
            mock.patch("skill_magnet.cli.show_context_result") as result_surface,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)
        selection.assert_called_once()
        self.assertEqual(
            selection.call_args.kwargs["skill_id"], "bounded-answer"
        )
        desktop_handoff.assert_called_once_with(
            activation, "actual-request-contract"
        )
        activation.execute.assert_not_called()
        result_surface.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_windows_root_launcher_opens_dynamic_selection_and_library_manager(self) -> None:
        activation = mock.Mock()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("skill_magnet.cli.ActivationEngine", return_value=activation),
            mock.patch(
                "skill_magnet.cli.show_context_selection", return_value=None
            ) as selection,
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
                    "--launcher",
                ]
            )
        self.assertEqual(exit_code, 0)
        selection.assert_called_once()
        self.assertTrue(selection.call_args.kwargs["allow_dynamic_selection"])
        self.assertIsNone(selection.call_args.kwargs["pack_id"])
        self.assertTrue(callable(selection.call_args.kwargs["library_manager"]))
        self.assertTrue(callable(selection.call_args.kwargs["register_selected"]))
        self.assertTrue(callable(selection.call_args.kwargs["window_ready"]))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_root_launcher_dispatches_library_action_outside_tk_callback(self) -> None:
        for action, extra in (
            ("library_manager", {}),
            ("register_selected", {"register_selected": True}),
        ):
            with (
                self.subTest(action=action),
                mock.patch(
                    "skill_magnet.cli.show_context_selection",
                    return_value=ContextUiAction(action),
                ),
                mock.patch("skill_magnet.cli._show_library_manager_ui") as manager,
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
                        "--launcher",
                    ]
                )
            self.assertEqual(exit_code, 0)
            manager.assert_called_once_with(
                mock.ANY,
                self.project.resolve(),
                context_lease=mock.ANY,
                **extra,
            )

    def test_root_launcher_surfaces_library_startup_failure(self) -> None:
        with (
            mock.patch(
                "skill_magnet.cli.show_context_selection",
                return_value=ContextUiAction("register_selected"),
            ),
            mock.patch(
                "skill_magnet.cli._show_library_manager_ui",
                side_effect=OSError("state directory is unavailable"),
            ),
            mock.patch("skill_magnet.cli.show_context_error") as error_ui,
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
                    "--launcher",
                ]
            )
        self.assertEqual(exit_code, 2)
        error_ui.assert_called_once()
        message = error_ui.call_args.args[0]
        expected_repair = subprocess.list2cmdline(
            [
                sys.executable,
                "-I",
                "-m",
                "skill_magnet",
                "--config",
                str(self.config_path.resolve()),
                "--state-dir",
                str(self.state.resolve()),
                "library",
                "ui",
            ]
        )
        self.assertIn("state directory is unavailable", message)
        self.assertIn(expected_repair, message)
        self.assertIn(str(self.config_path.resolve()), message)
        self.assertIn("library ui", message)

    def test_root_launcher_surfaces_unexpected_ui_initialization_failure(self) -> None:
        with (
            mock.patch(
                "skill_magnet.cli.show_context_selection",
                side_effect=OSError("display initialization failed"),
            ),
            mock.patch("skill_magnet.cli.show_context_error") as error_ui,
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
                    "--launcher",
                ]
            )
        self.assertEqual(exit_code, 2)
        error_ui.assert_called_once()
        message = error_ui.call_args.args[0]
        self.assertIn("OSError", message)
        self.assertIn("display initialization failed", message)
        self.assertIn("library ui", message)

    def test_context_launcher_lease_blocks_duplicate_and_recovers_after_release(self) -> None:
        lease_dir = self.root / "context-lease"
        first = acquire_context_ui_lease(lease_dir, self.project)
        self.assertTrue(first.acquired)
        second = acquire_context_ui_lease(lease_dir, self.project)
        self.assertFalse(second.acquired)
        self.assertTrue(second.owner["same_request"])
        self.assertEqual(second.owner["pid"], os.getpid())
        self.assertNotIn("project", second.owner)
        self.assertRegex(second.owner["target_sha256"], r"^[0-9a-f]{64}$")
        first.release()
        recovered = acquire_context_ui_lease(lease_dir, self.project)
        self.assertTrue(recovered.acquired)
        recovered.release()
        self.assertFalse((lease_dir / "context-launcher.owner.json").exists())

    def test_context_lease_retargets_same_and_different_folder_clicks_to_manager(self) -> None:
        lease_dir = self.root / "context-manager-handoff"
        other_project = self.root / "other-manager-project"
        other_project.mkdir()
        first = acquire_context_ui_lease(lease_dir, self.project)
        self.assertTrue(first.acquired)
        try:
            first.publish_window(phase="context_selection", window_handle=1111)
            first.publish_window(phase="library_manager", window_handle=2222)

            repeated = acquire_context_ui_lease(lease_dir, self.project)
            self.assertFalse(repeated.acquired)
            self.assertTrue(repeated.owner["same_request"])
            self.assertEqual(repeated.owner["phase"], "library_manager")
            self.assertEqual(repeated.owner["window_handle"], 2222)

            different = acquire_context_ui_lease(lease_dir, other_project)
            self.assertFalse(different.acquired)
            self.assertFalse(different.owner["same_request"])
            self.assertEqual(different.owner["phase"], "library_manager")
            self.assertEqual(different.owner["window_handle"], 2222)

            owner_record = json.loads(
                (lease_dir / "context-launcher.owner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(owner_record["phase"], "library_manager")
            self.assertEqual(owner_record["window_handle"], 2222)
        finally:
            first.release()

    def test_context_launcher_lease_recovers_owner_publish_and_release_races(self) -> None:
        lease_dir = self.root / "context-lease-race"
        first = acquire_context_ui_lease(lease_dir, self.project)
        first.owner_path.unlink()
        release = threading.Timer(0.05, first.release)
        release.start()
        recovered = acquire_context_ui_lease(lease_dir, self.project)
        release.join(timeout=1)
        self.assertTrue(recovered.acquired)
        recovered.release()

        cleanup_failure = acquire_context_ui_lease(lease_dir, self.project)
        with mock.patch.object(Path, "unlink", side_effect=OSError("metadata busy")):
            cleanup_failure.release()
        self.assertFalse(cleanup_failure.acquired)
        final = acquire_context_ui_lease(lease_dir, self.project)
        self.assertTrue(final.acquired)
        final.release()

    def test_ui_surface_receipt_is_atomic_generation_bound_and_secret_free(self) -> None:
        secret = "SECRET-request-token-123"
        selected = self.root / f"selected-{secret}"
        selected.mkdir()
        lease_dir = self.root / "semantic-owner"
        lease = acquire_context_ui_lease(lease_dir, selected)

        class Widget:
            def __init__(self, handle: int, *, value: str = "normal") -> None:
                self.handle = handle
                self.configured_state = value

            def winfo_id(self) -> int:
                return self.handle

            def winfo_rootx(self) -> int:
                return 100 + self.handle % 10

            def winfo_rooty(self) -> int:
                return 200 + self.handle % 10

            def winfo_width(self) -> int:
                return 80

            def winfo_height(self) -> int:
                return 24

            def winfo_viewable(self) -> bool:
                return True

            def cget(self, key: str) -> str:
                if key != "state":
                    raise KeyError(key)
                return self.configured_state

            def instate(self, states: tuple[str, ...]) -> bool:
                return "!disabled" in states and self.configured_state != "disabled"

        class Root(Widget):
            def update_idletasks(self) -> None:
                pass

            def title(self) -> str:
                return "Skill Magnet"

        try:
            lease.publish_window(phase="context_selection", window_handle=991991)
            owner_path = lease_dir / "context-launcher.owner.json"
            identity = ui_surface_owner_identity(
                owner_path,
                phase="context_selection",
                window_handle=991991,
            )
            choices = (f"Private {secret}", "Second private skill")
            widgets = (
                UiWidgetSpec(
                    "selection_choice",
                    Widget(991992),
                    "combobox",
                    value=choices[0],
                    values=choices,
                    hash_value=True,
                    hash_values=True,
                ),
                UiWidgetSpec("request", Widget(991993), "entry"),
            )
            surface = publish_tk_ui_surface(
                identity,
                Root(991991),
                widgets=widgets,
                state={"request_present": True, "request_length": len(secret)},
            )
            record = json.loads(owner_path.read_text(encoding="utf-8"))
            selector = surface["widgets"][0]
            request = surface["widgets"][1]
            self.assertEqual(selector["value_count"], 2)
            self.assertNotIn("value", selector)
            self.assertNotIn("values", selector)
            for private_field in (
                "text",
                "value",
                "values",
                "text_sha256",
                "value_sha256",
                "values_sha256",
            ):
                self.assertNotIn(private_field, request)
            self.assertEqual(surface["generation"], record["generation"])
            self.assertEqual(surface["revision"], record["revision"])
            self.assertEqual(surface["window"]["hwnd"], record["window_handle"])
            self.assertNotIn(secret, owner_path.read_text(encoding="utf-8"))
            lease.handle.seek(1)
            self.assertNotIn(
                secret,
                lease.handle.read().decode("utf-8", errors="ignore"),
            )
            self.assertNotIn("project", record)
            fixed = build_tk_ui_surface(
                Root(991991),
                identity=identity,
                widgets=(
                    UiWidgetSpec(
                        "selection_choice",
                        Widget(991994),
                        "label",
                        value=f"Fixed {secret}",
                        hash_value=True,
                    ),
                ),
                state={"selection_mode": "fixed"},
            )
            self.assertEqual(fixed["widgets"][0]["role"], "label")
            self.assertNotIn(secret, json.dumps(fixed))

            stale = dict(record)
            stale["published_at_utc"] = "2000-01-01T00:00:00Z"
            _atomic_write_ui_owner_record(owner_path, stale)
            with self.assertRaisesRegex(SkillMagnetError, "changed"):
                ui_surface_owner_identity(
                    owner_path,
                    phase="context_selection",
                    window_handle=991991,
                )
            reused_pid = dict(record)
            reused_pid["process_instance_id"] = "0" * 32
            _atomic_write_ui_owner_record(owner_path, reused_pid)
            with self.assertRaisesRegex(SkillMagnetError, "changed"):
                ui_surface_owner_identity(
                    owner_path,
                    phase="context_selection",
                    window_handle=991991,
                )

            tampered = dict(record)
            tampered["generation"] = "f" * 32
            _atomic_write_ui_owner_record(owner_path, tampered)
            with self.assertRaisesRegex(SkillMagnetError, "changed"):
                publish_tk_ui_surface(
                    identity, Root(991991), widgets=widgets, state={}
                )
            lease.release()
            self.assertTrue(owner_path.exists(), "old owner must not delete a replacement")
            self.assertEqual(
                json.loads(owner_path.read_text(encoding="utf-8"))["generation"],
                "f" * 32,
            )
        finally:
            if lease.acquired:
                lease.release()

    def test_ui_owner_atomic_failure_and_invalid_json_preserve_previous_record(self) -> None:
        lease_dir = self.root / "atomic-owner"
        lease = acquire_context_ui_lease(lease_dir, self.project)
        owner_path = lease_dir / "context-launcher.owner.json"
        before = owner_path.read_bytes()
        payload = json.loads(before)
        payload["revision"] += 1
        try:
            with mock.patch("skill_magnet.ui.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    _atomic_write_ui_owner_record(owner_path, payload)
            self.assertEqual(owner_path.read_bytes(), before)
            with self.assertRaisesRegex(SkillMagnetError, "duplicate key"):
                _owner_json_loads(b'{"pid":1,"pid":2}')
            with self.assertRaisesRegex(SkillMagnetError, "too large"):
                _owner_json_loads(b"{" + b" " * (256 * 1024) + b"}")
            oversized = lease_dir / "oversized-owner.json"
            oversized.write_bytes(b"{" + b" " * (256 * 1024) + b"}")
            with self.assertRaisesRegex(SkillMagnetError, "too large"):
                _read_ui_owner_record(oversized)
        finally:
            lease.release()

    def test_context_ui_owner_rejects_linked_lock_without_touching_target(self) -> None:
        lease_dir = self.root / "linked-owner"
        lease_dir.mkdir()
        outside = self.root / "outside-lock.txt"
        outside.write_text("preserve-me", encoding="utf-8")
        lock_path = lease_dir / "context-launcher.lock"
        try:
            os.symlink(outside, lock_path)
        except OSError:
            lock_path.touch()
            with mock.patch.dict(
                acquire_context_ui_lease.__globals__,
                {"_is_link": lambda path: Path(path).name == "context-launcher.lock"},
            ):
                with self.assertRaisesRegex(SkillMagnetError, "link or junction"):
                    acquire_context_ui_lease(lease_dir, self.project)
        else:
            with self.assertRaisesRegex(SkillMagnetError, "link or junction"):
                acquire_context_ui_lease(lease_dir, self.project)
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve-me")

    def test_duplicate_root_launcher_focuses_existing_window_without_second_ui(self) -> None:
        lease = acquire_context_ui_lease(self.state, self.project)
        try:
            with (
                mock.patch("skill_magnet.cli.focus_context_ui", return_value=True) as focus,
                mock.patch("skill_magnet.cli.show_context_selection") as selection,
                mock.patch("skill_magnet.cli.show_context_error") as error_ui,
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
                        "--launcher",
                    ]
                )
        finally:
            lease.release()
        self.assertEqual(exit_code, 0)
        focus.assert_called_once()
        selection.assert_not_called()
        error_ui.assert_not_called()

    def test_duplicate_root_launcher_gives_recovery_when_focus_fails(self) -> None:
        lease = acquire_context_ui_lease(self.state, self.project)
        try:
            with (
                mock.patch("skill_magnet.cli.focus_context_ui", return_value=False),
                mock.patch("skill_magnet.cli.show_context_selection") as selection,
                mock.patch("skill_magnet.cli.show_context_error") as error_ui,
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
                        "--launcher",
                    ]
                )
        finally:
            lease.release()
        self.assertEqual(exit_code, 0)
        selection.assert_not_called()
        error_ui.assert_called_once()
        message = error_ui.call_args.args[0]
        self.assertIn("すでに処理中", message)
        self.assertIn("復旧方法", message)
        self.assertIn("タスク マネージャー", message)

    def test_duplicate_root_launcher_does_not_drop_a_different_folder(self) -> None:
        other_project = self.root / "other-project"
        other_project.mkdir()
        lease = acquire_context_ui_lease(self.state, self.project)
        try:
            with (
                mock.patch("skill_magnet.cli.focus_context_ui", return_value=True),
                mock.patch("skill_magnet.cli.show_context_selection") as selection,
                mock.patch("skill_magnet.cli.show_context_error") as error_ui,
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
                        str(other_project),
                        "--launcher",
                    ]
                )
        finally:
            lease.release()
        self.assertEqual(exit_code, 0)
        selection.assert_not_called()
        error_ui.assert_called_once()
        message = error_ui.call_args.args[0]
        self.assertIn("別のフォルダー", message)
        self.assertIn("処理中フォルダー: 不明", message)
        self.assertNotIn(str(self.project.resolve()), message)
        self.assertIn(str(other_project.resolve()), message)
        self.assertIn("もう一度右クリック", message)

    def test_windows_context_config_load_failure_always_shows_actionable_ui(self) -> None:
        missing = self.root / "missing-config.json"
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
                    str(missing),
                    "context",
                    "--platform",
                    "windows",
                    "--project",
                    str(self.project),
                    "--launcher",
                ]
            )
        self.assertEqual(exit_code, 2)
        error_ui.assert_called_once()
        message = error_ui.call_args.args[0]
        expected_repair = subprocess.list2cmdline(
            [
                sys.executable,
                "-I",
                "-m",
                "skill_magnet",
                "--config",
                str(missing.resolve()),
                "library",
                "ui",
            ]
        )
        self.assertIn("原因", message)
        self.assertIn("Library Manager", message)
        self.assertIn(str(missing), message)
        self.assertIn(expected_repair, message)
        self.assertIn("library ui", message)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_context_repair_command_binds_and_quotes_the_running_python(self) -> None:
        executable = r"C:\Program Files\Python 3.12\python.exe"
        config = self.root / "config path & owner" / "skill-magnet.json"
        state = self.root / "state path & owner"
        expected = subprocess.list2cmdline(
            [
                executable,
                "-I",
                "-m",
                "skill_magnet",
                "--config",
                str(config.resolve()),
                "--state-dir",
                str(state.resolve()),
                "library",
                "ui",
            ]
        )

        with mock.patch("skill_magnet.ui.sys.executable", executable):
            surface = context_failure_surface(
                SkillMagnetError("config JSON is invalid"),
                config_path=config,
                state_dir=state,
            )

        self.assertIn(expected, surface["next_action"])
        self.assertNotIn("python -m skill_magnet", surface["next_action"])

    def test_macos_context_recovery_messages_never_name_windows_terminal(self) -> None:
        for error in (
            SkillMagnetError("config JSON is invalid"),
            SkillMagnetError("Library Manager could not recover"),
            SkillMagnetError("unknown launch failure"),
        ):
            with self.subTest(error=str(error)):
                surface = context_failure_surface(
                    error,
                    config_path=self.config_path,
                    state_dir=self.state,
                    platform="macos",
                )
                self.assertIn("Terminalで", surface["next_action"])
                self.assertNotIn("Windows Terminal", surface["next_action"])

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

    def test_windows_classic_installer_fails_closed_without_registry_calls(self) -> None:
        runner = mock.Mock(name="registry_runner")
        with self.assertRaisesRegex(
            SkillMagnetError, "classic context-menu registration is disabled"
        ):
            install_context_menu("windows", self.config_path, run=runner)
        runner.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_windows_public_cli_installs_and_uninstalls_modern_menu_by_default(self) -> None:
        install_result = {"installed": True, "modern": {"installed": True}}
        rollback_result = {"rolled_back": True, "rollback_point_removed": True}
        stdout = io.StringIO()
        with (
            mock.patch("skill_magnet.cli.validate_isolated_menu_runtime") as preflight,
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
        preflight.assert_called_once_with()
        install.assert_called_once_with(self.config_path)
        self.assertEqual(json.loads(stdout.getvalue()), install_result)

        classic_stdout = io.StringIO()
        classic_stderr = io.StringIO()
        with redirect_stdout(classic_stdout), redirect_stderr(classic_stderr):
            render_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "render-context-menu",
                    "--platform",
                    "windows",
                ]
            )
        self.assertEqual(render_code, 2)
        self.assertEqual(classic_stdout.getvalue(), "")
        self.assertIn(
            "classic context-menu registration is disabled",
            classic_stderr.getvalue(),
        )
        self.assertNotIn("Windows Registry Editor", classic_stderr.getvalue())
        self.assertNotIn("reg add", classic_stderr.getvalue().casefold())

        with (
            mock.patch(
                "skill_magnet.cli.validate_isolated_menu_runtime",
                side_effect=SkillMagnetError("isolated runtime mismatch"),
            ),
            mock.patch("skill_magnet.cli.install_windows_context_menus") as blocked_install,
            redirect_stderr(io.StringIO()) as blocked_error,
        ):
            blocked_code = cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "install-context-menu",
                    "--platform",
                    "windows",
                    "--confirm",
                ]
            )
        self.assertEqual(blocked_code, 2)
        blocked_install.assert_not_called()
        self.assertIn("isolated runtime mismatch", blocked_error.getvalue())

        stdout = io.StringIO()
        with (
            mock.patch(
                "skill_magnet.cli.uninstall_windows_context_menus",
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

    def test_menu_runtime_preflight_rejects_split_or_editable_generation(self) -> None:
        purelib = self.root / "site-packages"
        module_init = purelib / "skill_magnet" / "__init__.py"
        module_init.parent.mkdir(parents=True)
        module_init.write_text("# probe path\n", encoding="utf-8")

        def probe_result(*, module: str, distribution: str, editable: bool) -> SimpleNamespace:
            from skill_magnet import __version__
            from skill_magnet.platforms import _python_runtime_source_sha256, _PACKAGE_ROOT

            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    {
                        "module_version": module,
                        "distribution_version": distribution,
                        "distribution_name": "skill-magnet",
                        "module_init": str(module_init),
                        "distribution_module_init": str(module_init),
                        "purelib": str(purelib),
                        "editable": editable,
                        "python_payload_sha256": _python_runtime_source_sha256(_PACKAGE_ROOT),
                    }
                ),
            )

        from skill_magnet import __version__

        valid = validate_isolated_menu_runtime(
            run=lambda *_args, **_kwargs: probe_result(
                module=__version__, distribution=__version__, editable=False
            )
        )
        self.assertEqual(valid["module_version"], __version__)
        for result in (
            probe_result(module="0.5.0", distribution=__version__, editable=False),
            probe_result(module=__version__, distribution=__version__, editable=True),
        ):
            with self.subTest(result=result), self.assertRaisesRegex(
                SkillMagnetError, "まだ登録していません"
            ):
                validate_isolated_menu_runtime(run=lambda *_args, **_kwargs: result)

    def test_windows_commands_quote_special_config_path_and_placeholders(self) -> None:
        special = self.root / "config & (日本語) ' quoted.json"
        special.write_bytes(self.config_path.read_bytes())
        leaves = windows_menu_leaves(special, "%1")
        for leaf in leaves:
            command = __import__("subprocess").list2cmdline(list(leaf.command))
            config_argument = leaf.command[leaf.command.index("--config") + 1]
            self.assertIn(f'"{config_argument}"', command)
            self.assertIn("%1", command)
            self.assertIn("--menu-skill-digest", command)

    def test_windows_modern_manifest_has_one_direct_root_launcher(self) -> None:
        rendered = render_windows_modern_menu_manifest(self.config_path)
        lines = rendered.splitlines()
        self.assertEqual(lines[0], "skill-magnet-menu-v4")
        self.assertEqual(len(lines), 2)
        records = [line.split("\t") for line in lines[1:]]
        self.assertTrue(all(len(record) == 7 for record in records))
        launcher = records[0]
        self.assertEqual(
            launcher[:5],
            [
                "__launcher__",
                "Skill Magnet",
                "launcher",
                "root",
                "Skill Magnet",
            ],
        )
        self.assertIn("--launcher", launcher[6])
        self.assertIn(" -I -m skill_magnet ", launcher[6])
        self.assertNotIn("sys.path.insert", launcher[6])
        self.assertEqual(launcher[6].count("__SKILL_MAGNET_PROJECT__"), 1)
        self.assertNotIn("Library Manager\t", rendered)
        self.assertNotIn("Skill Pack:", rendered)
        self.assertNotIn("Skill:", rendered)

        colliding = self.root / "__SKILL_MAGNET_PROJECT__" / "skill-magnet.json"
        colliding.parent.mkdir()
        colliding.write_bytes(self.config_path.read_bytes())
        with self.assertRaisesRegex(Exception, "collides with the project placeholder"):
            render_windows_modern_menu_manifest(colliding)

    def test_library_manager_context_command_preselects_selected_folder(self) -> None:
        command = windows_library_manager_command_argv(
            self.config_path, "C:\\selected library"
        )
        self.assertIn("library", command)
        self.assertIn("ui", command)
        self.assertEqual(
            command[command.index("--repository") + 1], "C:\\selected library"
        )
        self.assertNotIn("--pack", command)
        self.assertNotIn("--runtime", command)
        registration = windows_library_manager_command_argv(
            self.config_path,
            "C:\\selected library",
            register_selected=True,
        )
        self.assertIn("--register-selected", registration)

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
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            action = args[args.index("-Action") + 1]
            installed = action != "uninstall"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": installed,
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "package_full_name": "SkillMagnet.ContextMenu_0.5.9.0_x64_test",
                        "install_location": str(root),
                        "legacy_certificate_thumbprints_removed": (
                            ["A" * 40] if action == "install" else []
                        ),
                    }
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            installed = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=False
            )
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
            removed = uninstall_windows_modern_context_menu(install_root=root, run=fake_run)
        self.assertEqual(installed["contexts"], ["Directory", r"Directory\Background"])
        self.assertEqual(installed["legacy_certificate_thumbprints_removed"], ["A" * 40])
        self.assertTrue(status["dll_exists"])
        self.assertTrue(status["menu_manifest_exists"])
        self.assertEqual(status["menu_leaf_count"], 0)
        self.assertEqual(status["menu_action_count"], 1)
        self.assertEqual(status["root_launcher_entry_count"], 1)
        self.assertEqual(status["configured_selection_count"], 2)
        self.assertEqual(status["library_manager_entry_count"], 0)
        self.assertEqual(status["register_folder_entry_count"], 0)
        self.assertTrue(status["command_target_exists"])
        self.assertTrue(status["command_target_signature_valid"])
        self.assertFalse(status["self_signed_launcher_referenced"])
        self.assertFalse(status["deprecated_launcher_exists"])
        self.assertTrue(status["identity_anchor_exists"])
        self.assertTrue(status["identity_matches"])
        self.assertTrue(status["com_identity_matches"])
        self.assertTrue(status["registered_identity_matches"])
        self.assertEqual(status["classic_owned_roots_present"], [])
        self.assertTrue(status["usable_installed_state"])
        self.assertTrue(removed["removed"])
        self.assertFalse(root.exists())
        self.assertEqual(
            [call[call.index("-Action") + 1] for call in calls if "-Action" in call],
            ["install", "status", "status", "uninstall", "cleanup-certificate"],
        )
        install_call = calls[0]
        external_location = install_call[install_call.index("-ExternalLocation") + 1]
        self.assertTrue(os.path.isabs(external_location))
        self.assertEqual(
            os.path.normcase(os.path.basename(external_location)),
            os.path.normcase(root.name),
        )

    def test_windows_modern_status_rejects_identity_and_com_manifest_tampering(self) -> None:
        root = self.root / "modern-manifest-tamper"
        package_identity = {
            "version": "0.5.9.0",
            "architecture": "X64",
            "publisher": "CN=Skill Magnet Local",
        }

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": True,
                        "name": "SkillMagnet.ContextMenu",
                        **package_identity,
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=False
            )
        manifest = root / "AppxManifest.xml"
        original = manifest.read_bytes()

        document = ET.fromstring(original)
        foundation = {
            "foundation": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
        }
        identity = document.find("foundation:Identity", foundation)
        self.assertIsNotNone(identity)
        identity.set("Version", "0.5.8.0")
        manifest.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["identity_matches"])
        self.assertFalse(status["usable_installed_state"])

        manifest.write_bytes(original)
        document = ET.fromstring(original)
        namespaces = {
            "foundation": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
            "com": "http://schemas.microsoft.com/appx/manifest/com/windows10",
            "desktop4": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/4",
            "desktop5": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/5",
            "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
        }
        command_class = document.find(".//com:Class", namespaces)
        self.assertIsNotNone(command_class)
        command_class.set("Path", "WrongCommand.dll")
        manifest.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["com_identity_matches"])
        self.assertFalse(status["usable_installed_state"])

        for xpath, attribute, invalid_value in (
            (".//foundation:Application", "Executable", "Missing.exe"),
            (".//com:Extension", "Category", "wrong.category"),
            (".//desktop4:Extension", "Category", "wrong.category"),
            (".//com:SurrogateServer", "AppId", "00000000-0000-0000-0000-000000000000"),
            (".//foundation:TargetDeviceFamily", "Name", "Windows.Universal"),
            (".//rescap:Capability", "Name", "wrongCapability"),
        ):
            with self.subTest(xpath=xpath, attribute=attribute):
                document = ET.fromstring(original)
                target = document.find(xpath, namespaces)
                self.assertIsNotNone(target)
                target.set(attribute, invalid_value)
                manifest.write_bytes(
                    ET.tostring(document, encoding="utf-8", xml_declaration=True)
                )
                with mock.patch("skill_magnet.platforms.os.name", "nt"):
                    status = windows_modern_context_menu_status(
                        install_root=root, config=self.config_path, run=fake_run
                    )
                self.assertFalse(status["com_identity_matches"])
                self.assertFalse(status["usable_installed_state"])

        for xpath, result_key in (
            (".//foundation:Identity", "identity_matches"),
            (".//foundation:Application", "com_identity_matches"),
            (".//com:Extension", "com_identity_matches"),
            (".//desktop4:Extension", "com_identity_matches"),
            (".//com:ComServer", "com_identity_matches"),
            (".//com:SurrogateServer", "com_identity_matches"),
            (".//com:Class", "com_identity_matches"),
            (".//desktop4:FileExplorerContextMenus", "com_identity_matches"),
            (".//desktop5:ItemType", "com_identity_matches"),
            (".//desktop5:Verb", "com_identity_matches"),
        ):
            with self.subTest(extra_attribute=xpath):
                document = ET.fromstring(original)
                target = document.find(xpath, namespaces)
                self.assertIsNotNone(target)
                target.set("Unexpected", "must-fail-closed")
                manifest.write_bytes(
                    ET.tostring(document, encoding="utf-8", xml_declaration=True)
                )
                with mock.patch("skill_magnet.platforms.os.name", "nt"):
                    status = windows_modern_context_menu_status(
                        install_root=root, config=self.config_path, run=fake_run
                    )
                self.assertFalse(status[result_key])
                self.assertFalse(status["usable_installed_state"])

        manifest.write_bytes(original)
        document = ET.fromstring(original)
        com_extension = document.find(".//com:Extension", namespaces)
        self.assertIsNotNone(com_extension)
        com_extension.append(
            ET.Element("{http://schemas.microsoft.com/appx/manifest/com/windows10}ComServer")
        )
        manifest.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["com_identity_matches"])
        self.assertFalse(status["usable_installed_state"])

        manifest.write_bytes(original)
        package_identity["version"] = "0.5.8.0"
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["registered_identity_matches"])
        self.assertFalse(status["usable_installed_state"])

        package_identity["version"] = "0.5.9.0"
        package_identity["same_name_package_count"] = 2
        package_identity["expected_identity_match_count"] = 1
        package_identity["unexpected_same_name_package_count"] = 1
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["registered_identity_matches"])
        self.assertFalse(status["usable_installed_state"])
        package_identity.pop("same_name_package_count")
        package_identity.pop("expected_identity_match_count")
        package_identity.pop("unexpected_same_name_package_count")

        manifest.write_bytes(original)
        document = ET.fromstring(original)
        item = document.find(".//desktop5:ItemType", namespaces)
        self.assertIsNotNone(item)
        duplicate = ET.Element(
            "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/5}Verb",
            {
                "Id": "DuplicateVerb",
                "Clsid": "13E2A9DD-4378-4F9D-A385-973C61B19E63",
            },
        )
        item.append(duplicate)
        manifest.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["com_identity_matches"])
        self.assertFalse(status["usable_installed_state"])

        manifest.write_bytes(original)
        document = ET.fromstring(original)
        container = document.find(".//desktop4:FileExplorerContextMenus", namespaces)
        self.assertIsNotNone(container)
        container.append(
            ET.Element(
                "{http://schemas.microsoft.com/appx/manifest/desktop/windows10/5}ItemType"
            )
        )
        manifest.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["com_identity_matches"])
        self.assertFalse(status["usable_installed_state"])

    def test_windows_modern_status_rejects_native_source_and_binary_tampering(self) -> None:
        root = self.root / "modern-native-binding-tamper"

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": True,
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            installed = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=False
            )
        self.assertTrue(installed["native_build_binding_valid"])

        manifest_path = root / "SkillMagnetNativeSource.json"
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["source_tree_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertFalse(status["native_source_manifest_valid"])
        self.assertFalse(status["native_build_binding_valid"])
        self.assertFalse(status["usable_installed_state"])

        manifest_path.write_bytes(original_manifest)
        dll_path = root / "SkillMagnetCommand.dll"
        dll_path.write_bytes(b"old-signed-dll-without-current-source-binding")
        manifest = json.loads(original_manifest)
        manifest["artifacts"][0] = {
            "path": "SkillMagnetCommand.dll",
            "size": dll_path.stat().st_size,
            "sha256": hashlib.sha256(dll_path.read_bytes()).hexdigest(),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            status = windows_modern_context_menu_status(
                install_root=root, config=self.config_path, run=fake_run
            )
        self.assertTrue(status["native_source_manifest_valid"])
        self.assertTrue(status["native_artifact_hashes_valid"])
        self.assertFalse(status["dll_native_source_binding_valid"])
        self.assertFalse(status["native_build_binding_valid"])
        self.assertFalse(status["usable_installed_state"])

    def test_windows_product_install_runs_native_contract_test(self) -> None:
        root = self.root / "policy-safe-modern-install"
        source_output = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "out"
        )
        calls: list[list[str]] = []
        build_outputs: list[Path] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            if any(str(item).endswith("build.ps1") for item in args):
                output = type(self.root)(str(args[args.index("-OutDir") + 1]))
                build_outputs.append(output)
                nonce = args[args.index("-BuildNonce") + 1]
                marker = json.loads(
                    (output.parent / ".skill-magnet-native-build.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(marker["nonce"], nonce)
                self.assertEqual(list(output.iterdir()), [])
                for name in (
                    "SkillMagnetCommand.dll",
                    "SkillMagnetIdentity.exe",
                    "SkillMagnetNativeSource.json",
                ):
                    shutil.copy2(source_output / name, output / name)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if any(str(item).endswith("build-package.ps1") for item in args):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": True,
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
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
        self.assertNotIn("-SkipContractTest", build_call)
        self.assertEqual(len(build_outputs), 1)
        self.assertNotEqual(build_outputs[0], source_output)
        self.assertFalse(build_outputs[0].exists())

    def test_installed_wheel_build_quarantines_package_residue_without_deleting_it(
        self,
    ) -> None:
        package_root = self.root / "installed" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        shutil.copytree(
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu",
            native_root,
            ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
        )
        legacy_output = native_root / "out"
        legacy_output.mkdir()
        (legacy_output / "SkillMagnetCommand.lib").write_bytes(b"stale build output")
        root = self.root / "installed-wheel-context-menu"
        calls: list[list[str]] = []
        build_outputs: list[Path] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            if any(str(item).endswith("build.ps1") for item in args):
                output = type(self.root)(str(args[args.index("-OutDir") + 1]))
                build_outputs.append(output)
                self.assertEqual(list(output.iterdir()), [])
                source_manifest = _windows_native_source_manifest(native_root)
                source_digest = str(source_manifest["source_tree_sha256"])
                artifacts = {
                    "SkillMagnetCommand.dll": (
                        b"test-dll\0"
                        + (
                            "skill-magnet-native-source-v1:" + source_digest
                        ).encode("utf-16-le")
                    ),
                    "SkillMagnetIdentity.exe": b"test-identity",
                }
                for name, payload in artifacts.items():
                    (output / name).write_bytes(payload)
                source_manifest["artifacts"] = [
                    {
                        "path": name,
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                    for name, payload in artifacts.items()
                ]
                (output / "SkillMagnetNativeSource.json").write_text(
                    json.dumps(source_manifest, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if any(str(item).endswith("build-package.ps1") for item in args):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            action = args[args.index("-Action") + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": action != "uninstall",
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
        ):
            result = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=True
            )
        self.assertTrue(result["usable_installed_state"])
        self.assertFalse(result["packaged_native_build_residue_removed"])
        self.assertTrue(result["packaged_native_build_residue_preserved"])
        self.assertFalse(legacy_output.exists())
        recovery = Path(result["packaged_native_build_recovery_directory"])
        preserved = list(recovery.glob("legacy-out-*"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(
            (preserved[0] / "SkillMagnetCommand.lib").read_bytes(),
            b"stale build output",
        )
        self.assertTrue(
            (recovery / f"complete-{result['packaged_native_build_recovery_id']}.json").is_file()
        )
        self.assertEqual(len(build_outputs), 1)
        self.assertFalse(build_outputs[0].is_relative_to(package_root))
        self.assertFalse(build_outputs[0].exists())

    def test_installed_wheel_quarantines_arbitrary_package_residue_byte_exactly(
        self,
    ) -> None:
        package_root = self.root / "unknown-residue" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        shutil.copytree(
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu",
            native_root,
            ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
        )
        output = native_root / "out"
        output.mkdir()
        foreign = output / "user-data.txt"
        foreign.write_text("preserve", encoding="utf-8")
        root = self.root / "unknown-residue-context-menu"
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            if any(str(item).endswith("build.ps1") for item in args):
                temporary_output = Path(args[args.index("-OutDir") + 1])
                source_manifest = _windows_native_source_manifest(native_root)
                source_digest = str(source_manifest["source_tree_sha256"])
                artifacts = {
                    "SkillMagnetCommand.dll": (
                        b"test-dll\0"
                        + ("skill-magnet-native-source-v1:" + source_digest).encode(
                            "utf-16-le"
                        )
                    ),
                    "SkillMagnetIdentity.exe": b"test-identity",
                }
                for name, payload in artifacts.items():
                    (temporary_output / name).write_bytes(payload)
                source_manifest["artifacts"] = [
                    {
                        "path": name,
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                    for name, payload in artifacts.items()
                ]
                (temporary_output / "SkillMagnetNativeSource.json").write_text(
                    json.dumps(source_manifest, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if any(str(item).endswith("build-package.ps1") for item in args):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            action = args[args.index("-Action") + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": action != "uninstall",
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
        ):
            result = install_windows_modern_context_menu(
                self.config_path,
                install_root=root,
                run=fake_run,
                build=True,
            )
        self.assertTrue(result["usable_installed_state"])
        self.assertFalse(foreign.exists())
        recovery = Path(result["packaged_native_build_recovery_directory"])
        preserved = list(recovery.glob("legacy-out-*/user-data.txt"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), b"preserve")

    def test_installed_wheel_build_failure_restores_package_output_byte_exactly(
        self,
    ) -> None:
        package_root = self.root / "failed-build" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        shutil.copytree(
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu",
            native_root,
            ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
        )
        legacy_output = native_root / "out"
        legacy_output.mkdir()
        (legacy_output / "ContractTest.obj").write_bytes(b"stale")
        build_outputs: list[Path] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            output = type(self.root)(str(args[args.index("-OutDir") + 1]))
            build_outputs.append(output)
            (output / "ContractTest.obj").write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stdout="compile failed", stderr="")

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
            self.assertRaisesRegex(SkillMagnetError, "compile failed"),
        ):
            install_windows_modern_context_menu(
                self.config_path,
                install_root=self.root / "failed-build-context-menu",
                run=fake_run,
                build=True,
            )
        self.assertTrue(legacy_output.exists())
        self.assertEqual((legacy_output / "ContractTest.obj").read_bytes(), b"stale")
        self.assertEqual(len(build_outputs), 1)
        self.assertFalse(build_outputs[0].exists())
        self.assertFalse((self.root / "failed-build-context-menu").exists())

    def test_installed_wheel_restores_legacy_output_at_every_failure_stage(
        self,
    ) -> None:
        stages = (
            "build",
            "missing-output",
            "package-build",
            "package-install",
            "status-readback",
            "workspace-cleanup",
            "quarantine-completion",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                package_root = self.root / stage / "site-packages" / "skill_magnet"
                native_root = package_root / "_native" / "windows-modern-context-menu"
                shutil.copytree(
                    Path(__file__).resolve().parents[1]
                    / "native"
                    / "windows-modern-context-menu",
                    native_root,
                    ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
                )
                legacy_output = native_root / "out"
                (legacy_output / "nested" / "empty").mkdir(parents=True)
                legacy_bytes = bytes(range(256)) + b"\0legacy\xff"
                (legacy_output / "nested" / "user-data.bin").write_bytes(
                    legacy_bytes
                )
                install_root = self.root / stage / "ContextMenu"
                build_outputs: list[Path] = []
                installed = False

                def write_valid_output(output: Path) -> None:
                    source_manifest = _windows_native_source_manifest(native_root)
                    source_digest = str(source_manifest["source_tree_sha256"])
                    artifacts = {
                        "SkillMagnetCommand.dll": (
                            b"test-dll\0"
                            + (
                                "skill-magnet-native-source-v1:" + source_digest
                            ).encode("utf-16-le")
                        ),
                        "SkillMagnetIdentity.exe": b"test-identity",
                    }
                    for name, payload in artifacts.items():
                        (output / name).write_bytes(payload)
                    source_manifest["artifacts"] = [
                        {
                            "path": name,
                            "size": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                        for name, payload in artifacts.items()
                    ]
                    (output / "SkillMagnetNativeSource.json").write_text(
                        json.dumps(source_manifest, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )

                def fake_run(args: list[str], **_: object) -> SimpleNamespace:
                    nonlocal installed
                    if args[:2] == ["reg", "query"]:
                        return SimpleNamespace(
                            returncode=1,
                            stdout="",
                            stderr="__REGISTRY_KEY_NOT_FOUND__",
                        )
                    if any(str(item).endswith("build.ps1") for item in args):
                        output = Path(args[args.index("-OutDir") + 1])
                        build_outputs.append(output)
                        if stage == "build":
                            (output / "ContractTest.obj").write_bytes(b"partial")
                            return SimpleNamespace(
                                returncode=1, stdout="build-stage", stderr=""
                            )
                        if stage != "missing-output":
                            write_valid_output(output)
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if any(
                        str(item).endswith("build-package.ps1") for item in args
                    ):
                        return SimpleNamespace(
                            returncode=1 if stage == "package-build" else 0,
                            stdout="package-stage" if stage == "package-build" else "",
                            stderr="",
                        )
                    action = args[args.index("-Action") + 1]
                    if action == "install":
                        if stage == "package-install":
                            return SimpleNamespace(
                                returncode=1, stdout="", stderr="install-stage"
                            )
                        installed = True
                    observed_installed = installed
                    if action == "status" and stage == "status-readback":
                        observed_installed = False
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "installed": observed_installed,
                                "name": "SkillMagnet.ContextMenu",
                                "version": "0.5.9.0",
                                "architecture": "X64",
                                "publisher": "CN=Skill Magnet Local",
                                "install_location": str(install_root),
                            }
                        ),
                        stderr="",
                    )

                cleanup_patch = (
                    mock.patch(
                        "skill_magnet.platforms._cleanup_windows_native_build_workspace",
                        side_effect=SkillMagnetError("cleanup-stage"),
                    )
                    if stage == "workspace-cleanup"
                    else mock.patch(
                        "skill_magnet.platforms._cleanup_windows_native_build_workspace",
                        wraps=__import__(
                            "skill_magnet.platforms", fromlist=["x"]
                        )._cleanup_windows_native_build_workspace,
                    )
                )
                completion_patch = (
                    mock.patch(
                        "skill_magnet.platforms._complete_native_quarantine",
                        side_effect=SkillMagnetError("completion-stage"),
                    )
                    if stage == "quarantine-completion"
                    else mock.patch(
                        "skill_magnet.platforms._complete_native_quarantine",
                        wraps=__import__(
                            "skill_magnet.platforms", fromlist=["x"]
                        )._complete_native_quarantine,
                    )
                )
                with (
                    mock.patch("skill_magnet.platforms.os.name", "nt"),
                    mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
                    cleanup_patch,
                    completion_patch,
                    self.assertRaises((SkillMagnetError, SafetyError)),
                ):
                    install_windows_modern_context_menu(
                        self.config_path,
                        install_root=install_root,
                        run=fake_run,
                        build=True,
                    )
                self.assertEqual(
                    (legacy_output / "nested" / "user-data.bin").read_bytes(),
                    legacy_bytes,
                )
                self.assertTrue((legacy_output / "nested" / "empty").is_dir())
                self.assertEqual(len(build_outputs), 1)
                if stage == "workspace-cleanup":
                    self.assertTrue(build_outputs[0].parent.exists())
                else:
                    self.assertFalse(build_outputs[0].parent.exists())

    def test_installed_wheel_refuses_reparse_legacy_output_without_running(self) -> None:
        package_root = self.root / "reparse-residue" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        shutil.copytree(
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu",
            native_root,
            ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
        )
        foreign_root = self.root / "foreign-native-output"
        foreign_root.mkdir()
        sentinel = foreign_root / "must-survive.bin"
        sentinel.write_bytes(b"foreign\0bytes")
        output = native_root / "out"
        try:
            output.symlink_to(foreign_root, target_is_directory=True)
        except OSError as exc:
            junction = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(output), str(foreign_root)],
                capture_output=True,
                text=True,
            )
            if junction.returncode != 0 or not output.is_junction():
                self.skipTest(
                    "directory symlink and junction are unavailable: "
                    + str(exc)
                    + " / "
                    + (junction.stderr or junction.stdout).strip()
                )
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
            self.assertRaisesRegex(SafetyError, "symlink or junction|recovery tree"),
        ):
            install_windows_modern_context_menu(
                self.config_path,
                install_root=self.root / "reparse-install-must-not-exist",
                run=fake_run,
                build=True,
            )
        self.assertEqual(calls, [])
        self.assertEqual(sentinel.read_bytes(), b"foreign\0bytes")

    def test_legacy_output_restore_collision_preserves_both_copies(self) -> None:
        package_root = self.root / "restore-collision" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        shutil.copytree(
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu",
            native_root,
            ignore=shutil.ignore_patterns("out", "__pycache__", "*.pyc"),
        )
        legacy_output = native_root / "out"
        legacy_output.mkdir()
        (legacy_output / "legacy.bin").write_bytes(b"legacy")
        build_output: Path | None = None

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal build_output
            build_output = Path(args[args.index("-OutDir") + 1])
            legacy_output.mkdir()
            (legacy_output / "new.bin").write_bytes(b"new-owner")
            (build_output / "ContractTest.obj").write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stdout="forced failure", stderr="")

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
            self.assertRaisesRegex(SkillMagnetError, "both source and preserved copies"),
        ):
            install_windows_modern_context_menu(
                self.config_path,
                install_root=self.root / "collision-install",
                run=fake_run,
                build=True,
            )
        self.assertEqual((legacy_output / "new.bin").read_bytes(), b"new-owner")
        recovery = package_root.parent / ".skill-magnet-native-recovery"
        preserved = list(recovery.glob("legacy-out-*/legacy.bin"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), b"legacy")
        self.assertIsNotNone(build_output)
        self.assertFalse(build_output.parent.exists())

    def test_incomplete_native_quarantine_is_restored_on_next_install(self) -> None:
        from skill_magnet.platforms import (
            _quarantine_packaged_windows_native_output,
            _recover_incomplete_native_quarantines,
        )

        package_root = self.root / "crash-recovery" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        native_root.mkdir(parents=True)
        legacy_output = native_root / "out"
        (legacy_output / "empty").mkdir(parents=True)
        payload = bytes(range(256))
        (legacy_output / "opaque.bin").write_bytes(payload)
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            record = _quarantine_packaged_windows_native_output(native_root)
            self.assertIsNotNone(record)
            self.assertFalse(legacy_output.exists())
            _recover_incomplete_native_quarantines(native_root)
            _recover_incomplete_native_quarantines(native_root)
        self.assertEqual((legacy_output / "opaque.bin").read_bytes(), payload)
        self.assertTrue((legacy_output / "empty").is_dir())
        self.assertFalse(Path(record["target"]).exists())

    def test_native_quarantine_process_crash_phases_recover_deterministically(
        self,
    ) -> None:
        from skill_magnet.platforms import (
            _complete_native_quarantine,
            _quarantine_packaged_windows_native_output,
            _recover_incomplete_native_quarantines,
        )

        for phase in ("after-journal", "after-rename", "after-completion"):
            with self.subTest(phase=phase):
                package_root = (
                    self.root / phase / "site-packages" / "skill_magnet"
                )
                native_root = package_root / "_native" / "windows-modern-context-menu"
                source = native_root / "out"
                source.mkdir(parents=True)
                (source / "opaque.bin").write_bytes(phase.encode("ascii"))
                with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
                    record = _quarantine_packaged_windows_native_output(native_root)
                    self.assertIsNotNone(record)
                    target = Path(record["target"])
                    if phase == "after-journal":
                        os.replace(target, source)
                    elif phase == "after-completion":
                        _complete_native_quarantine(record)
                    _recover_incomplete_native_quarantines(native_root)
                    _recover_incomplete_native_quarantines(native_root)
                if phase == "after-completion":
                    self.assertFalse(source.exists())
                    self.assertEqual(
                        (target / "opaque.bin").read_bytes(), phase.encode("ascii")
                    )
                else:
                    self.assertEqual(
                        (source / "opaque.bin").read_bytes(), phase.encode("ascii")
                    )
                    self.assertFalse(target.exists())

    def test_corrupt_native_recovery_journal_never_deletes_preserved_output(self) -> None:
        from skill_magnet.platforms import (
            _quarantine_packaged_windows_native_output,
            _recover_incomplete_native_quarantines,
        )

        package_root = self.root / "corrupt-journal" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        legacy_output = native_root / "out"
        legacy_output.mkdir(parents=True)
        (legacy_output / "opaque.bin").write_bytes(b"preserve-me")
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            record = _quarantine_packaged_windows_native_output(native_root)
            self.assertIsNotNone(record)
            journal = (
                Path(record["recovery"])
                / f"transaction-{record['nonce']}.json"
            )
            encoded = journal.read_text(encoding="utf-8")
            journal.write_text(
                encoded.replace(
                    '"schema_version":1',
                    '"schema_version":1,"schema_version":1',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SafetyError, "journal is invalid"):
                _recover_incomplete_native_quarantines(native_root)
        target = Path(record["target"])
        self.assertEqual((target / "opaque.bin").read_bytes(), b"preserve-me")
        self.assertFalse(legacy_output.exists())

    def test_native_quarantine_detects_source_swap_without_deleting_either_tree(
        self,
    ) -> None:
        from skill_magnet.platforms import _quarantine_packaged_windows_native_output

        package_root = self.root / "source-race" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        source = native_root / "out"
        source.mkdir(parents=True)
        (source / "original.bin").write_bytes(b"original")
        stolen = native_root / "attacker-moved-original"
        real_replace = os.replace

        def racing_replace(src: object, dst: object) -> None:
            if Path(src) == source:
                real_replace(src, stolen)
                source.mkdir()
                (source / "replacement.bin").write_bytes(b"replacement")
            real_replace(src, dst)

        with (
            mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root),
            mock.patch("skill_magnet.platforms.os.replace", side_effect=racing_replace),
            self.assertRaisesRegex(SafetyError, "snapshot changed"),
        ):
            _quarantine_packaged_windows_native_output(native_root)
        self.assertEqual((stolen / "original.bin").read_bytes(), b"original")
        recovery = package_root.parent / ".skill-magnet-native-recovery"
        replacements = list(recovery.glob("legacy-out-*/replacement.bin"))
        self.assertEqual(len(replacements), 1)
        self.assertEqual(replacements[0].read_bytes(), b"replacement")

    def test_native_recovery_lock_blocks_concurrent_installs(self) -> None:
        from skill_magnet.platforms import (
            _acquire_native_recovery_lock,
            _release_native_recovery_lock,
        )

        package_root = self.root / "concurrent" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        native_root.mkdir(parents=True)
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            first = _acquire_native_recovery_lock(native_root)
            try:
                with self.assertRaisesRegex(SkillMagnetError, "Another .* operation"):
                    _acquire_native_recovery_lock(native_root)
            finally:
                _release_native_recovery_lock(first)
            second = _acquire_native_recovery_lock(native_root)
            _release_native_recovery_lock(second)

    def test_native_recovery_lock_hardlink_never_modifies_peer(self) -> None:
        from skill_magnet.platforms import (
            _acquire_native_recovery_lock,
            _ensure_native_recovery_root,
        )

        package_root = self.root / "lock-hardlink" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        native_root.mkdir(parents=True)
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            recovery = _ensure_native_recovery_root(native_root)
            peer = recovery / "peer.bin"
            peer.write_bytes(b"")
            os.link(peer, recovery / "operation.lock")
            with self.assertRaisesRegex(SafetyError, "lock is unsafe"):
                _acquire_native_recovery_lock(native_root)
        self.assertEqual(peer.read_bytes(), b"")

    def test_native_recovery_lock_permission_error_reports_path_and_action(self) -> None:
        from skill_magnet.platforms import (
            _acquire_native_recovery_lock,
            _ensure_native_recovery_root,
        )

        package_root = self.root / "lock-permission" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        native_root.mkdir(parents=True)
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            recovery = _ensure_native_recovery_root(native_root)
            lock_path = recovery / "operation.lock"
            with (
                mock.patch(
                    "skill_magnet.platforms._ensure_native_recovery_root",
                    return_value=recovery,
                ),
                mock.patch.object(
                    Path,
                    "open",
                    side_effect=PermissionError(13, "permission denied", str(lock_path)),
                ),
                self.assertRaisesRegex(
                    SkillMagnetError,
                    re.escape(str(lock_path)) + ".*access permissions.*disk space",
                ),
            ):
                _acquire_native_recovery_lock(native_root)

    def test_incomplete_native_journal_rejects_changed_or_missing_source(self) -> None:
        from skill_magnet.platforms import (
            _quarantine_packaged_windows_native_output,
            _recover_incomplete_native_quarantines,
        )

        for state in ("changed", "missing"):
            with self.subTest(state=state):
                package_root = (
                    self.root / f"journal-{state}" / "site-packages" / "skill_magnet"
                )
                native_root = package_root / "_native" / "windows-modern-context-menu"
                source = native_root / "out"
                source.mkdir(parents=True)
                (source / "original.bin").write_bytes(b"original")
                with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
                    record = _quarantine_packaged_windows_native_output(native_root)
                    self.assertIsNotNone(record)
                    os.replace(record["target"], source)
                    if state == "changed":
                        (source / "original.bin").write_bytes(b"changed")
                    else:
                        missing_copy = native_root / "witness-missing-copy"
                        os.replace(source, missing_copy)
                    with self.assertRaisesRegex(
                        SafetyError, "snapshot changed|copy is missing"
                    ):
                        _recover_incomplete_native_quarantines(native_root)
                if state == "changed":
                    self.assertEqual(
                        (source / "original.bin").read_bytes(), b"changed"
                    )
                else:
                    self.assertEqual(
                        (missing_copy / "original.bin").read_bytes(), b"original"
                    )

    def test_native_quarantine_completion_rejects_modified_preserved_tree(self) -> None:
        from skill_magnet.platforms import (
            _complete_native_quarantine,
            _quarantine_packaged_windows_native_output,
        )

        package_root = self.root / "completion-race" / "site-packages" / "skill_magnet"
        native_root = package_root / "_native" / "windows-modern-context-menu"
        source = native_root / "out"
        source.mkdir(parents=True)
        (source / "opaque.bin").write_bytes(b"original")
        with mock.patch("skill_magnet.platforms._PACKAGE_ROOT", package_root):
            record = _quarantine_packaged_windows_native_output(native_root)
            self.assertIsNotNone(record)
            target_file = Path(record["target"]) / "opaque.bin"
            target_file.write_bytes(b"modified")
            with self.assertRaisesRegex(SafetyError, "snapshot changed"):
                _complete_native_quarantine(record)
        self.assertEqual(target_file.read_bytes(), b"modified")
        self.assertFalse(source.exists())

    def test_native_workspace_identity_swap_is_never_recursively_deleted(self) -> None:
        from skill_magnet.platforms import (
            _cleanup_windows_native_build_workspace,
            _create_windows_native_build_workspace,
        )

        created = self.root / "workspace-identity-race"

        def fake_mkdtemp(**_: object) -> str:
            created.mkdir()
            return str(created)

        with mock.patch("skill_magnet.platforms.tempfile.mkdtemp", fake_mkdtemp):
            workspace = _create_windows_native_build_workspace(self.root)
        original = created.with_name(created.name + "-original")
        created.rename(original)
        created.mkdir()
        (created / "foreign.bin").write_bytes(b"must-survive")
        with self.assertRaisesRegex(SafetyError, "identity changed"):
            _cleanup_windows_native_build_workspace(workspace)
        self.assertEqual((created / "foreign.bin").read_bytes(), b"must-survive")
        self.assertTrue((original / ".skill-magnet-native-build.json").is_file())
        self.assertTrue((original / "out").is_dir())

    def test_native_workspace_cleanup_swap_never_deletes_replacement(self) -> None:
        from skill_magnet.platforms import (
            _capture_windows_native_cleanup_identity,
            _cleanup_windows_native_build_workspace,
            _create_windows_native_build_workspace,
            _delete_windows_native_path_by_handle,
        )

        created = self.root / "workspace-cleanup-race"

        def fake_mkdtemp(**_: object) -> str:
            created.mkdir()
            return str(created)

        with mock.patch("skill_magnet.platforms.tempfile.mkdtemp", fake_mkdtemp):
            workspace = _create_windows_native_build_workspace(self.root)
        output = Path(workspace["output"])
        (output / "ContractTest.obj").write_bytes(b"owned")
        _capture_windows_native_cleanup_identity(workspace)
        swapped = False
        owned_isolate = self.root / "owned-isolate"

        def swap_then_delete(
            path: Path, expected: dict[str, int], *, directory: bool
        ) -> None:
            nonlocal swapped
            if not swapped:
                swapped = True
                retired = path.parents[1]
                os.replace(retired, owned_isolate)
                (retired / "out").mkdir(parents=True)
                (retired / "out" / "ContractTest.obj").write_bytes(b"foreign")
                (retired / "sentinel.bin").write_bytes(b"must-survive")
            _delete_windows_native_path_by_handle(
                path, expected, directory=directory
            )

        with (
            mock.patch(
                "skill_magnet.platforms._delete_windows_native_path_by_handle",
                side_effect=swap_then_delete,
            ),
            self.assertRaisesRegex(SkillMagnetError, "preserved for recovery"),
        ):
            _cleanup_windows_native_build_workspace(workspace)
        retired = next(self.root.glob("workspace-cleanup-race.cleanup-*"))
        self.assertEqual((retired / "sentinel.bin").read_bytes(), b"must-survive")
        self.assertEqual(
            (owned_isolate / "out" / "ContractTest.obj").read_bytes(), b"owned"
        )

    def test_native_workspace_cleanup_content_change_never_deletes_modified_file(self) -> None:
        from skill_magnet.platforms import (
            _capture_windows_native_cleanup_identity,
            _cleanup_windows_native_build_workspace,
            _create_windows_native_build_workspace,
            _delete_windows_native_path_by_handle,
        )

        created = self.root / "workspace-content-change"
        with mock.patch(
            "skill_magnet.platforms.tempfile.mkdtemp",
            side_effect=lambda **_: (created.mkdir(), str(created))[1],
        ):
            workspace = _create_windows_native_build_workspace(self.root)
        output = Path(workspace["output"])
        artifact = output / "ContractTest.obj"
        artifact.write_bytes(b"owned")
        _capture_windows_native_cleanup_identity(workspace)
        changed = False

        def mutate_then_delete(
            path: Path, expected: dict[str, object], *, directory: bool
        ) -> None:
            nonlocal changed
            if not directory and not changed:
                changed = True
                path.write_bytes(b"other")
            _delete_windows_native_path_by_handle(path, expected, directory=directory)

        with (
            mock.patch(
                "skill_magnet.platforms._delete_windows_native_path_by_handle",
                side_effect=mutate_then_delete,
            ),
            self.assertRaisesRegex(SkillMagnetError, "preserved for recovery"),
        ):
            _cleanup_windows_native_build_workspace(workspace)
        isolated = next(created.parent.glob(created.name + ".cleanup-*"))
        self.assertEqual((isolated / "out" / artifact.name).read_bytes(), b"other")

    def test_native_workspace_cleanup_rejects_unproduced_allowlisted_name(self) -> None:
        from skill_magnet.platforms import (
            _capture_windows_native_cleanup_identity,
            _create_windows_native_build_workspace,
        )

        created = self.root / "workspace-unproduced-output"
        with mock.patch(
            "skill_magnet.platforms.tempfile.mkdtemp",
            side_effect=lambda **_: (created.mkdir(), str(created))[1],
        ):
            workspace = _create_windows_native_build_workspace(self.root)
        output = Path(workspace["output"])
        launcher = output / "SkillMagnetLauncher.exe"
        launcher.write_bytes(b"not produced by build.ps1")
        with self.assertRaisesRegex(SafetyError, "unowned entries"):
            _capture_windows_native_cleanup_identity(workspace)
        self.assertEqual(launcher.read_bytes(), b"not produced by build.ps1")

    def test_native_source_manifest_rejects_duplicate_keys(self) -> None:
        from skill_magnet.platforms import _windows_native_build_binding

        native_root = self._native_output.parent
        manifest = self._native_output / "SkillMagnetNativeSource.json"
        original = manifest.read_text(encoding="utf-8")
        try:
            for duplicate in (
                original.replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1),
                original.replace('"path":"SkillMagnetCommand.dll"', '"path":"SkillMagnetCommand.dll","path":"SkillMagnetCommand.dll"', 1),
            ):
                with self.subTest(duplicate=duplicate[:80]):
                    manifest.write_text(duplicate, encoding="utf-8")
                    binding = _windows_native_build_binding(
                        native_root, self._native_output
                    )
                    self.assertFalse(binding["native_source_manifest_valid"])
                    self.assertFalse(binding["native_build_binding_valid"])
        finally:
            manifest.write_text(original, encoding="utf-8")

    def test_all_windows_context_mutators_share_one_lock(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def held_install(*_: object, **__: object) -> dict[str, object]:
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release held install")
            return {"installed": True}

        def first() -> None:
            try:
                install_windows_context_menus(self.config_path)
            except BaseException as exc:
                failures.append(exc)

        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch(
                "skill_magnet.platforms._install_windows_context_menus_unlocked",
                side_effect=held_install,
            ),
            mock.patch(
                "skill_magnet.platforms._rollback_windows_context_menus_unlocked",
                return_value={"rolled_back": True},
            ) as rollback_body,
            mock.patch(
                "skill_magnet.platforms._uninstall_windows_context_menus_unlocked",
                return_value={"removed": True},
            ) as uninstall_body,
        ):
            worker = threading.Thread(target=first)
            worker.start()
            self.assertTrue(entered.wait(10))
            for operation in (rollback_windows_context_menus, uninstall_windows_context_menus):
                with self.assertRaisesRegex(SkillMagnetError, "operation is active"):
                    operation()
            self.assertEqual(rollback_body.call_count, 0)
            self.assertEqual(uninstall_body.call_count, 0)
            release.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(rollback_windows_context_menus(), {"rolled_back": True})

    def test_windows_context_mutation_lock_releases_after_failure(self) -> None:
        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch(
                "skill_magnet.platforms._install_windows_context_menus_unlocked",
                side_effect=[SkillMagnetError("failed"), {"installed": True}],
            ),
        ):
            with self.assertRaisesRegex(SkillMagnetError, "failed"):
                install_windows_context_menus(self.config_path)
            self.assertEqual(
                install_windows_context_menus(self.config_path), {"installed": True}
            )

    def test_windows_context_menu_install_returns_native_recovery_receipt(self) -> None:
        root = self.root / "combined-recovery-receipt"
        receipt = {
            "usable_installed_state": True,
            "packaged_native_build_residue_removed": False,
            "packaged_native_build_residue_preserved": True,
            "packaged_native_build_recovery_id": "a" * 32,
            "packaged_native_build_recovery_directory": str(
                self.root / ".skill-magnet-native-recovery"
            ),
            "legacy_certificate_thumbprints_removed": ["B" * 40],
        }
        readback = {"usable_installed_state": True}
        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            mock.patch(
                "skill_magnet.platforms._recover_windows_rollback_rotation",
                return_value=False,
            ),
            mock.patch(
                "skill_magnet.platforms._recover_windows_certificate_ownership_from_residue",
                return_value=False,
            ),
            mock.patch(
                "skill_magnet.platforms._cleanup_windows_context_residue",
                return_value=[],
            ),
            mock.patch(
                "skill_magnet.platforms._capture_windows_context_backup",
                return_value={"package_installed": False},
            ),
            mock.patch(
                "skill_magnet.platforms.install_windows_modern_context_menu",
                return_value=receipt,
            ),
            mock.patch("skill_magnet.platforms.uninstall_context_menu"),
            mock.patch(
                "skill_magnet.platforms._windows_owned_registry_roots_present",
                return_value=[],
            ),
            mock.patch(
                "skill_magnet.platforms.windows_modern_context_menu_status",
                return_value=readback,
            ),
        ):
            result = install_windows_context_menus(
                self.config_path, install_root=root, build=True
            )
        for field in (
            "packaged_native_build_residue_removed",
            "packaged_native_build_residue_preserved",
            "packaged_native_build_recovery_id",
            "packaged_native_build_recovery_directory",
            "legacy_certificate_thumbprints_removed",
        ):
            self.assertEqual(result["modern"][field], receipt[field])

    def test_outer_context_install_post_modern_failure_reports_native_quarantine(
        self,
    ) -> None:
        recovery = self.root / ".skill-magnet-native-recovery"
        receipt = {
            "usable_installed_state": True,
            "packaged_native_build_residue_preserved": True,
            "packaged_native_build_recovery_id": "c" * 32,
            "packaged_native_build_recovery_directory": str(recovery),
        }
        for stage in ("classic-cleanup", "final-readback", "rotation"):
            with self.subTest(stage=stage):
                root = self.root / stage / "ContextMenu"
                if stage == "rotation":
                    root.with_name(root.name + ".rollback").mkdir(parents=True)
                classic_effect = (
                    SkillMagnetError("classic cleanup failed")
                    if stage == "classic-cleanup"
                    else None
                )
                status_effect = (
                    SkillMagnetError("final readback failed")
                    if stage == "final-readback"
                    else None
                )
                rotation_effect = (
                    SkillMagnetError("rotation failed")
                    if stage == "rotation"
                    else None
                )
                with (
                    mock.patch("skill_magnet.platforms.os.name", "nt"),
                    mock.patch(
                        "skill_magnet.platforms._recover_windows_rollback_rotation",
                        return_value=False,
                    ),
                    mock.patch(
                        "skill_magnet.platforms._recover_windows_certificate_ownership_from_residue",
                        return_value=False,
                    ),
                    mock.patch(
                        "skill_magnet.platforms._cleanup_windows_context_residue",
                        return_value=[],
                    ),
                    mock.patch(
                        "skill_magnet.platforms._capture_windows_context_backup",
                        return_value={"package_installed": False},
                    ),
                    mock.patch(
                        "skill_magnet.platforms.install_windows_modern_context_menu",
                        return_value=receipt,
                    ),
                    mock.patch(
                        "skill_magnet.platforms.uninstall_context_menu",
                        side_effect=classic_effect,
                    ),
                    mock.patch(
                        "skill_magnet.platforms._windows_owned_registry_roots_present",
                        return_value=[],
                    ),
                    mock.patch(
                        "skill_magnet.platforms.windows_modern_context_menu_status",
                        return_value={"usable_installed_state": True},
                        side_effect=status_effect,
                    ),
                    mock.patch(
                        "skill_magnet.platforms._rotate_windows_context_backup",
                        side_effect=rotation_effect,
                    ),
                    self.assertRaisesRegex(
                        SkillMagnetError,
                        re.escape(str(recovery)) + ".*" + "c" * 32,
                    ),
                ):
                    install_windows_context_menus(
                        self.config_path, install_root=root, build=True
                    )

    def test_windows_modern_install_removes_deprecated_blocked_launcher(self) -> None:
        root = self.root / "remove-blocked-launcher"
        root.mkdir()
        blocked = root / "SkillMagnetLauncher.exe"
        blocked.write_bytes(b"deprecated self-signed adapter")

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            action = args[args.index("-Action") + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": action != "uninstall",
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            result = install_windows_modern_context_menu(
                self.config_path, install_root=root, run=fake_run, build=False
            )
        self.assertTrue(result["usable_installed_state"])
        self.assertFalse(blocked.exists())
        self.assertFalse(result["deprecated_launcher_exists"])

    def test_windows_modern_operations_preserve_symlinked_install_targets(self) -> None:
        target = self.root / "foreign-windows-context-target"
        target.mkdir()
        sentinel = target / "do-not-delete.txt"
        sentinel.write_text("foreign data", encoding="utf-8")
        link = self.root / "redirected-context-root"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            junction = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
            )
            if junction.returncode != 0 or not link.is_junction():
                self.skipTest(
                    "directory symlink and junction are unavailable: "
                    + str(exc)
                    + " / "
                    + (junction.stderr or junction.stdout).strip()
                )

        for install_root in (link, link / "nested" / "ContextMenu"):
            with self.subTest(install_root=install_root):
                calls: list[list[str]] = []

                def fake_run(args: list[str], **_: object) -> SimpleNamespace:
                    calls.append(args)
                    return SimpleNamespace(returncode=0, stdout="{}", stderr="")

                with (
                    mock.patch("skill_magnet.platforms.os.name", "nt"),
                    self.assertRaisesRegex(SafetyError, "symlink or junction"),
                ):
                    uninstall_windows_modern_context_menu(
                        install_root=install_root, run=fake_run
                    )
                self.assertEqual(calls, [])
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "foreign data")

    def test_windows_backup_and_uninstall_preserve_nested_junction_target(self) -> None:
        root = self.root / "nested-junction-context-root"
        root.mkdir()
        target = self.root / "foreign-nested-junction-target"
        target.mkdir()
        sentinel = target / "do-not-delete.txt"
        sentinel.write_text("foreign nested data", encoding="utf-8")
        junction = root / "Assets"
        try:
            junction.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            created = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(junction),
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            if created.returncode != 0 or not junction.is_junction():
                self.skipTest(
                    "directory symlink and junction are unavailable: "
                    + str(exc)
                    + " / "
                    + (created.stderr or created.stdout).strip()
                )

        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        backup = root.with_name(root.name + ".rollback")
        with self.assertRaisesRegex(SafetyError, "symlink or junction"):
            _capture_windows_context_backup(
                backup, install_root=root, run=fake_run
            )
        with (
            mock.patch("skill_magnet.platforms.os.name", "nt"),
            self.assertRaisesRegex(SafetyError, "symlink or junction"),
        ):
            uninstall_windows_modern_context_menu(
                install_root=root, run=fake_run
            )
        self.assertEqual(calls, [])
        self.assertFalse(backup.exists())
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"), "foreign nested data"
        )

        if junction.is_symlink():
            junction.unlink()
        else:
            junction.rmdir()

    @unittest.skipUnless(sys.platform == "win32", "Windows certificate provider required")
    def test_windows_certificate_cleanup_preserves_state_when_owner_is_missing(self) -> None:
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
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all certificates were preserved", result.stderr)
        self.assertTrue((external / "certificate-state.json").is_file())

    @unittest.skipUnless(sys.platform == "win32", "Windows certificate provider required")
    def test_windows_certificate_cleanup_preserves_malformed_state(self) -> None:
        external = self.root / "malformed-certificate-cleanup"
        external.mkdir()
        state = external / "certificate-state.json"
        state.write_bytes(b"{not-json")
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

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all certificates were preserved", result.stderr)
        self.assertEqual(state.read_bytes(), b"{not-json")

    def test_windows_combined_install_and_rollback_restore_classic_and_package_state(self) -> None:
        root = self.root / "combined-modern"
        registry = {
            r"HKCU\Software\Classes\Directory\shell\SkillMagnet": True,
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet": True,
            r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic": False,
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic": False,
        }
        package_installed = False

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal package_installed
            if args[0] == "reg":
                action, target = args[1], args[2]
                if action == "query":
                    present = bool(registry.get(target))
                    return SimpleNamespace(
                        returncode=0 if present else 1,
                        stdout="",
                        stderr="" if present else "__REGISTRY_KEY_NOT_FOUND__",
                    )
                if action == "export":
                    Path(args[3]).write_text(target, encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "delete":
                    registry[target] = False
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "add":
                    raise AssertionError("modern install must not create classic keys")
                if action == "import":
                    registry[Path(target).read_text(encoding="utf-8")] = True
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            action = args[args.index("-Action") + 1]
            if action == "install":
                package_installed = True
            elif action == "uninstall":
                package_installed = False
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": package_installed,
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
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
        self.assertTrue(
            registry[r"HKCU\Software\Classes\Directory\shell\SkillMagnet"]
        )
        self.assertTrue(
            registry[
                r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet"
            ]
        )
        self.assertFalse(
            registry.get(
                r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic", False
            )
        )
        self.assertFalse(
            registry.get(
                r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
                False,
            )
        )
        self.assertFalse(root.exists())
        self.assertFalse(root.with_name(root.name + ".rollback").exists())

    def test_windows_install_removes_only_valid_owned_transaction_residue(self) -> None:
        from skill_magnet.platforms import _cleanup_windows_context_residue

        root = self.root / "ContextMenu"
        valid = root.with_name("ContextMenu.rollback.interrupted-20260829-0929")
        valid.mkdir()
        (valid / "backup.json").write_text(
            json.dumps({"version": 2}), encoding="utf-8"
        )
        unrelated = root.with_name("ContextMenu.rollback.interrupted-not-a-timestamp")
        unrelated.mkdir()
        removed = _cleanup_windows_context_residue(root)
        self.assertEqual(removed, [valid.name])
        self.assertFalse(valid.exists())
        self.assertTrue(unrelated.exists())

    def test_windows_recovers_bom_certificate_ownership_before_residue_cleanup(self) -> None:
        from skill_magnet.platforms import (
            _cleanup_windows_context_residue,
            _recover_windows_certificate_ownership_from_residue,
        )

        root = self.root / "ContextMenu"
        root.mkdir()
        thumbprint = "A" * 40
        (root / "certificate-state.json").write_text(
            json.dumps({
                "thumbprint": thumbprint,
                "created_my": False,
                "created_trusted_people": False,
                "created_machine_trusted_people": False,
            }), encoding="utf-8-sig")
        residue = root.with_name("ContextMenu.rollback.interrupted-20260829-0929")
        (residue / "external").mkdir(parents=True)
        (residue / "backup.json").write_text(json.dumps({"version": 2}), encoding="utf-8")
        (residue / "external" / "certificate-state.json").write_text(
            json.dumps({
                "thumbprint": thumbprint,
                "created_my": True,
                "created_trusted_people": True,
                "created_machine_trusted_people": True,
            }), encoding="utf-8-sig")
        self.assertTrue(_recover_windows_certificate_ownership_from_residue(root))
        recovered = json.loads((root / "certificate-state.json").read_text(encoding="utf-8"))
        self.assertTrue(all(recovered[flag] for flag in (
            "created_my", "created_trusted_people", "created_machine_trusted_people")))
        self.assertEqual(_cleanup_windows_context_residue(root), [residue.name])

    def test_windows_residue_cleanup_fails_closed_on_invalid_metadata(self) -> None:
        from skill_magnet.platforms import _cleanup_windows_context_residue

        root = self.root / "ContextMenu"
        invalid = root.with_name("ContextMenu.rollback.recovered-20260829-093300")
        invalid.mkdir()
        (invalid / "backup.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "Unsupported"):
            _cleanup_windows_context_residue(root)
        self.assertTrue(invalid.exists())

    def test_windows_update_rollback_restores_immediately_previous_install(self) -> None:
        root = self.root / "combined-repeatable-modern"
        registry = {
            r"HKCU\Software\Classes\Directory\shell\SkillMagnet": True,
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet": True,
            r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic": False,
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic": False,
        }
        package_installed = False
        fail_next_install = False

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal fail_next_install, package_installed
            if args[0] == "reg":
                action, target = args[1], args[2]
                if action == "query":
                    present = bool(registry.get(target))
                    return SimpleNamespace(
                        returncode=0 if present else 1,
                        stdout="",
                        stderr="" if present else "__REGISTRY_KEY_NOT_FOUND__",
                    )
                if action == "export":
                    Path(args[3]).write_text(target, encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "delete":
                    registry[target] = False
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "import":
                    registry[Path(target).read_text(encoding="utf-8")] = True
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if action == "add":
                    raise AssertionError("modern install must not create classic keys")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            action = args[args.index("-Action") + 1]
            if action == "install":
                if fail_next_install:
                    fail_next_install = False
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="injected install failure"
                    )
                package_installed = True
            elif action == "uninstall":
                package_installed = False
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": package_installed,
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.9.0",
                        "architecture": "X64",
                        "publisher": "CN=Skill Magnet Local",
                        "install_location": str(root),
                    }
                ),
                stderr="",
            )

        rollback_root = root.with_name(root.name + ".rollback")
        update_root = rollback_root.with_name(rollback_root.name + ".update")
        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            first = install_windows_context_menus(
                self.config_path, install_root=root, run=fake_run, build=False
            )
            self.assertTrue(first["modern"]["usable_installed_state"])
            self.assertTrue(package_installed)
            self.assertTrue(rollback_root.is_dir())
            self.assertFalse(any(registry.values()))

            second = install_windows_context_menus(
                self.config_path, install_root=root, run=fake_run, build=False
            )
            self.assertTrue(second["modern"]["usable_installed_state"])
            self.assertTrue(package_installed)
            self.assertTrue(rollback_root.is_dir())
            self.assertFalse(update_root.exists())
            self.assertFalse(any(registry.values()))

            fail_next_install = True
            with self.assertRaisesRegex(
                Exception, "no policy-incompatible classic fallback"
            ):
                install_windows_context_menus(
                    self.config_path, install_root=root, run=fake_run, build=False
                )
            # A failed update restores the immediately preceding usable modern
            # state. It must not expose the blocked self-signed classic path.
            self.assertTrue(package_installed)
            self.assertTrue(rollback_root.is_dir())
            self.assertFalse(update_root.exists())
            self.assertFalse(
                registry[r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic"]
            )
            self.assertFalse(
                registry[
                    r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic"
                ]
            )
            self.assertFalse(
                registry[r"HKCU\Software\Classes\Directory\shell\SkillMagnet"]
            )
            self.assertFalse(
                registry[r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet"]
            )

            rollback_windows_context_menus(install_root=root, run=fake_run)
        self.assertTrue(package_installed)
        self.assertFalse(
            registry[r"HKCU\Software\Classes\Directory\shell\SkillMagnet"]
        )
        self.assertFalse(
            registry[
                r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnet"
            ]
        )
        self.assertFalse(
            registry.get(
                r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic", False
            )
        )
        self.assertFalse(
            registry.get(
                r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
                False,
            )
        )
        self.assertTrue(root.exists())
        self.assertFalse(rollback_root.exists())

    def test_windows_rollback_rotation_recovers_after_abrupt_stop(self) -> None:
        backup = self.root / "ContextMenu.rollback"
        update = self.root / "ContextMenu.rollback.update"
        for candidate in (backup, update):
            candidate.mkdir()
            (candidate / "backup.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "registry_roots": [],
                        "registry_sha256": [],
                        "package_installed": False,
                        "owned_packages": [],
                        "external_existed": False,
                        "external_manifest": {},
                    }
                ),
                encoding="utf-8",
            )
        real_replace = os.replace
        calls = 0

        def interrupted_replace(source: object, destination: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise KeyboardInterrupt("simulated power loss during promotion")
            real_replace(source, destination)

        with (
            mock.patch("skill_magnet.platforms.os.replace", side_effect=interrupted_replace),
            self.assertRaises(KeyboardInterrupt),
        ):
            _rotate_windows_context_backup(backup, update)
        self.assertFalse(backup.exists())
        self.assertTrue(update.exists())
        self.assertTrue(backup.with_name(backup.name + ".rotation-old").exists())
        self.assertTrue(_recover_windows_rollback_rotation(backup))
        self.assertTrue(backup.is_dir())
        self.assertFalse(update.exists())
        self.assertFalse(backup.with_name(backup.name + ".rotation-old").exists())
        self.assertFalse(backup.with_name(backup.name + ".rotation.json").exists())

    def test_windows_backup_restores_owned_previous_version_identity(self) -> None:
        root = self.root / "previous-version-context"
        root.mkdir()
        (root / "SkillMagnet.ContextMenu.msix").write_bytes(b"previous-msix")
        (root / "previous-static.bin").write_bytes(b"previous-content")
        package_registered = True
        previous_identity = {
            "name": "SkillMagnet.ContextMenu",
            "version": "0.5.8.0",
            "architecture": "X64",
            "publisher": "CN=Skill Magnet Local",
            "package_full_name": "SkillMagnet.ContextMenu_0.5.8.0_x64__previous",
        }

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            nonlocal package_registered
            if args[0] == "reg":
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="__REGISTRY_KEY_NOT_FOUND__",
                )
            action = args[args.index("-Action") + 1]
            if action == "uninstall":
                package_registered = False
            elif action == "install":
                package_registered = True
            packages = [previous_identity] if package_registered else []
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "installed": False,
                        "name": "SkillMagnet.ContextMenu",
                        "same_name_package_count": len(packages),
                        "expected_identity_match_count": 0,
                        "unexpected_same_name_package_count": len(packages),
                        "same_name_packages": packages,
                    }
                ),
                stderr="",
            )

        backup = root.with_name(root.name + ".rollback")
        with mock.patch(
            "skill_magnet.platforms.windows_modern_context_menu_status",
            return_value={
                "installed": False,
                "same_name_packages": [previous_identity],
            },
        ):
            metadata = _capture_windows_context_backup(
                backup, install_root=root, run=fake_run
            )
        self.assertTrue(metadata["package_installed"])
        self.assertEqual(metadata["owned_packages"], [previous_identity])
        (root / "previous-static.bin").write_bytes(b"changed")
        _restore_windows_context_backup(backup, install_root=root, run=fake_run)
        self.assertTrue(package_registered)
        self.assertEqual((root / "previous-static.bin").read_bytes(), b"previous-content")

    def test_windows_restore_validates_complete_backup_before_destructive_calls(self) -> None:
        root_count = len(_windows_owned_menu_roots())
        digest = hashlib.sha256(b"backup payload").hexdigest()
        base = {
            "version": 3,
            "registry_roots": [False] * root_count,
            "registry_sha256": [None] * root_count,
            "package_installed": False,
            "owned_packages": [],
            "external_existed": False,
            "external_manifest": {},
        }
        corruptions = {
            "incomplete_schema": {"version": 3},
            "non_boolean_registry_flag": {
                **base,
                "registry_roots": [False] * (root_count - 1) + [0],
            },
            "escaping_manifest_path": {
                **base,
                "external_existed": True,
                "external_manifest": {"../payload.bin": digest},
            },
            "wrong_external_digest": {
                **base,
                "external_existed": True,
                "external_manifest": {"payload.bin": "0" * 64},
            },
            "unowned_package_identity": {
                **base,
                "package_installed": True,
                "owned_packages": [
                    {
                        "name": "SkillMagnet.ContextMenu",
                        "version": "0.5.8.0",
                        "architecture": "X64",
                        "publisher": "CN=Unrelated",
                        "package_full_name": "foreign-package",
                    }
                ],
            },
        }
        for label, metadata in corruptions.items():
            with self.subTest(label=label):
                root = self.root / f"rollback-current-{label}"
                root.mkdir()
                (root / "AppxManifest.xml").write_bytes(b"current appx")
                (root / "current-sentinel.bin").write_bytes(b"current bytes")
                backup = root.with_name(root.name + ".rollback")
                backup.mkdir()
                if metadata.get("external_existed"):
                    external = backup / "external"
                    external.mkdir()
                    (external / "payload.bin").write_bytes(b"backup payload")
                (backup / "backup.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                before = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                calls: list[list[str]] = []

                def fake_run(args: list[str], **_: object) -> SimpleNamespace:
                    calls.append(args)
                    return SimpleNamespace(returncode=0, stdout="{}", stderr="")

                with self.assertRaisesRegex(SkillMagnetError, "rollback"):
                    _restore_windows_context_backup(
                        backup, install_root=root, run=fake_run
                    )
                after = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(calls, [])
                self.assertEqual(after, before)
                self.assertTrue(backup.is_dir())

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

    def test_modern_menu_does_not_use_selected_project_as_process_cwd(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "windows-modern-context-menu"
            / "SkillMagnetCommand.cpp"
        ).read_text(encoding="utf-8")
        create_process = source[source.index("if (!CreateProcessW"):]
        create_process = create_process[: create_process.index("&startup, &process)")]
        self.assertNotIn("project.c_str()", create_process)
        self.assertIn(
            "CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, nullptr,\n"
            "                            nullptr,",
            create_process,
        )

    def test_confirmation_ui_treats_index_as_optional_and_requires_application(self) -> None:
        message = context_ui_text("ja", "verification")
        self.assertIn("存在する場合のINDEX関係", message)
        self.assertIn("実作業へ適用", message)
    def test_windows_classic_installer_rejection_precedes_config_and_runner(self) -> None:
        missing_config = self.root / "does-not-exist.json"
        runner = mock.Mock(name="registry_runner")
        with self.assertRaisesRegex(
            SkillMagnetError, "classic context-menu registration is disabled"
        ):
            install_context_menu("windows", missing_config, run=runner)
        runner.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_windows_uninstall_removes_only_owned_subtrees(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            calls.append(args)
            if args[:2] == ["reg", "query"]:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__"
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            result = uninstall_context_menu("windows", run=fake_run)
        self.assertTrue(result["removed"])
        self.assertEqual(
            [call for call in calls if call[:2] == ["reg", "delete"]],
            [
                [
                    "reg",
                    "delete",
                    r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic",
                    "/f",
                ],
                [
                    "reg",
                    "delete",
                    r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic",
                    "/f",
                ],
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

    def test_windows_uninstall_fails_closed_when_registry_root_remains(self) -> None:
        residual = r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic"

        def fake_run(args: list[str], **_: object) -> SimpleNamespace:
            if args[:2] == ["reg", "delete"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="Access is denied")
            if args[:2] == ["reg", "query"]:
                present = args[2] == residual
                return SimpleNamespace(
                    returncode=0 if present else 1,
                    stdout="",
                    stderr="" if present else "__REGISTRY_KEY_NOT_FOUND__",
                )
            raise AssertionError(args)

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            with self.assertRaisesRegex(Exception, "remain after removal"):
                uninstall_context_menu("windows", run=fake_run)
        self.assertFalse(self.state.exists())

    def test_windows_classic_reinstall_is_blocked_and_uninstall_preserves_neighbors(self) -> None:
        special_config = self.root / "config 空白 日本語 & ( ) ' ! ^ # %.json"
        special_config.write_bytes(self.config_path.read_bytes())
        original_config = special_config.read_bytes()
        directory = r"HKCU\Software\Classes\Directory\shell\SkillMagnetClassic"
        background = (
            r"HKCU\Software\Classes\Directory\Background\shell\SkillMagnetClassic"
        )
        legacy_directory = r"HKCU\Software\Classes\Directory\shell\SkillMagnet"
        legacy_background = (
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
            legacy_directory,
            legacy_directory + r"\shell\stale-pack",
            legacy_background,
            legacy_background + r"\shell\stale-pack",
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
                raise AssertionError("classic registration must remain unreachable")
            if args[:2] == ["reg", "query"]:
                present = target in registry
                return SimpleNamespace(
                    returncode=0 if present else 1,
                    stdout="",
                    stderr="" if present else "__REGISTRY_KEY_NOT_FOUND__",
                )
            raise AssertionError(args)

        before_rejected_install = set(registry)
        with self.assertRaisesRegex(
            SkillMagnetError, "classic context-menu registration is disabled"
        ):
            install_context_menu("windows", special_config, run=fake_run)
        self.assertEqual(registry, before_rejected_install)
        self.assertTrue(protected.issubset(registry))
        self.assertEqual(special_config.read_bytes(), original_config)

        with mock.patch("skill_magnet.platforms.os.name", "nt"):
            uninstall_context_menu("windows", run=fake_run)
        self.assertEqual(registry, protected)
        self.assertEqual(special_config.read_bytes(), original_config)

    def test_macos_installer_creates_and_removes_finder_quick_action(self) -> None:
        services = self.root / "Library" / "Services"
        probe = self.root / "finder probe.txt"
        with mock.patch.dict(
            os.environ, {"SKILL_MAGNET_FINDER_E2E_PROBE": str(probe)}
        ):
            result = install_context_menu(
                "macos", self.config_path, services_dir=services
            )
        workflow = services / "Skill Magnet.workflow" / "Contents" / "document.wflow"
        self.assertTrue(result["installed"])
        self.assertTrue(workflow.is_file())
        status = finder_context_menu_status(
            config=self.config_path, services_dir=services
        )
        self.assertTrue(status["usable_installed_state"])
        self.assertTrue(status["workflow_contract_valid"])
        self.assertTrue(status["workflow_contract_matches_config"])
        self.assertFalse(status["release_probe_present"])
        self.assertEqual(status["transaction_residue"], [])
        workflow_bytes = workflow.read_bytes()
        self.assertIn(b"com.apple.RunShellScript", workflow_bytes)
        self.assertNotIn(b"finder probe.txt", workflow_bytes)
        workflow_document = plistlib.loads(workflow_bytes)
        action = workflow_document["actions"][0]["action"]
        self.assertIn("ActionParameters", action)
        self.assertNotIn("parameters", action)
        production_command = action["ActionParameters"]["COMMAND_STRING"]
        self.assertIn("--launcher", production_command)
        self.assertIn("--finder-selection-count", production_command)
        self.assertNotIn("--release-probe", production_command)
        self.assertNotIn("printf", production_command)
        self.assertNotIn("exit 0", production_command)
        selected = self.root / "selected by Finder"
        selected.mkdir()
        self.assertEqual(
            cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "context",
                    "--platform",
                    "macos",
                    "--project",
                    str(selected),
                    "--release-probe",
                    str(probe),
                ]
            ),
            0,
        )
        probe_record = json.loads(probe.read_text(encoding="utf-8"))
        self.assertEqual(probe_record["adapter"], "macos_finder_quick_action")
        self.assertEqual(probe_record["selected_path"], str(selected.resolve()))
        self.assertEqual(probe_record["pack_id"], "bounded-pack")
        self.assertEqual(probe_record["selection_kind"], "pack")
        self.assertEqual(probe_record["runtime"], "codex")
        self.assertEqual(probe_record["status"], "desktop_handoff_ready")
        self.assertEqual(
            probe_record["result_verification"], "not_claimed_by_design"
        )
        self.assertTrue(probe_record["handoff_completed"])
        self.assertFalse(probe_record["answer_completion_claimed"])
        self.assertEqual(
            probe_record["billing_boundary"], "existing_plan_no_api_key"
        )
        self.assertNotIn("verified_completed", probe_record)
        self.assertEqual(
            probe_record["delivery"]["project"], str(selected.resolve())
        )
        self.assertEqual(
            probe_record["delivery"]["destination"], "codex://threads/new"
        )
        self.assertTrue(probe_record["delivery"]["prompt_present"])
        self.assertEqual(probe_record["skill_ids"], ["bounded-answer"])
        self.assertTrue(probe_record["instruction_digest"])
        self.assertTrue(probe_record["index_digest"])
        claude_probe = self.root / "finder claude probe.json"
        self.assertEqual(
            cli_main(
                [
                    "--config",
                    str(self.config_path),
                    "context",
                    "--platform",
                    "macos",
                    "--project",
                    str(selected),
                    "--release-probe",
                    str(claude_probe),
                    "--release-probe-runtime",
                    "claude",
                ]
            ),
            0,
        )
        claude_record = json.loads(claude_probe.read_text(encoding="utf-8"))
        self.assertEqual(claude_record["runtime"], "claude")
        self.assertEqual(claude_record["status"], "desktop_handoff_prepared")
        self.assertEqual(
            claude_record["delivery"]["destination"], "claude://code/new"
        )
        self.assertTrue(claude_record["delivery"]["prompt_present"])
        removed = uninstall_context_menu("macos", services_dir=services)
        self.assertTrue(removed["removed"])
        self.assertFalse(workflow.parent.parent.exists())
        self.assertFalse(
            finder_context_menu_status(services_dir=services)[
                "usable_installed_state"
            ]
        )

    def test_macos_workflow_update_is_idempotent_and_recovers_interrupted_swap(self) -> None:
        services = self.root / "update-services"
        first = install_context_menu(
            "macos", self.config_path, services_dir=services
        )
        self.assertFalse(first["updated"])
        workflow_root = services / "Skill Magnet.workflow"
        document = workflow_root / "Contents" / "document.wflow"
        original = document.read_bytes()

        unchanged = install_context_menu(
            "macos",
            self.config_path,
            services_dir=services,
            replace_existing=True,
        )
        self.assertTrue(unchanged["unchanged"])
        self.assertFalse(unchanged["updated"])
        self.assertEqual(document.read_bytes(), original)

        alternate_config = self.root / "crash config" / "skill-magnet.json"
        alternate_config.parent.mkdir()
        alternate_config.write_bytes(self.config_path.read_bytes())
        real_replace = os.replace

        def crash_before_candidate_swap(source: object, destination: object) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                source_path.name.startswith(".skill-magnet-workflow-stage-")
                and destination_path == workflow_root
            ):
                raise SystemExit("simulated process termination")
            real_replace(source, destination)

        with (
            self.assertRaisesRegex(SystemExit, "simulated process termination"),
            mock.patch(
                "skill_magnet.platforms.os.replace",
                side_effect=crash_before_candidate_swap,
            ),
        ):
            install_context_menu(
                "macos",
                alternate_config,
                services_dir=services,
                replace_existing=True,
            )
        self.assertFalse(workflow_root.exists())
        self.assertEqual(
            len(list(services.glob(".skill-magnet-workflow-backup-*"))), 1
        )
        self.assertEqual(
            len(list(services.glob(".skill-magnet-workflow-stage-*"))), 1
        )
        recovered = install_context_menu(
            "macos",
            self.config_path,
            services_dir=services,
            replace_existing=True,
        )
        self.assertTrue(recovered["recovered_transaction"])
        self.assertTrue(recovered["unchanged"])
        self.assertEqual(document.read_bytes(), original)
        self.assertEqual(
            finder_context_menu_status(services_dir=services)["transaction_residue"],
            [],
        )

    def test_macos_workflow_update_restores_previous_bytes_when_swap_fails(self) -> None:
        services = self.root / "failed-update-services"
        install_context_menu("macos", self.config_path, services_dir=services)
        workflow_root = services / "Skill Magnet.workflow"
        document = workflow_root / "Contents" / "document.wflow"
        original = document.read_bytes()
        alternate_config = self.root / "alternate config" / "skill-magnet.json"
        alternate_config.parent.mkdir()
        alternate_config.write_bytes(self.config_path.read_bytes())
        real_replace = os.replace

        def fail_candidate_swap(source: object, destination: object) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                source_path.name.startswith(".skill-magnet-workflow-stage-")
                and destination_path == workflow_root
            ):
                raise OSError("injected Finder candidate swap failure")
            real_replace(source, destination)

        with (
            self.assertRaisesRegex(OSError, "candidate swap failure"),
            mock.patch(
                "skill_magnet.platforms.os.replace", side_effect=fail_candidate_swap
            ),
        ):
            install_context_menu(
                "macos",
                alternate_config,
                services_dir=services,
                replace_existing=True,
            )

        self.assertEqual(document.read_bytes(), original)
        self.assertEqual(
            finder_context_menu_status(services_dir=services)["transaction_residue"],
            [],
        )
        updated = install_context_menu(
            "macos",
            alternate_config,
            services_dir=services,
            replace_existing=True,
        )
        self.assertTrue(updated["updated"])
        self.assertFalse(updated["unchanged"])
        self.assertNotEqual(document.read_bytes(), original)

    def test_macos_status_rejects_tampered_workflow_and_transaction_residue(self) -> None:
        services = self.root / "tampered-services"
        install_context_menu("macos", self.config_path, services_dir=services)
        workflow = services / "Skill Magnet.workflow" / "Contents" / "document.wflow"
        document = plistlib.loads(workflow.read_bytes())
        document["workflowMetaData"]["serviceApplicationBundleID"] = "com.example.other"
        workflow.write_bytes(plistlib.dumps(document))
        residue = services / ".skill-magnet-workflow-leftover"
        residue.mkdir()
        status = finder_context_menu_status(services_dir=services)
        self.assertTrue(status["installed"])
        self.assertFalse(status["workflow_contract_valid"])
        self.assertEqual(status["transaction_residue"], [str(residue)])
        self.assertFalse(status["usable_installed_state"])

    def test_macos_recovery_preserves_foreign_residue_without_journal(self) -> None:
        services = self.root / "foreign-residue-services"
        services.mkdir()
        first = services / ".skill-magnet-workflow-backup-foreign"
        second = services / ".skill-magnet-workflow-backup-other"
        first.mkdir()
        second.mkdir()
        first_sentinel = first / "do-not-delete.txt"
        second_sentinel = second / "do-not-delete.txt"
        first_sentinel.write_text("foreign-one", encoding="utf-8")
        second_sentinel.write_text("foreign-two", encoding="utf-8")

        with self.assertRaisesRegex(SafetyError, "transaction journal"):
            install_context_menu(
                "macos", self.config_path, services_dir=services
            )

        self.assertEqual(first_sentinel.read_text(encoding="utf-8"), "foreign-one")
        self.assertEqual(second_sentinel.read_text(encoding="utf-8"), "foreign-two")
        self.assertFalse((services / "Skill Magnet.workflow").exists())

    def test_macos_recovery_preserves_corrupt_journal(self) -> None:
        services = self.root / "corrupt-journal-services"
        services.mkdir()
        journal = services / ".skill-magnet-workflow-transaction.json"
        journal.write_text("{not-json", encoding="utf-8")

        with self.assertRaisesRegex(SafetyError, "journal"):
            install_context_menu(
                "macos", self.config_path, services_dir=services
            )

        self.assertEqual(journal.read_text(encoding="utf-8"), "{not-json")
        self.assertFalse((services / "Skill Magnet.workflow").exists())

    def test_macos_install_and_uninstall_preserve_unowned_workflow(self) -> None:
        services = self.root / "unowned-workflow-services"
        foreign = services / "Skill Magnet.workflow"
        contents = foreign / "Contents"
        contents.mkdir(parents=True)
        sentinel = contents / "document.wflow"
        sentinel.write_bytes(b"foreign Finder workflow")

        with self.assertRaisesRegex(SafetyError, "所有marker"):
            install_context_menu(
                "macos",
                self.config_path,
                services_dir=services,
                replace_existing=True,
            )
        self.assertEqual(sentinel.read_bytes(), b"foreign Finder workflow")

        with self.assertRaisesRegex(SafetyError, "所有marker"):
            uninstall_context_menu("macos", services_dir=services)
        self.assertEqual(sentinel.read_bytes(), b"foreign Finder workflow")

    def test_macos_tampered_owned_workflow_is_reported_and_preserved(self) -> None:
        services = self.root / "owned-workflow-tamper-services"
        install_context_menu("macos", self.config_path, services_dir=services)
        workflow = services / "Skill Magnet.workflow"
        document = workflow / "Contents" / "document.wflow"
        document.write_bytes(document.read_bytes() + b"tampered")

        status = finder_context_menu_status(services_dir=services)
        self.assertFalse(status["workflow_owned"])
        self.assertFalse(status["usable_installed_state"])
        self.assertIn("digest", status["ownership_error"])

        with self.assertRaisesRegex(SafetyError, "digest"):
            install_context_menu(
                "macos",
                self.config_path,
                services_dir=services,
                replace_existing=True,
            )
        with self.assertRaisesRegex(SafetyError, "digest"):
            uninstall_context_menu("macos", services_dir=services)
        self.assertTrue(workflow.is_dir())
        self.assertTrue(document.read_bytes().endswith(b"tampered"))

    def test_macos_product_workflow_omits_release_probe(self) -> None:
        services = self.root / "product-services"
        with mock.patch.dict(
            os.environ, {"SKILL_MAGNET_FINDER_E2E_PROBE": ""}
        ):
            install_context_menu("macos", self.config_path, services_dir=services)
        workflow = services / "Skill Magnet.workflow" / "Contents" / "document.wflow"
        self.assertNotIn(b"finder probe.txt", workflow.read_bytes())
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
