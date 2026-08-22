from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.activation import ActivationEngine
from skill_magnet.cli import main as cli_main
from skill_magnet.core import Config, SafetyError
from skill_magnet.platforms import (
    context_menu_spec,
    install_context_menu,
    render_registration,
    uninstall_context_menu,
)


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
        self.assertFalse((self.root / "must-not-install-codex").exists())
        self.assertFalse((self.root / "must-not-install-claude").exists())

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
            "not_guaranteed",
        )

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
        self.assertIn("HKEY_CURRENT_USER", render_registration("windows", self.config_path))
        self.assertIn("Finder Quick Action", render_registration("macos", self.config_path))

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
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call[:2] == ["reg", "add"] for call in calls))
        self.assertFalse(self.state.exists())

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
        self.assertEqual(len(deleted), 2)
        self.assertFalse(self.state.exists())

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
