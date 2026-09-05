from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from skill_magnet.activation import validate_product_state_directory
from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    LibraryTransaction,
    add_skill,
    find_resumable_transaction,
    initialize_library,
    library_mutation_lock,
    local_mutation_status,
    validate_library,
)
from skill_magnet.library_ui import (
    automatic_sync_next_stage,
    hydrate_managed_repository,
    managed_repository_is_owned,
    managed_repository_has_unfinished_transaction,
    managed_repository_path,
    migrate_legacy_managed_repository,
    purge_managed_repository,
    register_skill_source,
    require_registration_source,
    restore_managed_repository_from_github,
    _require_readable_transaction_journals,
)


class LibraryUiRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    def _remote_library(self) -> tuple[Path, str]:
        source = self.root / "remote-source"
        initialize_library(source)
        add_skill(
            source,
            skill_id="first-skill",
            display_name="First skill",
            purpose="Exercise workspace hydration",
            pack_id="first-pack",
        )
        self._git("init", "-b", "main", cwd=source)
        self._git("add", "--all", cwd=source)
        self._git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
            cwd=source,
        )
        commit = self._git("rev-parse", "HEAD", cwd=source)
        bare = self.root / "skills.git"
        self._git("clone", "--bare", str(source), str(bare))
        return bare, commit

    def _skill(self, folder: Path, skill_id: str, detail: str = "initial") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            "---\n"
            f"name: {skill_id}\n"
            f"description: Exercise {skill_id} registration.\n"
            "---\n\n"
            f"# {skill_id}\n\n{detail}\n",
            encoding="utf-8",
        )

    def _pack(self, folder: Path, skill_id: str, detail: str = "initial") -> None:
        self._skill(folder / skill_id, skill_id, detail)
        (folder / "INDEX.md").write_text(
            f"# {folder.name}\n\n- [{skill_id}](./{skill_id}/SKILL.md)\n",
            encoding="utf-8",
        )

    def test_reserved_runtime_skill_roots_cannot_be_product_state(self) -> None:
        home = self.root / "home"
        for product in (".codex", ".agents", ".claude"):
            reserved = home / product / "skills"
            child = reserved / "some-skill" / "state"
            child.mkdir(parents=True)
            for candidate in (reserved, child):
                with self.subTest(candidate=candidate), self.assertRaisesRegex(
                    SkillMagnetError, "~/.skill-magnet"
                ):
                    validate_product_state_directory(candidate, home)
        allowed = home / ".skill-magnet"
        self.assertEqual(
            validate_product_state_directory(allowed, home), allowed.resolve()
        )

    def test_ephemeral_workspace_hydrates_pinned_commit_and_purges(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "state"
        repository = managed_repository_path(state)

        hydrated = hydrate_managed_repository(
            state, repository, str(remote), commit=commit
        )

        self.assertTrue(hydrated["hydrated"])
        self.assertTrue(validate_library(repository).as_dict()["valid"])
        self.assertTrue((repository / "first-skill" / "SKILL.md").is_file())
        (repository / "first-skill" / "SKILL.md").chmod(stat.S_IREAD)
        purged = purge_managed_repository(state, repository)
        self.assertTrue(purged["purged"])
        self.assertFalse(repository.exists())

        relaunched = hydrate_managed_repository(
            state, repository, str(remote), commit=commit
        )
        self.assertTrue(relaunched["hydrated"])
        self.assertTrue((repository / "first-skill" / "SKILL.md").is_file())
        self.assertTrue(purge_managed_repository(state, repository)["purged"])

    def test_cancel_cleanup_and_unowned_user_files_are_never_deleted(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "cancel-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)

        # Closing without a mutation uses the same purge operation as a completed
        # transaction and leaves no copied skill content behind.
        self.assertTrue(purge_managed_repository(state, repository)["purged"])
        self.assertFalse(repository.exists())

        repository.mkdir(parents=True)
        user_file = repository / "user-created.txt"
        user_file.write_text("must survive\n", encoding="utf-8")
        refused = purge_managed_repository(state, repository)
        self.assertFalse(refused["purged"])
        self.assertEqual(refused["reason"], "unowned_workspace_preserved")
        self.assertEqual(user_file.read_text(encoding="utf-8"), "must survive\n")
        with self.assertRaisesRegex(SkillMagnetError, "自動削除・上書きしません"):
            hydrate_managed_repository(state, repository, str(remote), commit=commit)
        self.assertTrue(user_file.is_file())

    def test_failed_or_interrupted_transaction_preserves_only_its_workspace(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "failure-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        transaction = LibraryTransaction(state, "transaction-current-repository")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "interrupted",
                "draft": str(repository),
                "remote": str(remote),
                "resume_status": "draft",
            }
        )

        preserved = hydrate_managed_repository(
            state, repository, str(remote), commit=commit
        )
        self.assertFalse(preserved["hydrated"])
        self.assertEqual(preserved["reason"], "unfinished_transaction_preserved")
        self.assertTrue((repository / "first-skill" / "SKILL.md").is_file())
        self.assertTrue(
            managed_repository_has_unfinished_transaction(
                state, repository, remote=str(remote)
            )
        )

        other = self.root / "other-library"
        other.mkdir()
        other_transaction = LibraryTransaction(state, "transaction-other-repository")
        other_transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": other_transaction.transaction_id,
                "status": "interrupted",
                "draft": str(other),
                "remote": "https://github.com/example/other.git",
                "resume_status": "draft",
            }
        )
        self.assertEqual(
            find_resumable_transaction(
                state, draft=repository, remote=str(remote)
            ).transaction_id,
            transaction.transaction_id,
        )

    def test_markerless_manual_clone_is_never_adopted_or_deleted(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "legacy-state"
        repository = managed_repository_path(state)
        repository.parent.mkdir(parents=True)
        self._git("clone", str(remote), str(repository))
        sentinel = repository / ".git" / "USER_SENTINEL"
        sentinel.write_text("manual clone must survive\n", encoding="utf-8")

        with self.assertRaisesRegex(SkillMagnetError, "所有証明|自動所有化"):
            migrate_legacy_managed_repository(
                state, repository, str(remote), commit=commit
            )
        self.assertFalse(managed_repository_is_owned(state, repository))
        refused = purge_managed_repository(state, repository)
        self.assertFalse(refused["purged"])
        self.assertEqual(refused["reason"], "unowned_workspace_preserved")
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"), "manual clone must survive\n"
        )

    def test_explicit_remote_restore_preserves_unowned_original_as_backup(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "explicit-legacy-restore-state"
        repository = managed_repository_path(state)
        repository.parent.mkdir(parents=True)
        self._git("clone", str(remote), str(repository))
        sentinel = repository / ".git" / "USER_SENTINEL"
        sentinel.write_text("manual clone backup\n", encoding="utf-8")

        restored = restore_managed_repository_from_github(
            repository, str(remote), commit=commit
        )
        backup = Path(str(restored["backup"]))
        self.assertTrue(backup.is_dir())
        self.assertEqual(
            (backup / ".git" / "USER_SENTINEL").read_text(encoding="utf-8"),
            "manual clone backup\n",
        )
        self.assertTrue((repository / "first-skill" / "SKILL.md").is_file())
        self.assertFalse(managed_repository_is_owned(state, repository))

    def test_stale_outer_marker_cannot_authorize_replacement_deletion(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "stale-marker-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        displaced = repository.with_name(repository.name + "-old-product-copy")
        os.replace(repository, displaced)
        repository.mkdir()
        sentinel = repository / "USER_SENTINEL"
        sentinel.write_text("replacement must survive\n", encoding="utf-8")

        self.assertFalse(managed_repository_is_owned(state, repository))
        refused = purge_managed_repository(state, repository)
        self.assertFalse(refused["purged"])
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"), "replacement must survive\n"
        )

    def test_legacy_unknown_file_is_preserved_and_not_adopted(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "legacy-user-file-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        (state / "library" / "managed-workspace.json").unlink()
        user_file = repository / "user-created.txt"
        user_file.write_text("keep\n", encoding="utf-8")

        with self.assertRaisesRegex(SkillMagnetError, "所有証明|自動所有化"):
            migrate_legacy_managed_repository(
                state, repository, str(remote), commit=commit
            )
        self.assertEqual(user_file.read_text(encoding="utf-8"), "keep\n")
        self.assertIsNone(
            find_resumable_transaction(
                state, draft=repository, remote=str(remote)
            )
        )

    def test_mismatched_ownership_nonce_preserves_workspace(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "nonce-mismatch-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        inner = repository / ".skill-magnet.workspace-owner.json"
        value = json.loads(inner.read_text(encoding="utf-8"))
        value["ownership_nonce"] = "0" * 32
        inner.write_text(json.dumps(value), encoding="utf-8")
        sentinel = repository / "USER_SENTINEL"
        sentinel.write_text("nonce mismatch must survive\n", encoding="utf-8")

        self.assertFalse(managed_repository_is_owned(state, repository))
        refused = purge_managed_repository(state, repository)
        self.assertFalse(refused["purged"])
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"), "nonce mismatch must survive\n"
        )

    def test_corrupt_transaction_journal_blocks_purge_hydrate_and_new_work(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "corrupt-journal-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        sentinel = repository / "USER_SENTINEL"
        sentinel.write_text("corrupt journal must preserve data\n", encoding="utf-8")
        journal = state / "library-transactions" / "corrupt-record" / "journal.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{not-json", encoding="utf-8")

        self.assertTrue(
            managed_repository_has_unfinished_transaction(state, repository)
        )
        purge = purge_managed_repository(state, repository)
        self.assertFalse(purge["purged"])
        self.assertEqual(purge["reason"], "unreadable_transaction_state_preserved")
        hydrated = hydrate_managed_repository(
            state, repository, str(remote), commit=commit
        )
        self.assertFalse(hydrated["hydrated"])
        with self.assertRaisesRegex(SkillMagnetError, "記録が壊れている|復旧不能"):
            _require_readable_transaction_journals(state, repository)
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"),
            "corrupt journal must preserve data\n",
        )

    def test_purge_cannot_enter_while_crud_lock_is_held(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "purge-lock-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        locked = threading.Event()
        release = threading.Event()

        def hold_crud_lock() -> None:
            with library_mutation_lock(repository):
                locked.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=hold_crud_lock)
        worker.start()
        self.assertTrue(locked.wait(timeout=2))
        try:
            with self.assertRaisesRegex(SkillMagnetError, "処理中|CRUD"):
                purge_managed_repository(state, repository)
            self.assertTrue(repository.is_dir())
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_purge_preserves_workspace_during_prejournal_transaction_window(self) -> None:
        remote, commit = self._remote_library()
        state = self.root / "transaction-lock-state"
        repository = managed_repository_path(state)
        hydrate_managed_repository(state, repository, str(remote), commit=commit)
        transaction = LibraryTransaction(state, "prejournal-window")
        locked = threading.Event()
        release = threading.Event()

        def hold_transaction_lock() -> None:
            with transaction._transaction_lock():
                locked.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=hold_transaction_lock)
        worker.start()
        self.assertTrue(locked.wait(timeout=2))
        try:
            result = purge_managed_repository(state, repository)
            self.assertFalse(result["purged"])
            self.assertEqual(result["reason"], "unfinished_transaction_preserved")
            self.assertTrue(repository.is_dir())
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_managed_root_junction_and_registration_source_link_are_rejected(self) -> None:
        state = self.root / "junction-state"
        repository = managed_repository_path(state)
        outside = self.root / "outside-library"
        initialize_library(outside)
        sentinel = outside / "USER_SENTINEL"
        sentinel.write_text("outside must survive\n", encoding="utf-8")
        repository.parent.mkdir(parents=True)
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(repository), str(outside)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest(f"junction creation unavailable: {result.stderr}")
        else:
            repository.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaisesRegex(SkillMagnetError, "junction|リンク"):
                managed_repository_path(state)
            with self.assertRaisesRegex(SkillMagnetError, "junction|リンク"):
                purge_managed_repository(state, repository)
            with self.assertRaisesRegex(SkillMagnetError, "junction|リンク"):
                require_registration_source(str(repository))
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "outside must survive\n"
            )
        finally:
            if os.path.lexists(repository):
                if os.name == "nt":
                    os.rmdir(repository)
                else:
                    repository.unlink()

    def test_closed_unmerged_pr_stops_polling_and_exposes_reopen_action(self) -> None:
        self.assertEqual(
            automatic_sync_next_stage(
                {
                    "status": "published_pending",
                    "wait_state": "closed_unmerged",
                }
            ),
            "reopen_pr",
        )
        self.assertEqual(
            automatic_sync_next_stage(
                {
                    "status": "published_pending",
                    "wait_state": "waiting_for_merge",
                }
            ),
            "waiting",
        )

    def test_closed_pr_reopens_once_and_open_reentry_does_not_duplicate_it(self) -> None:
        calls: list[list[str]] = []

        def run(
            args: list[str], *, cwd: Path | None = None, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps({"state": "CLOSED", "mergeCommit": None}), ""
                )
            return subprocess.CompletedProcess(args, 0, "", "")

        transaction = LibraryTransaction(
            self.root / "reopen-state", "closed-pr-transaction", run=run
        )
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "wait_state": "closed_unmerged",
                "pr_url": "https://github.com/example/skills/pull/7",
            }
        )

        reopened = transaction.reopen_pull_request(confirmed=True)

        self.assertEqual(reopened["wait_state"], "waiting_for_merge")
        self.assertEqual(
            sum(call[:3] == ["gh", "pr", "reopen"] for call in calls), 1
        )

        calls.clear()

        def already_open(
            args: list[str], *, cwd: Path | None = None, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"state": "OPEN", "mergeCommit": None}), ""
            )

        journal = transaction._journal()
        journal["wait_state"] = "closed_unmerged"
        transaction._write_journal(journal)
        transaction.run = already_open
        recovered = transaction.reopen_pull_request(confirmed=True)
        self.assertEqual(recovered["wait_state"], "waiting_for_merge")
        self.assertFalse(any(call[:3] == ["gh", "pr", "reopen"] for call in calls))

    def test_same_ids_with_changed_skill_bytes_is_an_update_not_a_noop(self) -> None:
        repository = self.root / "same-id-library"
        initialize_library(repository)
        source = self.root / "same-skill"
        self._skill(source, "same-skill", "first body")
        first = register_skill_source(repository, source)
        self.assertFalse(first["already_registered"])
        same = register_skill_source(repository, source)
        self.assertTrue(same["already_registered"])

        self._skill(source, "same-skill", "changed body")
        changed = register_skill_source(repository, source)

        self.assertFalse(changed["already_registered"])
        self.assertEqual(changed["updated_skill_ids"], ["same-skill"])
        self.assertIn(
            "changed body",
            (repository / "same-skill" / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_collection_atomically_updates_existing_pack_and_adds_new_pack(self) -> None:
        repository = self.root / "mixed-library"
        initialize_library(repository)
        books = self.root / "books"
        existing = books / "existing-pack"
        self._pack(existing, "existing-skill", "old body")
        register_skill_source(repository, existing)

        self._pack(existing, "existing-skill", "new body")
        self._pack(books / "new-pack", "new-skill", "new pack body")
        result = register_skill_source(repository, books)

        self.assertFalse(result["already_registered"])
        self.assertIn("existing-pack", result["updated_pack_ids"])
        self.assertIn("new-pack", result["imported_pack_ids"])
        self.assertEqual(
            set(validate_library(repository).skill_ids),
            {"existing-skill", "new-skill"},
        )
        self.assertIn(
            "new body",
            (repository / "existing-skill" / "SKILL.md").read_text(encoding="utf-8"),
        )

    def test_forced_exit_after_crud_retains_recoverable_local_intent(self) -> None:
        repository = self.root / "crash-library"
        initialize_library(repository)
        source = self.root / "crash-skill"
        self._skill(source, "crash-skill")
        script = (
            "import os,sys\n"
            "from pathlib import Path\n"
            "from skill_magnet.library_manager import upsert_skill_source\n"
            "upsert_skill_source(Path(sys.argv[1]), Path(sys.argv[2]))\n"
            "os._exit(23)\n"
        )

        completed = subprocess.run(
            ["python", "-c", script, str(repository), str(source)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONPATH": "src"},
            check=False,
        )

        self.assertEqual(completed.returncode, 23)
        status = local_mutation_status(repository)
        self.assertTrue(status["pending"])
        self.assertTrue(status["current_matches_checkpoint"])
        self.assertEqual(status["operations"][-1]["operation"], "upsert_source")
        self.assertTrue(
            managed_repository_has_unfinished_transaction(state_dir=self.root, repository=repository)
        )
        self.assertTrue((repository / "crash-skill" / "SKILL.md").is_file())

    def test_registration_cancelled_before_work_keeps_library_byte_identical(self) -> None:
        repository = self.root / "cancel-before-library"
        initialize_library(repository)
        source = self.root / "cancel-before-skill"
        self._skill(source, "cancel-before-skill")
        before = {
            path.relative_to(repository).as_posix(): path.read_bytes()
            for path in repository.rglob("*")
            if path.is_file()
        }
        cancel = threading.Event()
        cancel.set()

        with self.assertRaisesRegex(SkillMagnetError, "保存前に中止"):
            register_skill_source(repository, source, cancel_event=cancel)

        after = {
            path.relative_to(repository).as_posix(): path.read_bytes()
            for path in repository.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((repository / "cancel-before-skill").exists())

    def test_registration_cancelled_after_candidate_validation_does_not_commit(self) -> None:
        import skill_magnet.library_manager as manager

        repository = self.root / "cancel-gate-library"
        initialize_library(repository)
        source = self.root / "cancel-gate-skill"
        self._skill(source, "cancel-gate-skill")
        before = {
            path.relative_to(repository).as_posix(): path.read_bytes()
            for path in repository.rglob("*")
            if path.is_file()
        }
        cancel = threading.Event()
        validate = manager.validate_library

        def cancel_after_candidate_validation(path: Path, *args: object, **kwargs: object):
            result = validate(path, *args, **kwargs)
            if "-crud-" in str(path.parent):
                cancel.set()
            return result

        with (
            mock.patch(
                "skill_magnet.library_manager.validate_library",
                side_effect=cancel_after_candidate_validation,
            ),
            self.assertRaisesRegex(SkillMagnetError, "保存前に中止"),
        ):
            register_skill_source(repository, source, cancel_event=cancel)

        after = {
            path.relative_to(repository).as_posix(): path.read_bytes()
            for path in repository.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((repository / "cancel-gate-skill").exists())

    def test_close_after_commit_gate_leaves_durable_recovery_checkpoint(self) -> None:
        import skill_magnet.library_manager as manager

        repository = self.root / "commit-gate-library"
        initialize_library(repository)
        source = self.root / "commit-gate-skill"
        self._skill(source, "commit-gate-skill")
        cancel = threading.Event()
        replace = manager.os.replace

        def cancel_during_atomic_commit(source_path: object, destination_path: object) -> None:
            replace(source_path, destination_path)
            if (
                Path(source_path) == repository
                and Path(destination_path).name.startswith(f".{repository.name}-backup-")
            ):
                # The UI may receive WM_CLOSE immediately after the final
                # pre-commit cancellation gate.  From here the durable journal
                # must make the committed CRUD operation recoverable.
                cancel.set()

        with mock.patch(
            "skill_magnet.library_manager.os.replace",
            side_effect=cancel_during_atomic_commit,
        ):
            result = register_skill_source(repository, source, cancel_event=cancel)

        self.assertFalse(result["already_registered"])
        self.assertTrue((repository / "commit-gate-skill" / "SKILL.md").is_file())
        status = local_mutation_status(repository)
        self.assertTrue(status["pending"])
        self.assertTrue(status["current_matches_checkpoint"])


if __name__ == "__main__":
    unittest.main()
