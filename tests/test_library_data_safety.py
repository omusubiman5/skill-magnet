from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from skill_magnet import library_manager as manager
from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    CATALOG_FILENAME,
    LOCAL_MUTATION_FILENAME,
    LibraryTransaction,
    add_skill,
    delete_skill,
    discover_skill_sources,
    import_skill_source,
    initialize_library,
    render_index,
    local_mutation_status,
    upsert_skill_source,
    update_pack_source,
    validate_library,
)


class LibraryDataSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def skill(self, parent: Path, skill_id: str, body: str = "original") -> Path:
        source = parent / skill_id
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\n"
            f"name: {skill_id}\n"
            f"description: {body}\n"
            "---\n\n"
            f"# {skill_id}\n\n"
            "## Trigger\n\nUse for the test.\n\n"
            "## Boundary\n\nDo not touch unrelated data.\n",
            encoding="utf-8",
        )
        return source

    def library(self, name: str = "library") -> Path:
        root = self.root / name
        initialize_library(root)
        add_skill(
            root,
            skill_id="first-skill",
            display_name="First",
            purpose="First purpose",
            pack_id="first-pack",
        )
        add_skill(
            root,
            skill_id="second-skill",
            display_name="Second",
            purpose="Second purpose",
            pack_id="second-pack",
        )
        return root

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def remote(self) -> tuple[Path, Path]:
        seed = self.library("seed")
        self.git(seed, "init", "-b", "main")
        self.git(seed, "add", "--all")
        self.git(
            seed,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "seed",
        )
        remote = self.root / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(seed), str(remote)],
            check=True,
            capture_output=True,
        )
        return seed, remote

    def test_fresh_library_cannot_delete_remote_managed_files(self) -> None:
        _, remote = self.remote()
        fresh = self.root / "fresh"
        initialize_library(fresh)
        add_skill(
            fresh,
            skill_id="unrelated-skill",
            display_name="Unrelated",
            purpose="Must not replace a populated remote",
            pack_id="unrelated-pack",
        )

        transaction = LibraryTransaction(self.root / "state", "fresh-delete-guard")
        with self.assertRaisesRegex(SkillMagnetError, "baseline|基準|復旧"):
            transaction.prepare(draft=fresh, remote=str(remote), branch="main")

        checkout = self.root / "fresh-checkout"
        subprocess.run(["git", "clone", str(remote), str(checkout)], check=True)
        self.assertTrue((checkout / "first-skill" / "SKILL.md").is_file())
        self.assertTrue((checkout / "second-skill" / "SKILL.md").is_file())

    def test_stale_delete_is_rejected_when_remote_changed_after_local_baseline(self) -> None:
        seed, remote = self.remote()
        stale = self.root / "stale"
        shutil.copytree(seed, stale, ignore=shutil.ignore_patterns(".git"))
        delete_skill(stale, "first-skill", confirmed=True)

        (seed / "first-skill" / "SKILL.md").write_text(
            (seed / "first-skill" / "SKILL.md").read_text(encoding="utf-8")
            + "\nRemote-only change.\n",
            encoding="utf-8",
        )
        self.git(seed, "add", "--all")
        self.git(
            seed,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "remote changed",
        )
        self.git(seed, "push", str(remote), "main")

        transaction = LibraryTransaction(self.root / "state", "stale-delete-guard")
        with self.assertRaisesRegex(SkillMagnetError, "baseline|基準|更新"):
            transaction.prepare(draft=stale, remote=str(remote), branch="main")

    def test_shared_skill_pack_update_allows_identical_bytes_and_rejects_drift(self) -> None:
        repository = self.library()
        catalog_path = repository / CATALOG_FILENAME
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        second_pack = catalog["packs"][1]
        second_pack["skills"].append("first-skill")
        second_pack["skill_metadata"]["first-skill"] = {
            "display_name": "First",
            "purpose": "First purpose",
        }
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        (repository / "INDEX.md").write_text(render_index(catalog), encoding="utf-8")
        # The manual edit above constructs a historical shared-skill fixture;
        # it is not an operation under test.
        (repository / LOCAL_MUTATION_FILENAME).unlink(missing_ok=True)
        validate_library(repository)

        identical_pack = self.root / "sources" / "first-pack"
        shutil.copytree(repository / "first-skill", identical_pack / "first-skill")
        result = update_pack_source(repository, "first-pack", identical_pack)
        self.assertEqual(result["operation"], "update_pack")

        changed_pack = self.root / "changed" / "first-pack"
        shutil.copytree(repository / "first-skill", changed_pack / "first-skill")
        skill_file = changed_pack / "first-skill" / "SKILL.md"
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8") + "\nChanged only for one pack.\n",
            encoding="utf-8",
        )
        before = (repository / "first-skill" / "SKILL.md").read_bytes()
        with self.assertRaisesRegex(SkillMagnetError, "共有|shared"):
            update_pack_source(repository, "first-pack", changed_pack)
        self.assertEqual((repository / "first-skill" / "SKILL.md").read_bytes(), before)

    def test_pack_update_cannot_rename_a_skill_as_an_implicit_deletion(self) -> None:
        repository = self.library()
        replacement = self.root / "replacement" / "first-pack"
        self.skill(replacement, "replacement-skill")
        with self.assertRaisesRegex(SkillMagnetError, "削除|rename|明示"):
            update_pack_source(repository, "first-pack", replacement)
        self.assertTrue((repository / "first-skill" / "SKILL.md").is_file())

    def test_nested_filesystem_indirection_is_rejected(self) -> None:
        repository = self.library()
        source = self.skill(self.root / "links", "linked-skill")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "payload.txt").write_text("must not be traversed", encoding="utf-8")
        link = source / "references"
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest(f"junction creation unavailable: {result.stderr}")
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SkillMagnetError, "link|Link|Symbolic|Junction|リンク"):
            import_skill_source(repository, source)

    def test_root_entry_pack_does_not_copy_child_skill_inside_entry_skill(self) -> None:
        repository = self.library()
        pack = self.root / "root-pack"
        self.skill(pack.parent, pack.name, "Root entry")
        # The helper created root-pack/SKILL.md; this child is a separate member.
        self.skill(pack, "child-skill", "Child")
        (pack / "references").mkdir()
        (pack / "references" / "guide.md").write_text("support", encoding="utf-8")

        imported = import_skill_source(repository, pack)
        self.assertIn("root-pack", imported["imported_skill_ids"])
        self.assertIn("child-skill", imported["imported_skill_ids"])
        self.assertFalse((repository / "root-pack" / "child-skill").exists())
        self.assertTrue((repository / "child-skill" / "SKILL.md").is_file())
        self.assertTrue((repository / "root-pack" / "references" / "guide.md").is_file())

    def test_mixed_collection_accounts_for_standalone_pack_and_ambiguous_sibling(self) -> None:
        collection = self.root / "collection"
        pack = collection / "documented-pack"
        self.skill(pack, "pack-skill")
        self.skill(collection, "standalone-skill")
        support = collection / "audit"
        support.mkdir(parents=True)
        (support / "report.md").write_text("support", encoding="utf-8")

        discovered = discover_skill_sources(collection)
        self.assertEqual(
            {item["id"] for item in discovered},
            {"documented-pack", "custom-skills"},
        )
        accepted = {
            path
            for item in discovered
            for path in item["candidate_accounting"]["accepted"]
        }
        self.assertIn("documented-pack", accepted)
        self.assertIn("standalone-skill", accepted)

        ambiguous = collection / "mystery-material"
        ambiguous.mkdir()
        (ambiguous / "notes.md").write_text("unknown", encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "mystery-material"):
            discover_skill_sources(collection)

    def test_concurrent_crud_fails_one_writer_instead_of_losing_an_update(self) -> None:
        repository = self.library()
        first_source = self.skill(self.root / "concurrent-a", "third-skill")
        second_source = self.skill(self.root / "concurrent-b", "fourth-skill")
        entered = threading.Event()
        release = threading.Event()
        original = manager._write_source_skill

        def held_write(*args: object, **kwargs: object) -> object:
            target = args[0]
            if isinstance(target, Path) and target.name == "third-skill":
                entered.set()
                self.assertTrue(release.wait(10))
            return original(*args, **kwargs)

        outcome: list[object] = []

        def first_writer() -> None:
            try:
                outcome.append(import_skill_source(repository, first_source))
            except Exception as exc:  # pragma: no cover - assertion reports it
                outcome.append(exc)

        with mock.patch.object(manager, "_write_source_skill", side_effect=held_write):
            thread = threading.Thread(target=first_writer)
            thread.start()
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(SkillMagnetError, "別の|同時|進行中|lock"):
                import_skill_source(repository, second_source)
            release.set()
            thread.join(10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertNotIsInstance(outcome[0], Exception)
        inventory = validate_library(repository)
        self.assertIn("third-skill", inventory.skill_ids)
        self.assertNotIn("fourth-skill", inventory.skill_ids)

    def test_old_publish_cannot_clear_a_different_local_mutation(self) -> None:
        repository = self.library()
        state_path = repository / LOCAL_MUTATION_FILENAME
        initial = json.loads(state_path.read_text(encoding="utf-8"))
        current = manager._managed_manifest_snapshot(repository)
        expected_id = str(initial["mutation_id"])
        expected_revision = int(initial["revision"])

        # A replacement journal may reuse the same revision number.  The old
        # publish completion must not mark this new mutation synchronized.
        replacement = dict(initial)
        replacement["mutation_id"] = "replacement-mutation"
        manager._atomic_json(state_path, replacement)
        manager._mark_local_mutation_synchronized(
            repository,
            current,
            expected_revision=expected_revision,
            expected_mutation_id=expected_id,
        )
        observed = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(observed["pending"])
        self.assertEqual(observed["mutation_id"], "replacement-mutation")

        # Identity alone is insufficient: only the exact verified remote
        # bytes may clear the matching local intent.
        replacement["mutation_id"] = expected_id
        manager._atomic_json(state_path, replacement)
        remote_mismatch = dict(current)
        relative = next(iter(remote_mismatch))
        remote_mismatch[relative] = "0" * 64
        manager._mark_local_mutation_synchronized(
            repository,
            remote_mismatch,
            expected_revision=expected_revision,
            expected_mutation_id=expected_id,
        )
        self.assertTrue(json.loads(state_path.read_text(encoding="utf-8"))["pending"])

        manager._mark_local_mutation_synchronized(
            repository,
            current,
            expected_revision=expected_revision,
            expected_mutation_id=expected_id,
        )
        self.assertFalse(json.loads(state_path.read_text(encoding="utf-8"))["pending"])

    def test_git_metadata_updated_during_crud_is_preserved(self) -> None:
        repository = self.library()
        self.git(repository, "init", "-b", "main")
        marker = repository / ".git" / "skill-magnet-concurrency-marker"
        marker.write_text("before", encoding="utf-8")
        source = self.skill(self.root / "git-concurrent", "third-skill")
        original = manager._write_source_skill

        def git_changes_while_candidate_is_built(*args: object, **kwargs: object) -> object:
            marker.write_text("updated-by-git", encoding="utf-8")
            return original(*args, **kwargs)

        with mock.patch.object(
            manager,
            "_write_source_skill",
            side_effect=git_changes_while_candidate_is_built,
        ):
            import_skill_source(repository, source)

        self.assertEqual(marker.read_text(encoding="utf-8"), "updated-by-git")
        self.assertTrue((repository / ".git" / "HEAD").is_file())
        self.assertIn("third-skill", validate_library(repository).skill_ids)

    def test_git_metadata_crash_window_is_recoverable(self) -> None:
        repository = self.library()
        self.git(repository, "init", "-b", "main")
        marker = repository / ".git" / "skill-magnet-recovery-marker"
        marker.write_text("preserve-me", encoding="utf-8")
        backup = repository.parent / f".{repository.name}-backup-simulated"
        backup.mkdir()
        os.replace(repository / ".git", backup / ".git")

        result = manager.recover_interrupted_library(repository)

        self.assertTrue(result["recovered"])
        self.assertEqual(result["recovery"], "git_metadata_reattached")
        self.assertEqual(
            (repository / ".git" / "skill-magnet-recovery-marker").read_text(
                encoding="utf-8"
            ),
            "preserve-me",
        )
        self.assertFalse(backup.exists())

    def test_collection_upsert_updates_existing_and_adds_new_pack_atomically(self) -> None:
        repository = self.library()
        collection = self.root / "upsert"
        shutil.copytree(repository / "first-skill", collection / "first-pack" / "first-skill")
        first_file = collection / "first-pack" / "first-skill" / "SKILL.md"
        first_file.write_text(
            first_file.read_text(encoding="utf-8") + "\nUpdated content.\n",
            encoding="utf-8",
        )
        self.skill(collection / "new-pack", "third-skill", "Third")
        result = upsert_skill_source(repository, collection)
        self.assertEqual(result["updated_pack_ids"], ["first-pack"])
        self.assertEqual(result["imported_pack_ids"], ["new-pack"])
        self.assertIn("Updated content", (repository / "first-skill" / "SKILL.md").read_text())
        self.assertTrue((repository / "third-skill" / "SKILL.md").is_file())
        self.assertTrue(local_mutation_status(repository)["pending"])

        before = manager._tree_digest(repository)
        failing = self.root / "failing-upsert"
        shutil.copytree(repository / "first-skill", failing / "first-pack" / "first-skill")
        first_retry = failing / "first-pack" / "first-skill" / "SKILL.md"
        first_retry.write_text(first_retry.read_text() + "\nMust roll back.\n", encoding="utf-8")
        shutil.copytree(repository / "second-skill", failing / "newer-pack" / "second-skill")
        second_changed = failing / "newer-pack" / "second-skill" / "SKILL.md"
        second_changed.write_text(second_changed.read_text() + "\nConflicting bytes.\n", encoding="utf-8")
        self.skill(failing / "newer-pack", "fourth-skill", "Fourth")
        with self.assertRaisesRegex(SkillMagnetError, "共有"):
            upsert_skill_source(repository, failing)
        self.assertEqual(manager._tree_digest(repository), before)
        self.assertFalse((repository / "fourth-skill").exists())


if __name__ == "__main__":
    unittest.main()
