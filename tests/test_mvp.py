from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.core import Config, Engine, SafetyError, hash_directory
import skill_magnet.core as core_module


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


class MvpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "owner-pack"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Skill Magnet Test")
        git(self.repo, "config", "user.email", "skill-magnet@example.invalid")
        git(self.repo, "remote", "add", "origin", "https://github.com/my-owner/my-pack.git")
        self._write_skill("first-skill", "version one")
        self._write_skill("second-skill", "second body")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "initial pack")
        self.config_path = self.root / "config.json"
        self.codex = self.root / "codex"
        self.claude = self.root / "claude"
        self.state = self.root / "state"
        self._write_config()
        self.config = Config.load(self.config_path)
        self.engine = Engine(self.config, self.state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_skill(self, name: str, body: str) -> None:
        directory = self.repo / name
        directory.mkdir(exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test {name}.\n---\n\n{body}\n",
            encoding="utf-8",
        )

    def _write_config(self, owner: str = "my-owner", repo_url: str | None = None) -> None:
        expected_commit = git(self.repo, "rev-parse", "HEAD")
        data = {
            "version": 1,
            "allowed_github_owners": [owner],
            "state_dir": str(self.state),
            "targets": {"codex": str(self.codex), "claude": str(self.claude)},
            "packs": [
                {
                    "id": "my-pack",
                    "repo_url": repo_url or "https://github.com/my-owner/my-pack.git",
                    "expected_commit": expected_commit,
                    "source": str(self.repo),
                    "skills": ["first-skill", "second-skill"],
                }
            ],
        }
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def _assert_no_hidden_transaction_paths(self) -> None:
        for target in (self.codex, self.claude):
            if target.exists():
                self.assertEqual(list(target.glob(".skill-magnet-*")), [])
        self.assertFalse((self.state / "pending-transaction.json").exists())

    def test_runtime_supports_real_windows_junction_detection(self) -> None:
        self.assertGreaterEqual(sys.version_info, (3, 12))
        self.assertTrue(hasattr(Path("."), "is_junction"))

    def test_pack_is_selected_as_one_item(self) -> None:
        pack = self.config.packs["my-pack"]
        self.assertEqual(pack.skills, ("first-skill", "second-skill"))

    def test_only_allowlisted_matching_github_origin_is_accepted(self) -> None:
        self._write_config(owner="someone-else")
        engine = Engine(Config.load(self.config_path), self.state)
        with self.assertRaises(SafetyError):
            engine.plan("my-pack")
        self._write_config(repo_url="https://github.com/my-owner/different.git")
        engine = Engine(Config.load(self.config_path), self.state)
        with self.assertRaises(SafetyError):
            engine.plan("my-pack")

    def test_unpinned_head_is_rejected(self) -> None:
        self._write_skill("first-skill", "new unpinned commit")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "move head")
        with self.assertRaises(SafetyError):
            self.engine.plan("my-pack")

    def test_secret_like_file_is_rejected(self) -> None:
        (self.repo / "first-skill" / ".env").write_text("TOKEN=value", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "unsafe secret file")
        self._write_config()
        engine = Engine(Config.load(self.config_path), self.state)
        with self.assertRaises(SafetyError):
            engine.plan("my-pack")

    def test_secret_content_in_normally_named_file_is_rejected(self) -> None:
        (self.repo / "first-skill" / "notes.txt").write_text(
            "OPENAI_API_KEY=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            encoding="utf-8",
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "unsafe secret content")
        self._write_config()
        engine = Engine(Config.load(self.config_path), self.state)
        with self.assertRaises(SafetyError):
            engine.plan("my-pack")

    def test_symlinked_skill_content_is_rejected(self) -> None:
        link = self.repo / "first-skill" / "linked.txt"
        link.write_text("simulated linked content", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "unsafe link")
        self._write_config()
        engine = Engine(Config.load(self.config_path), self.state)
        original_is_link = __import__("skill_magnet.core", fromlist=["_is_link"])._is_link

        def simulate_link(path: Path) -> bool:
            return path.name == "linked.txt" or original_is_link(path)

        with mock.patch("skill_magnet.core._is_link", side_effect=simulate_link):
            with self.assertRaises(SafetyError):
                engine.plan("my-pack")

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_real_windows_junction_in_skill_is_rejected(self) -> None:
        ignore = self.repo / ".gitignore"
        ignore.write_text("first-skill/linked-junction/\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "ignore audit junction")
        self._write_config()
        outside = self.root / "junction-target"
        outside.mkdir()
        (outside / "outside.txt").write_text("outside", encoding="utf-8")
        junction = self.repo / "first-skill" / "linked-junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        try:
            self.assertTrue(junction.is_junction())
            engine = Engine(Config.load(self.config_path), self.state)
            with self.assertRaises(SafetyError):
                engine.plan("my-pack")
        finally:
            os.rmdir(junction)

    def test_dirty_source_is_rejected(self) -> None:
        (self.repo / "first-skill" / "SKILL.md").write_text("dirty", encoding="utf-8")
        with self.assertRaises(SafetyError):
            self.engine.plan("my-pack")

    def test_dry_run_does_not_write(self) -> None:
        plan = self.engine.plan("my-pack")
        self.assertEqual({item["action"] for item in plan["items"]}, {"create"})
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.state.exists())

    def test_sync_to_both_targets_and_status(self) -> None:
        result = self.engine.sync("my-pack")
        self.assertEqual(result["result"], "synced")
        for target in (self.codex, self.claude):
            for skill in ("first-skill", "second-skill"):
                self.assertTrue((target / skill / "SKILL.md").is_file())
        status = self.engine.status("my-pack")
        self.assertEqual({item["action"] for item in status["items"]}, {"unchanged"})
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["transactions"]), 1)
        self.assertEqual(len(state["transactions"][0]["after"]), 4)

    def test_unmanaged_collision_is_not_overwritten(self) -> None:
        collision = self.codex / "first-skill"
        collision.mkdir(parents=True)
        marker = collision / "owner.txt"
        marker.write_text("keep me", encoding="utf-8")
        with self.assertRaises(SafetyError):
            self.engine.sync("my-pack", ["codex"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")
        self.assertFalse((self.codex / "second-skill").exists())

    def test_managed_drift_is_not_overwritten(self) -> None:
        self.engine.sync("my-pack", ["codex"])
        installed = self.codex / "first-skill" / "SKILL.md"
        installed.write_text("local edit", encoding="utf-8")
        before = installed.read_bytes()
        with self.assertRaises(SafetyError):
            self.engine.sync("my-pack", ["codex"])
        self.assertEqual(installed.read_bytes(), before)

    def test_rollback_restores_previous_managed_version(self) -> None:
        self.engine.sync("my-pack", ["codex"])
        old_hash = hash_directory(self.codex / "first-skill")
        self._write_skill("first-skill", "version two")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "update pack")
        self._write_config()
        self.engine = Engine(Config.load(self.config_path), self.state)
        self.engine.sync("my-pack", ["codex"])
        self.assertNotEqual(hash_directory(self.codex / "first-skill"), old_hash)
        result = self.engine.rollback("my-pack")
        self.assertEqual(result["result"], "rolled-back")
        self.assertEqual(hash_directory(self.codex / "first-skill"), old_hash)
        status = self.engine.status("my-pack", ["codex"])
        first = next(item for item in status["items"] if item["skill"] == "first-skill")
        self.assertEqual(first["action"], "update")

    def test_first_install_can_be_rolled_back(self) -> None:
        self.engine.sync("my-pack", ["claude"])
        self.engine.rollback("my-pack")
        self.assertFalse((self.claude / "first-skill").exists())
        self.assertFalse((self.claude / "second-skill").exists())

    def test_pack_sync_midway_failure_restores_every_target(self) -> None:
        original = self.engine._activate_stage
        calls = 0

        def fail_second(record: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("injected activation failure")
            original(record)

        with mock.patch.object(self.engine, "_activate_stage", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.engine.sync("my-pack")
        for target in (self.codex, self.claude):
            self.assertFalse((target / "first-skill").exists())
            self.assertFalse((target / "second-skill").exists())
            self.assertFalse(target.exists())
        self.assertFalse((self.state / "state.json").exists())
        self.assertFalse(self.state.exists())
        self._assert_no_hidden_transaction_paths()

    def test_journal_write_failure_leaves_no_residue(self) -> None:
        original_replace = core_module.os.replace

        def fail_journal_replace(source: object, destination: object) -> None:
            if Path(destination) == self.engine.pending_file:
                raise OSError("injected journal write failure")
            original_replace(source, destination)

        with mock.patch("skill_magnet.core.os.replace", side_effect=fail_journal_replace):
            with self.assertRaises(OSError):
                self.engine.sync("my-pack")
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.state.exists())
        self._assert_no_hidden_transaction_paths()

    def test_stage_prepare_failure_leaves_no_residue(self) -> None:
        original_copytree = core_module.shutil.copytree
        calls = 0

        def fail_second_copy(source: object, destination: object, *args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            self.assertTrue(
                self.engine.pending_file.exists(),
                "recovery journal must exist before stage preparation",
            )
            if calls == 2:
                raise OSError("injected stage copy failure")
            return original_copytree(source, destination, *args, **kwargs)

        with mock.patch("skill_magnet.core.shutil.copytree", side_effect=fail_second_copy):
            with self.assertRaises(OSError):
                self.engine.sync("my-pack")
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.state.exists())
        self._assert_no_hidden_transaction_paths()

    def test_snapshot_prepare_failure_preserves_old_pack_and_state(self) -> None:
        self.engine.sync("my-pack")
        before_state_hash = hash_directory(self.state)
        before_hashes = {
            (target.name, skill): hash_directory(target / skill)
            for target in (self.codex, self.claude)
            for skill in ("first-skill", "second-skill")
        }
        self._write_skill("first-skill", "updated first")
        self._write_skill("second-skill", "updated second")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "update for snapshot test")
        self._write_config()
        engine = Engine(Config.load(self.config_path), self.state)
        original_copytree = core_module.shutil.copytree

        def fail_snapshot(source: object, destination: object, *args: object, **kwargs: object):
            self.assertTrue(
                engine.pending_file.exists(),
                "recovery journal must exist before snapshot preparation",
            )
            if "snapshots" in Path(destination).parts:
                raise OSError("injected snapshot copy failure")
            return original_copytree(source, destination, *args, **kwargs)

        with mock.patch("skill_magnet.core.shutil.copytree", side_effect=fail_snapshot):
            with self.assertRaises(OSError):
                engine.sync("my-pack")
        self.assertEqual(hash_directory(self.state), before_state_hash)
        for target in (self.codex, self.claude):
            for skill in ("first-skill", "second-skill"):
                self.assertEqual(
                    hash_directory(target / skill), before_hashes[(target.name, skill)]
                )
        self._assert_no_hidden_transaction_paths()

    def test_interrupted_sync_is_recovered_from_pending_journal(self) -> None:
        original = self.engine._activate_stage
        calls = 0

        def interrupt_second(record: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit("simulated process interruption")
            original(record)

        with mock.patch.object(self.engine, "_activate_stage", side_effect=interrupt_second):
            with self.assertRaises(SystemExit):
                self.engine.sync("my-pack")
        self.assertTrue((self.state / "pending-transaction.json").exists())
        recovered_engine = Engine(Config.load(self.config_path), self.state)
        result = recovered_engine.sync("my-pack")
        self.assertEqual(result["result"], "synced")
        for target in (self.codex, self.claude):
            self.assertTrue((target / "first-skill").exists())
            self.assertTrue((target / "second-skill").exists())
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["transactions"]), 1)
        self._assert_no_hidden_transaction_paths()

    def test_tampered_pending_journal_cannot_delete_outside_managed_paths(self) -> None:
        transaction_id = "0" * 32
        canary = self.root / "must-not-delete"
        canary.mkdir()
        (canary / "user-data.txt").write_text("preserve", encoding="utf-8")
        target_root = self.config.targets["codex"]
        self.state.mkdir()
        self.engine.pending_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "id": transaction_id,
                    "mode": "sync",
                    "pack": "my-pack",
                    "state_before": None,
                    "records": [
                        {
                            "target": "codex",
                            "skill": "first-skill",
                            "destination": str(canary),
                            "stage": str(
                                target_root
                                / f".skill-magnet-stage-{transaction_id}-first-skill"
                            ),
                            "backup": str(
                                target_root
                                / f".skill-magnet-old-{transaction_id}-first-skill"
                            ),
                            "before_existed": False,
                            "parent_preexisting": True,
                            "snapshot": None,
                            "install": True,
                        }
                    ],
                    "snapshot_root": str(
                        self.state / "snapshots" / transaction_id
                    ),
                    "remove_snapshot_on_revert": True,
                    "delete_snapshot_on_commit": False,
                    "state_dir_preexisting": True,
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            SafetyError, "Pending record path escapes the managed target"
        ):
            self.engine.rollback("my-pack")

        self.assertTrue((canary / "user-data.txt").is_file())
        self.assertTrue(self.engine.pending_file.is_file())

    def test_new_engine_public_rollback_recovers_interruption(self) -> None:
        self.engine.sync("my-pack")
        original = self.engine._activate_stage
        calls = 0

        def interrupt_fourth(record: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise SystemExit("simulated rollback interruption")
            original(record)

        with mock.patch.object(self.engine, "_activate_stage", side_effect=interrupt_fourth):
            with self.assertRaises(SystemExit):
                self.engine.rollback("my-pack")
        self.assertTrue((self.state / "pending-transaction.json").exists())
        recovered_engine = Engine(Config.load(self.config_path), self.state)
        result = recovered_engine.rollback("my-pack")
        self.assertEqual(result["result"], "rolled-back")
        for target in (self.codex, self.claude):
            self.assertFalse((target / "first-skill").exists())
            self.assertFalse((target / "second-skill").exists())
        self._assert_no_hidden_transaction_paths()

    def test_sync_state_save_failure_restores_pack_and_old_state(self) -> None:
        self.engine.sync("my-pack", ["codex"])
        before_state = (self.state / "state.json").read_bytes()
        before_hashes = {
            skill: hash_directory(self.codex / skill)
            for skill in ("first-skill", "second-skill")
        }
        self._write_skill("first-skill", "new version")
        self._write_skill("second-skill", "new second version")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "update all")
        self._write_config()
        engine = Engine(Config.load(self.config_path), self.state)
        with mock.patch.object(engine, "_write_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                engine.sync("my-pack", ["codex"])
        self.assertEqual((self.state / "state.json").read_bytes(), before_state)
        for skill, expected in before_hashes.items():
            self.assertEqual(hash_directory(self.codex / skill), expected)
        self.assertFalse((self.state / "pending-transaction.json").exists())

    def test_rollback_state_save_failure_restores_entire_pack(self) -> None:
        self.engine.sync("my-pack")
        before_state = (self.state / "state.json").read_bytes()
        before_hashes = {
            (target.name, skill): hash_directory(target / skill)
            for target in (self.codex, self.claude)
            for skill in ("first-skill", "second-skill")
        }
        with mock.patch.object(self.engine, "_write_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine.rollback("my-pack")
        self.assertEqual((self.state / "state.json").read_bytes(), before_state)
        for target in (self.codex, self.claude):
            for skill in ("first-skill", "second-skill"):
                self.assertEqual(
                    hash_directory(target / skill), before_hashes[(target.name, skill)]
                )
        self.assertFalse((self.state / "pending-transaction.json").exists())

    def test_rollback_midway_failure_restores_entire_pack(self) -> None:
        self.engine.sync("my-pack")
        before_state = (self.state / "state.json").read_bytes()
        before_hashes = {
            (target.name, skill): hash_directory(target / skill)
            for target in (self.codex, self.claude)
            for skill in ("first-skill", "second-skill")
        }
        original = self.engine._activate_stage
        calls = 0

        def fail_second(record: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected rollback failure")
            original(record)

        with mock.patch.object(self.engine, "_activate_stage", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.engine.rollback("my-pack")
        self.assertEqual((self.state / "state.json").read_bytes(), before_state)
        for target in (self.codex, self.claude):
            for skill in ("first-skill", "second-skill"):
                self.assertEqual(
                    hash_directory(target / skill), before_hashes[(target.name, skill)]
                )
        self.assertFalse((self.state / "pending-transaction.json").exists())


if __name__ == "__main__":
    unittest.main()
