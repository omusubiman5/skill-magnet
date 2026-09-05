from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from skill_magnet import library_manager as manager
from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    LibraryTransaction,
    add_skill,
    canonical_remote_identity,
    discover_skill_sources,
    find_resumable_transaction,
    initialize_library,
    recover_interrupted_library,
    validate_library,
)


class TransactionConcurrencySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_library(self, name: str = "library") -> Path:
        root = self.root / name
        initialize_library(root, name)
        add_skill(
            root,
            skill_id="first-skill",
            display_name="First skill",
            purpose="Exercise transaction recovery",
            pack_id="first-pack",
            pack_display_name="First pack",
        )
        return root

    @staticmethod
    def pending_journal(transaction: LibraryTransaction, draft: Path) -> dict[str, object]:
        return {
            "schema_version": 1,
            "transaction_id": transaction.transaction_id,
            "status": "published_pending",
            "commit": "a" * 40,
            "draft": str(draft.resolve()),
            "remote": "https://github.com/example/skills.git",
            "branch": "codex/test",
            "default_branch": "main",
            "pr_url": "https://github.com/example/skills/pull/1",
            "preview": {"manifest": {"INDEX.md": "digest"}},
        }

    def test_github_remote_identity_normalizes_case_suffix_and_slash(self) -> None:
        variants = {
            "https://github.com/Owner/Repo",
            "HTTPS://GITHUB.COM/OWNER/REPO.git",
            "https://github.com/owner/repo.git/",
        }
        self.assertEqual(
            {canonical_remote_identity(value) for value in variants},
            {"https://github.com/owner/repo.git"},
        )

    def test_resumable_search_uses_canonical_remote_identity(self) -> None:
        draft = self.make_library("canonical-resume")
        state = self.root / "canonical-state"
        transaction = LibraryTransaction(state, "canonical-transaction")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "prepared",
                "draft": str(draft.resolve()),
                "remote": "HTTPS://GITHUB.COM/Owner/Repo",
                "preview": {"manifest": {}},
            }
        )
        found = find_resumable_transaction(
            state,
            draft=draft,
            remote="https://github.com/owner/repo.git/",
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.transaction_id, transaction.transaction_id)

    def test_prepare_rejects_reusing_transaction_for_other_draft_or_remote(self) -> None:
        draft_a = self.make_library("draft-a")
        draft_b = self.make_library("draft-b")
        transaction = LibraryTransaction(self.root / "state", "identity-check")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "prepared",
                "draft": str(draft_a.resolve()),
                "remote": "https://github.com/example/one.git",
                "preview": {"manifest": {}},
            }
        )
        with self.assertRaisesRegex(SkillMagnetError, "別のライブラリ"):
            transaction.prepare(
                draft=draft_b, remote="https://github.com/example/one.git"
            )
        with self.assertRaisesRegex(SkillMagnetError, "別のGitHub公開先"):
            transaction.prepare(
                draft=draft_a, remote="https://github.com/example/two.git"
            )

    def test_complete_rechecks_remote_identity_before_later_stage(self) -> None:
        draft = self.make_library("complete-identity")
        transaction = LibraryTransaction(self.root / "state", "later-stage-identity")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "prepared",
                "draft": str(draft.resolve()),
                "remote": "https://github.com/example/original.git",
                "preview": {"manifest": {}},
            }
        )
        with self.assertRaisesRegex(SkillMagnetError, "別のGitHub公開先"):
            transaction.complete_automatically(
                draft=draft,
                remote="https://github.com/example/replacement.git",
                config_path=self.root / "config.json",
                confirmed=True,
            )

    def test_corrupt_journal_blocks_resumable_search(self) -> None:
        draft = self.make_library("corrupt-search")
        corrupt = self.root / "state" / "library-transactions" / "corrupt-record"
        corrupt.mkdir(parents=True)
        (corrupt / "journal.json").write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "作業記録が壊れ"):
            find_resumable_transaction(
                self.root / "state",
                draft=draft,
                remote="https://github.com/example/skills.git",
            )

    def test_semantically_incomplete_journal_also_fails_closed(self) -> None:
        draft = self.make_library("incomplete-search")
        corrupt = self.root / "other-state" / "library-transactions" / "empty-record"
        corrupt.mkdir(parents=True)
        (corrupt / "journal.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "作業記録が壊れ"):
            find_resumable_transaction(
                self.root / "other-state",
                draft=draft,
                remote="https://github.com/example/skills.git",
            )

    def test_selected_source_link_is_rejected_before_resolution(self) -> None:
        real = self.make_library("real-source")
        selected = self.root / "selected-link"
        try:
            os.symlink(real, selected, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"directory symlink unavailable: {exc}")
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(selected), str(real)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode:
                self.skipTest(f"directory junction unavailable: {created.stderr}")
        try:
            with self.assertRaisesRegex(
                SkillMagnetError, "シンボリックリンク|ジャンクション"
            ):
                discover_skill_sources(selected)
        finally:
            # Remove the exact junction/link entry before TemporaryDirectory
            # cleanup so no recursive operation can cross its boundary.
            if os.path.lexists(selected):
                os.rmdir(selected)

    def test_saved_draft_replaced_by_link_cannot_match_another_library(self) -> None:
        saved = self.make_library("saved-draft")
        replacement = self.make_library("replacement-target")
        preserved = self.root / "saved-draft-preserved"
        state = self.root / "saved-link-state"
        transaction = LibraryTransaction(state, "saved-link-identity")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "prepared",
                "draft": str(saved.resolve()),
                "remote": "https://github.com/example/skills.git",
                "preview": {"manifest": {}},
            }
        )
        os.replace(saved, preserved)
        try:
            os.symlink(replacement, saved, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"directory symlink unavailable: {exc}")
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(saved), str(replacement)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode:
                self.skipTest(f"directory junction unavailable: {created.stderr}")
        try:
            with self.assertRaisesRegex(
                SkillMagnetError, "保存済みライブラリ.*リンク|ジャンクション"
            ):
                find_resumable_transaction(
                    state,
                    draft=replacement,
                    remote="https://github.com/example/skills.git",
                )
        finally:
            if os.path.lexists(saved):
                if saved.is_symlink():
                    saved.unlink()
                else:
                    os.rmdir(saved)

    def test_recovery_uses_crud_lock_and_preserves_backup(self) -> None:
        repository = self.make_library("locked-recovery")
        backup = repository.parent / f".{repository.name}-backup-test"
        os.replace(repository, backup)
        with manager.library_mutation_lock(repository):
            with self.assertRaisesRegex(SkillMagnetError, "CRUD操作が進行中"):
                recover_interrupted_library(repository)
            self.assertFalse(repository.exists())
            self.assertTrue(backup.is_dir())
            validate_library(backup)
        recovered = recover_interrupted_library(repository)
        self.assertTrue(recovered["recovered"])
        validate_library(repository)

    def test_prepare_cannot_snapshot_while_crud_lock_is_held(self) -> None:
        draft = self.make_library("prepare-vs-crud")
        transaction = LibraryTransaction(self.root / "state", "prepare-vs-crud")
        with manager.library_mutation_lock(draft):
            with self.assertRaisesRegex(SkillMagnetError, "CRUD操作が進行中"):
                transaction.prepare(
                    draft=draft, remote="https://github.com/example/skills.git"
                )
        self.assertFalse(transaction.journal_path.exists())

    def test_same_transaction_lock_blocks_second_instance_remote_effect(self) -> None:
        draft = self.make_library("concurrent-remote")
        state = self.root / "state"
        first = LibraryTransaction(state, "same-transaction")
        second = LibraryTransaction(state, "same-transaction")
        first._write_journal(self.pending_journal(first, draft))
        entered = threading.Event()
        release = threading.Event()
        commands: list[list[str]] = []

        def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            if args[:3] == ["gh", "pr", "merge"]:
                entered.set()
                self.assertTrue(release.wait(3))
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        {"state": "OPEN", "mergeCommit": None, "autoMergeRequest": {}}
                    ),
                    "",
                )
            raise AssertionError(args)

        first.run = run
        outcome: dict[str, object] = {}

        def merge_first() -> None:
            try:
                outcome["value"] = first.merge_pull_request(confirmed=True)
            except BaseException as exc:  # reported in the assertion thread
                outcome["error"] = exc

        worker = threading.Thread(target=merge_first)
        worker.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(SkillMagnetError, "別のプロセスで進行中"):
            second.merge_pull_request(confirmed=True)
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(
            sum(command[:3] == ["gh", "pr", "merge"] for command in commands), 1
        )

    def test_transaction_lock_blocks_another_process(self) -> None:
        state = self.root / "cross-process-state"
        ready = self.root / "cross-process.ready"
        source_root = Path(manager.__file__).resolve().parents[1]
        code = (
            "import time\n"
            "from pathlib import Path\n"
            "from skill_magnet.library_manager import LibraryTransaction\n"
            f"tx=LibraryTransaction(Path({str(state)!r}), 'cross-process-lock')\n"
            "with tx._transaction_lock():\n"
            f"    Path({str(ready)!r}).write_text('locked', encoding='utf-8')\n"
            "    time.sleep(30)\n"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        holder = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if not ready.exists():
                _, stderr = holder.communicate(timeout=2)
                self.fail(f"lock holder did not start: {stderr}")
            contender = LibraryTransaction(state, "cross-process-lock")
            with self.assertRaisesRegex(SkillMagnetError, "別のプロセスで進行中"):
                contender._write_journal(
                    {
                        "schema_version": 1,
                        "transaction_id": contender.transaction_id,
                        "status": "draft",
                    }
                )
        finally:
            if holder.poll() is None:
                holder.terminate()
            try:
                holder.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate(timeout=3)

    def test_cancellation_terminates_descendant_process_tree(self) -> None:
        cancel = threading.Event()
        transaction = LibraryTransaction(
            self.root / "state", "descendant-cancel", cancel_event=cancel
        )
        pid_file = self.root / "descendant.pid"
        child_code = "import time; time.sleep(30)"
        parent_code = (
            "import pathlib,subprocess,sys,time;"
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
            "time.sleep(30)"
        )
        outcome: dict[str, object] = {}

        def invoke() -> None:
            try:
                transaction._exec([sys.executable, "-c", parent_code])
            except BaseException as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=invoke)
        worker.start()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(pid_file.exists())
        descendant_pid = int(pid_file.read_text(encoding="utf-8"))
        cancel.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(outcome.get("error"), manager.ExternalCommandCancelled)

        def alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True

        deadline = time.monotonic() + 3
        while alive(descendant_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if alive(descendant_pid):
            # Prevent a failed test from leaking its fixture process.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(descendant_pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.kill(descendant_pid, 9)
            self.fail(f"descendant process survived cancellation: {descendant_pid}")


if __name__ == "__main__":
    unittest.main()
