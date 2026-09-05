from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from skill_magnet import library_manager as manager_module
from skill_magnet.cli import main as cli_main
from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LOCAL_MUTATION_FILENAME,
    LibraryTransaction,
    add_skill,
    delete_pack,
    delete_skill,
    discover_skill_sources,
    find_resumable_transaction,
    import_skill_source,
    initialize_library,
    library_inventory,
    list_transactions,
    recover_interrupted_library,
    render_index,
    update_pack_source,
    update_skill_source,
    validate_library,
)
from skill_magnet.library_ui import (
    acquire_library_ui_lease,
    configuration_repair_notice,
    configured_repository_url,
    import_selected_skill,
    library_action_label,
    library_failure_message,
    library_wizard_steps,
    managed_repository_path,
    prepare_managed_repository,
    register_skill_source,
    remote_restore_available,
    require_registration_source,
    restore_managed_repository_from_github,
    show_library_manager,
    source_already_registered,
    skill_registration_metadata,
)


class LibraryManagerTests(unittest.TestCase):
    def make_source_skill(self, parent: Path, skill_id: str, description: str = "Updated purpose") -> Path:
        source = parent / skill_id
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\n"
            f"name: {skill_id}\n"
            f"description: {description}\n"
            "---\n\n"
            f"# {skill_id} updated\n\n"
            "## Trigger\n\nUse when requested.\n\n"
            "## Boundary\n\nDo not modify unrelated files.\n",
            encoding="utf-8",
        )
        return source

    def make_crud_library(self) -> Path:
        repository = self.root / "crud-library"
        initialize_library(repository)
        add_skill(
            repository,
            skill_id="first-skill",
            display_name="First skill",
            purpose="First purpose",
            pack_id="first-pack",
            pack_display_name="First pack",
        )
        add_skill(
            repository,
            skill_id="second-skill",
            display_name="Second skill",
            purpose="Second purpose",
            pack_id="second-pack",
            pack_display_name="Second pack",
        )
        return repository

    def test_crud_inventory_update_and_delete(self) -> None:
        repository = self.make_crud_library()
        inventory = library_inventory(repository)
        self.assertEqual(inventory["pack_count"], 2)
        self.assertEqual(inventory["skill_count"], 2)
        self.assertEqual(inventory["packs"][0]["skills"][0]["id"], "first-skill")

        source = self.make_source_skill(self.root / "updates", "first-skill")
        updated = update_skill_source(repository, "first-skill", source)
        self.assertEqual(updated["operation"], "update_skill")
        self.assertIn(
            "first-skill updated",
            (repository / "first-skill" / "SKILL.md").read_text(encoding="utf-8"),
        )
        catalog = json.loads((repository / CATALOG_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(
            catalog["packs"][0]["skill_metadata"]["first-skill"]["purpose"],
            "Updated purpose",
        )

        deleted = delete_skill(repository, "first-skill", confirmed=True)
        self.assertEqual(deleted["operation"], "delete_skill")
        self.assertFalse((repository / "first-skill").exists())
        self.assertEqual(library_inventory(repository)["pack_count"], 1)
        with self.assertRaisesRegex(SkillMagnetError, "最後のパック"):
            delete_pack(repository, "second-pack", confirmed=True)

    def test_update_rejects_wrong_id_and_rolls_back_invalid_content(self) -> None:
        repository = self.make_crud_library()
        before = (repository / "first-skill" / "SKILL.md").read_bytes()
        wrong = self.make_source_skill(self.root / "wrong", "different-skill")
        with self.assertRaisesRegex(SkillMagnetError, "更新対象のスキルID"):
            update_skill_source(repository, "first-skill", wrong)
        invalid = self.make_source_skill(self.root / "invalid", "first-skill")
        (invalid / "SKILL.md").write_text(
            "---\nname: first-skill\ndescription:\n---\n\n# Invalid\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "descriptionがありません"):
            update_skill_source(repository, "first-skill", invalid)
        self.assertEqual((repository / "first-skill" / "SKILL.md").read_bytes(), before)
        validate_library(repository)

    def test_delete_rejects_dependency_and_pack_update_rejects_implicit_rename(self) -> None:
        repository = self.make_crud_library()
        catalog_path = repository / CATALOG_FILENAME
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["packs"][1]["skills"].append("first-skill")
        catalog["packs"][1]["skill_metadata"]["first-skill"] = {
            "display_name": "First skill",
            "purpose": "First purpose",
        }
        catalog["packs"][1]["relations"]["depends-on"] = [["second-skill", "first-skill"]]
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        (repository / "INDEX.md").write_text(render_index(catalog), encoding="utf-8")
        # This direct catalog edit constructs a historical dependency fixture;
        # it is not a Library Manager operation under test.
        (repository / LOCAL_MUTATION_FILENAME).unlink(missing_ok=True)
        with self.assertRaisesRegex(SkillMagnetError, "second-skill"):
            delete_skill(repository, "first-skill", confirmed=True)

        pack_source = self.root / "sources" / "first-pack"
        self.make_source_skill(pack_source, "replacement-skill")
        with self.assertRaisesRegex(SkillMagnetError, "削除・rename"):
            update_pack_source(repository, "first-pack", pack_source)
        inventory = library_inventory(repository)
        first_pack = next(pack for pack in inventory["packs"] if pack["id"] == "first-pack")
        self.assertEqual([skill["id"] for skill in first_pack["skills"]], ["first-skill"])
        self.assertTrue((repository / "first-skill").is_dir())

    def test_create_rejects_same_skill_set_under_another_pack_id(self) -> None:
        repository = self.make_crud_library()
        source = self.root / "duplicate-pack"
        self.make_source_skill(source, "first-skill")
        with self.assertRaisesRegex(SkillMagnetError, "already exists|登録済み"):
            import_skill_source(repository, source)

    def test_managed_repository_path_is_inside_app_state(self) -> None:
        self.assertEqual(
            managed_repository_path(self.root),
            (self.root / "library" / "skill-magnet-skills").resolve(),
        )

    def test_library_ui_lease_serializes_repeated_context_menu_requests(self) -> None:
        state = self.root / "lease-state"
        selected = self.root / "selected-skill"
        selected.mkdir()

        first = acquire_library_ui_lease(state, selected)
        self.assertTrue(first.acquired)
        try:
            first.publish_window(24680)
            duplicate = acquire_library_ui_lease(state, selected)
            self.assertFalse(duplicate.acquired)
            self.assertTrue(duplicate.same_request)
            self.assertEqual(duplicate.owner["phase"], "library_manager")
            self.assertEqual(duplicate.owner["window_handle"], 24680)
            self.assertNotIn("selected_source", duplicate.owner)
            self.assertRegex(duplicate.owner["target_sha256"], r"^[0-9a-f]{64}$")

            other = self.root / "other-skill"
            other.mkdir()
            competing = acquire_library_ui_lease(state, other)
            self.assertFalse(competing.acquired)
            self.assertFalse(competing.same_request)
            self.assertEqual(competing.owner["phase"], "library_manager")
            self.assertEqual(competing.owner["window_handle"], 24680)
        finally:
            first.release()
        recovered = acquire_library_ui_lease(state, other)
        self.assertTrue(recovered.acquired)
        recovered.release()
        self.assertTrue((state / "library-manager.lock").exists())

    def test_library_ui_lease_rejects_linked_lock_without_touching_target(self) -> None:
        state = self.root / "linked-library-lease"
        state.mkdir()
        outside = self.root / "outside-library-lock.txt"
        outside.write_text("preserve-me", encoding="utf-8")
        lock_path = state / "library-manager.lock"
        try:
            os.symlink(outside, lock_path)
        except OSError:
            lock_path.touch()
            with mock.patch.dict(
                acquire_library_ui_lease.__globals__,
                {"_is_link": lambda path: Path(path).name == "library-manager.lock"},
            ):
                with self.assertRaisesRegex(SkillMagnetError, "link or junction"):
                    acquire_library_ui_lease(state, self.root / "selected")
        else:
            with self.assertRaisesRegex(SkillMagnetError, "link or junction"):
                acquire_library_ui_lease(state, self.root / "selected")
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve-me")

    def test_library_ui_lease_blocks_other_process_and_recovers_after_exit(self) -> None:
        state = self.root / "process-lease-state"
        selected = self.root / "selected-process-skill"
        selected.mkdir()
        code = (
            "import json, pathlib, time; "
            "from skill_magnet.library_ui import acquire_library_ui_lease; "
            f"lease=acquire_library_ui_lease(pathlib.Path({str(state)!r}), pathlib.Path({str(selected)!r})); "
            "print(json.dumps({'acquired': lease.acquired}), flush=True); "
            "time.sleep(30)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(json.loads(child.stdout.readline()), {"acquired": True})
            duplicate = acquire_library_ui_lease(state, selected)
            self.assertFalse(duplicate.acquired)
            self.assertTrue(duplicate.same_request)
        finally:
            child.kill()
            child.communicate(timeout=5)

        recovered = acquire_library_ui_lease(state, selected)
        self.assertTrue(recovered.acquired)
        recovered.release()

    def test_repeated_same_folder_registration_focuses_existing_manager(self) -> None:
        lease = SimpleNamespace(
            acquired=False,
            same_request=True,
            owner={"pid": 43210, "selected_source": str(self.root / "selected")},
        )
        with (
            mock.patch(
                "skill_magnet.library_ui.acquire_library_ui_lease", return_value=lease
            ),
            mock.patch(
                "skill_magnet.library_ui.focus_library_ui", return_value=True
            ) as focus,
            mock.patch("tkinter.Tk") as tk_root,
        ):
            result = show_library_manager(
                config_path=self.root / "skill-magnet.json",
                state_dir=self.root / "state",
                initial_repository=self.root / "selected",
                register_selected=True,
            )

        self.assertEqual(result, {"status": "already_running", "same_request": True})
        focus.assert_called_once_with(lease.owner)
        tk_root.assert_not_called()

    def test_standard_selected_skill_is_imported_automatically(self) -> None:
        repository = managed_repository_path(self.root)
        initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        source = self.root / "sample-skill"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: Sample purpose\n---\n\n"
            "# Sample skill\n\n## Trigger\n\nUse for a sample task.\n\n"
            "## Boundary\n\nDo not modify unrelated files.\n",
            encoding="utf-8",
        )
        (source / "acceptance.json").write_text(
            json.dumps(
                {"version": 1, "assertions": [{"path": "result.applied", "equals": True}]}
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(SkillMagnetError, "スキル"):
            require_registration_source("")
        self.assertEqual(require_registration_source(str(source)), source.resolve())
        self.assertEqual(
            skill_registration_metadata(source),
            ("sample-skill", "Sample skill", "Sample purpose"),
        )
        missing_skill = self.root / "missing-skill"
        missing_skill.mkdir()
        (missing_skill / "acceptance.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "SKILL.md"):
            require_registration_source(str(missing_skill))
        missing_acceptance = self.root / "missing-acceptance"
        missing_acceptance.mkdir()
        (missing_acceptance / "SKILL.md").write_text(
            "---\nname: missing-acceptance\ndescription: Missing acceptance\n---\n\n# Missing acceptance\n",
            encoding="utf-8",
        )
        self.assertEqual(
            require_registration_source(str(missing_acceptance)),
            missing_acceptance.resolve(),
        )

        self.assertTrue(import_selected_skill(repository, source))
        catalog = json.loads((repository / CATALOG_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(catalog["packs"][0]["id"], "custom-skills")
        self.assertEqual(catalog["packs"][0]["skills"], ["sample-skill"])
        self.assertTrue((repository / "sample-skill" / "SKILL.md").is_file())
        self.assertTrue(source_already_registered(repository, source))
        before = (repository / CATALOG_FILENAME).read_bytes()
        repeated = register_skill_source(repository, source)
        self.assertTrue(repeated["already_registered"])
        self.assertEqual((repository / CATALOG_FILENAME).read_bytes(), before)
        self.assertTrue(import_selected_skill(repository, source))

    def test_registration_preserves_skill_supporting_resources(self) -> None:
        repository = managed_repository_path(self.root)
        initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        source = self.root / "resource-skill"
        (source / "references").mkdir(parents=True)
        (source / "scripts").mkdir()
        (source / "agents").mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: resource-skill\ndescription: Apply when resource output is requested.\n"
            "---\n\n# Resource skill\n\n## Trigger\n\nUse for resource output.\n\n"
            "## Boundary\n\nDo not publish externally.\n",
            encoding="utf-8",
        )
        (source / "references" / "schema.md").write_text("# Schema\n", encoding="utf-8")
        (source / "scripts" / "render.py").write_text("print('ok')\n", encoding="utf-8")
        (source / "agents" / "openai.yaml").write_text(
            'interface:\n  display_name: "Resource skill"\n', encoding="utf-8"
        )
        (source / "scripts" / "__pycache__").mkdir()
        (source / "scripts" / "__pycache__" / "render.pyc").write_bytes(b"cache")

        register_skill_source(repository, source)

        registered = repository / "resource-skill"
        self.assertTrue((registered / "references" / "schema.md").is_file())
        self.assertTrue((registered / "scripts" / "render.py").is_file())
        self.assertTrue((registered / "agents" / "openai.yaml").is_file())
        self.assertFalse((registered / "scripts" / "__pycache__").exists())

    def test_standard_skill_does_not_require_literal_contract_headings(self) -> None:
        repository = managed_repository_path(self.root)
        initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        source = self.root / "android-cli"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\n"
            "name: android-cli\n"
            "description: Provides instructions for installing and using the android CLI.\n"
            "---\n\n"
            "# Android CLI Specialist\n\n"
            "Manage Android SDK components and interact with virtual devices.\n",
            encoding="utf-8",
        )
        registered = register_skill_source(repository, source)
        self.assertFalse(registered["already_registered"])
        self.assertEqual(registered["imported_skill_ids"], ["android-cli"])
        self.assertTrue(validate_library(repository).as_dict()["valid"])
        self.assertTrue(register_skill_source(repository, source)["already_registered"])

    def test_abrupt_close_between_backup_and_replace_is_recovered(self) -> None:
        repository = self.make_crud_library()
        before = validate_library(repository).as_dict()["manifest"]
        invalid = self.root / "invalid-import"
        invalid.mkdir()
        (invalid / "SKILL.md").write_text(
            "---\nname: invalid-import\ndescription:\n---\n# Invalid\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "descriptionがありません"):
            register_skill_source(repository, invalid)
        self.assertEqual(validate_library(repository).as_dict()["manifest"], before)

        backup = repository.parent / f".{repository.name}-backup-interrupted"
        os.replace(repository, backup)
        self.assertFalse(repository.exists())

        recovery = recover_interrupted_library(repository)

        self.assertTrue(recovery["recovered"])
        self.assertTrue(repository.is_dir())
        self.assertFalse(backup.exists())
        self.assertEqual(validate_library(repository).as_dict()["manifest"], before)

    def test_books_folder_imports_every_pack_and_skill_without_candidate_omission(self) -> None:
        repository = managed_repository_path(self.root)
        initialize_library(repository, DEFAULT_REPOSITORY_NAME)
        books = self.root / "books"

        def skill(folder: Path, skill_id: str) -> None:
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                "---\n"
                f"name: {skill_id}\n"
                f"description: |\n  Apply {skill_id} when requested.\n"
                "---\n\n"
                f"# {skill_id}\n\n## Trigger\n\nUse when requested.\n\n"
                "## Boundary\n\nDo not use outside its scope.\n",
                encoding="utf-8",
            )
            (folder / "test-prompts.json").write_text("{}", encoding="utf-8")

        first = books / "first-pack"
        skill(first, "first-pack")
        skill(first / "first-a", "first-a")
        skill(first / "first-b", "first-b")
        root_skill = first / "SKILL.md"
        root_skill.write_text(
            root_skill.read_text(encoding="utf-8")
            + "\n[first-a](first-a/SKILL.md)\n",
            encoding="utf-8",
        )
        (first / "INDEX.md").write_text(
            "# First Pack — Skill Index\n\n"
            "- [first-a](./first-a/SKILL.md)\n"
            "- [first-b](./first-b/SKILL.md)\n\n"
            "```mermaid\nflowchart LR\n"
            '  A["first-a"] -->|depends-on| B["first-b"]\n'
            '  A -.->|contrasts-with| B\n```\n',
            encoding="utf-8",
        )
        second = books / "second-pack"
        skill(second / "second-a", "second-a")
        (second / "INDEX.md").write_text(
            "# Second Pack — Skill Index\n\n"
            "- [second-a](./second-a/SKILL.md)\n",
            encoding="utf-8",
        )

        discovered = discover_skill_sources(books)
        self.assertEqual([pack["id"] for pack in discovered], ["first-pack", "second-pack"])
        mother_set = {
            "first-pack",
            "first-a",
            "first-b",
            "second-a",
        }
        self.assertEqual(
            {skill_id for pack in discovered for skill_id in pack["skills"]},
            mother_set,
        )
        broken = books / "broken-pack"
        skill(broken / "only-skill", "only-skill")
        (broken / "INDEX.md").write_text(
            "# Broken Pack — Skill Index\n\n"
            "- [only-skill](./only-skill/SKILL.md)\n\n"
            "```mermaid\nflowchart LR\n"
            '  A["only-skill"] -->|depends-on| MISSING["missing-skill"]\n```\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "cannot be resolved"):
            discover_skill_sources(books)
        shutil.rmtree(broken)

        result = import_skill_source(repository, books)
        self.assertEqual(result["source_kind"], "collection")
        self.assertEqual(set(result["imported_skill_ids"]), mother_set)
        self.assertEqual(result["generated_acceptance_count"], 4)
        self.assertEqual(set(result["skill_ids"]), mother_set)
        catalog = json.loads((repository / CATALOG_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual([pack["id"] for pack in catalog["packs"]], ["first-pack", "second-pack"])
        self.assertEqual(
            catalog["packs"][0]["relations"]["contrasts-with"],
            [["first-a", "first-b"]],
        )
        self.assertIn("../first-a/SKILL.md", (repository / "first-pack" / "SKILL.md").read_text(encoding="utf-8"))
        for skill_id in mother_set:
            acceptance = json.loads(
                (repository / skill_id / "acceptance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(acceptance["generated_by"], "Skill Magnet Library Manager")
            self.assertIn("source_test_prompts_sha256", acceptance)
        self.assertTrue(source_already_registered(repository, books))
        shutil.rmtree(repository / "first-a")
        with self.assertRaisesRegex(SkillMagnetError, "登録情報と保存ファイル"):
            source_already_registered(repository, first)

    def test_existing_repository_url_is_prefilled_when_unambiguous(self) -> None:
        config = self.root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "packs": [
                        {"repo_url": "https://github.com/example/skills.git"},
                        {"repo_url": "https://github.com/example/skills.git"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            configured_repository_url(config),
            "https://github.com/example/skills.git",
        )

        config.write_text(
            json.dumps(
                {
                    "packs": [
                        {"repo_url": "https://github.com/example/one.git"},
                        {"repo_url": "https://github.com/example/two.git"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(configured_repository_url(config), "")

    def test_corrupt_or_missing_config_does_not_block_manager_repair(self) -> None:
        config = self.root / "broken-config.json"
        config.write_text("{", encoding="utf-8")
        self.assertEqual(configured_repository_url(config), "")
        notice = configuration_repair_notice(config)
        self.assertIsNotNone(notice)
        self.assertIn("Library Managerで修復", notice)

        missing = self.root / "missing-config.json"
        self.assertEqual(configured_repository_url(missing), "")
        notice = configuration_repair_notice(missing)
        self.assertIsNotNone(notice)
        self.assertIn("再作成", notice)

        for value in ([], None, {"packs": "wrong"}, {"packs": ["wrong"]}):
            with self.subTest(value=value):
                config.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(configured_repository_url(config), "")
                self.assertIsNotNone(configuration_repair_notice(config))

    def test_library_failures_keep_the_cause_and_give_a_recovery_action(self) -> None:
        missing_skill = library_failure_message(
            SkillMagnetError("選択したフォルダーにSKILL.mdがありません")
        )
        self.assertIn("原因\n選択したフォルダーにSKILL.mdがありません", missing_skill)
        self.assertIn("次の操作", missing_skill)
        self.assertIn("SKILL.mdを含むフォルダー", missing_skill)
        self.assertIn("完了扱いにしていません", missing_skill)

        github = library_failure_message(
            SkillMagnetError("GitHub remoteへのpushに失敗しました")
        )
        self.assertIn("ログイン状態", github)
        self.assertIn("途中状態は破棄していません", github)

    def test_corrupt_config_with_empty_library_offers_remote_restore(self) -> None:
        repository = self.root / "empty-managed-library"
        initialize_library(repository)
        self.assertTrue(
            remote_restore_available(
                repository,
                config_repair="設定ファイルが壊れています",
                catalog_error=None,
            )
        )
        self.assertFalse(
            remote_restore_available(
                repository,
                config_repair=None,
                catalog_error=None,
            )
        )
        self.assertTrue(
            remote_restore_available(
                repository,
                config_repair=None,
                catalog_error="catalog missing",
            )
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def git(repository: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repository, capture_output=True, text=True
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def make_library(self, name: str = DEFAULT_REPOSITORY_NAME) -> Path:
        library = self.root / name
        initialize_library(library, name)
        add_skill(
            library,
            skill_id="first-skill",
            display_name="First skill",
            purpose="Apply a bounded first operation",
            pack_id="starter-pack",
            pack_display_name="Starter pack",
        )
        return library

    def make_remote(self) -> tuple[Path, Path]:
        seed = self.make_library("seed-library")
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
        result = subprocess.run(
            ["git", "clone", "--bare", str(seed), str(remote)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return seed, remote

    @staticmethod
    def require_menu_repair(transaction: LibraryTransaction) -> None:
        journal = transaction._journal()
        journal["force_menu_update"] = True
        transaction._write_journal(journal)

    def test_init_uses_generic_repository_name_and_add_round_trips(self) -> None:
        library = self.root / "library"
        result = initialize_library(library)
        self.assertEqual(result["name"], "skill-magnet-skills")
        added = add_skill(
            library,
            skill_id="bounded-review",
            display_name="Bounded review",
            purpose="Review within an explicit boundary",
            pack_id="review-pack",
        )
        self.assertTrue(added["valid"])
        self.assertEqual(added["skill_ids"], ["bounded-review"])
        catalog = json.loads((library / CATALOG_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(catalog["repository"]["name"], "skill-magnet-skills")
        self.assertNotEqual(catalog["repository"]["name"], "bounded-review")

    def test_validation_rejects_secret_and_missing_required_description(self) -> None:
        library = self.make_library()
        skill = library / "first-skill" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "\napi_key=abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "Secret candidate"):
            validate_library(library)
        skill.write_text(
            "---\nname: first-skill\ndescription: test\n---\nNo fixed section names.\n",
            encoding="utf-8",
        )
        self.assertTrue(validate_library(library).as_dict()["valid"])
        skill.write_text(
            "---\nname: first-skill\ndescription:\n---\nInstructions.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "descriptionがありません"):
            validate_library(library)

    def test_validation_rejects_unknown_and_cycle_but_allows_contrast_in_pack(self) -> None:
        library = self.make_library()
        add_skill(
            library,
            skill_id="second-skill",
            display_name="Second skill",
            purpose="Apply a second bounded operation",
            pack_id="starter-pack",
        )
        catalog_path = library / CATALOG_FILENAME
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        relations = catalog["packs"][0]["relations"]
        relations["depends-on"] = [["first-skill", "missing-skill"]]
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "unknown skills"):
            validate_library(library)
        relations["depends-on"] = [
            ["first-skill", "second-skill"],
            ["second-skill", "first-skill"],
        ]
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(SkillMagnetError, "Dependency cycle"):
            validate_library(library)
        relations["depends-on"] = []
        relations["contrasts-with"] = [["first-skill", "second-skill"]]
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        (library / "INDEX.md").write_text(render_index(catalog), encoding="utf-8")
        self.assertTrue(validate_library(library).as_dict()["valid"])

    def test_isolated_publish_remote_verification_activation_and_retry(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "author-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)], check=True, capture_output=True
        )
        add_skill(
            draft,
            skill_id="second-skill",
            display_name="Second skill",
            purpose="Apply a second bounded operation",
            pack_id="starter-pack",
        )
        before = self.git(draft, "status", "--porcelain")
        state = self.root / "state"
        transaction = LibraryTransaction(state, "transaction-0001")
        preview = transaction.prepare(draft=draft, remote=str(remote), branch="main")
        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(self.git(draft, "status", "--porcelain"), before)
        with self.assertRaisesRegex(SkillMagnetError, "explicit confirmation"):
            transaction.publish(confirmed=False, direct=True, create_pr=False)
        published = transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.assertEqual(published["status"], "verified")
        commit = published["commit"]
        self.assertEqual(len(commit), 40)
        again = transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.assertEqual(again["commit"], commit)
        remote_count = subprocess.run(
            ["git", f"--git-dir={remote}", "rev-list", "--count", "--all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(remote_count, "2")

        old_commit = self.git(seed, "rev-parse", "HEAD")
        config_path = self.root / "skill-magnet.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["example"],
                    "state_dir": str(self.root / "runtime"),
                    "packs": [
                        {
                            "id": "starter-pack",
                            "menu_label": "Old label",
                            "selection_kind": "package",
                            "repo_url": str(remote),
                            "expected_commit": old_commit,
                            "purpose": "Old purpose",
                            "approved_by": "test",
                            "approved_at": "2026-09-02T00:00:00+00:00",
                            "skill_metadata": {
                                "first-skill": {
                                    "display_name": "First skill",
                                    "purpose": "Old purpose",
                                }
                            },
                            "skills": ["first-skill"],
                        },
                        {
                            "id": "stale-pack-from-same-library",
                            "menu_label": "Stale pack",
                            "selection_kind": "package",
                            "repo_url": str(remote),
                            "expected_commit": old_commit,
                            "purpose": "Must be removed after catalog deletion",
                            "approved_by": "test",
                            "approved_at": "2026-09-02T00:00:00+00:00",
                            "skill_metadata": {
                                "stale-skill": {
                                    "display_name": "Stale skill",
                                    "purpose": "Stale",
                                }
                            },
                            "skills": ["stale-skill"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            transaction.status(config_path)["status"], "published_but_inactive"
        )
        menu_calls: list[Path] = []
        receipt = transaction.activate(
            config_path=config_path,
            confirmed=True,
            menu_update=lambda path: menu_calls.append(path) or {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(receipt["commit"], commit)
        self.assertEqual(menu_calls, [])
        activated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(activated["packs"][0]["expected_commit"], commit)
        self.assertEqual([pack["id"] for pack in activated["packs"]], ["starter-pack"])
        self.assertEqual(
            activated["packs"][0]["skills"], ["first-skill", "second-skill"]
        )
        active_status = transaction.status(config_path)
        self.assertEqual(active_status["status"], "active")
        self.assertTrue(active_status["platform_parity"])
        self.assertEqual(
            active_status["platforms"]["windows"],
            active_status["platforms"]["macos"],
        )
        self.assertEqual(transaction.activate(config_path=config_path, confirmed=True), receipt)

    def test_activation_rebuilds_corrupt_or_missing_config_with_recoverable_backup(self) -> None:
        for mode in ("corrupt", "missing", "schema-null", "existing-backup"):
            with self.subTest(mode=mode):
                case = self.root / mode
                case.mkdir()
                seed = case / "seed"
                initialize_library(seed)
                add_skill(
                    seed,
                    skill_id=f"{mode}-skill",
                    display_name=f"{mode} skill",
                    purpose="Prove config repair",
                    pack_id=f"{mode}-pack",
                )
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
                remote = case / "remote.git"
                subprocess.run(
                    ["git", "clone", "--bare", str(seed), str(remote)],
                    check=True,
                    capture_output=True,
                )
                draft = case / "draft"
                subprocess.run(
                    ["git", "clone", str(seed), str(draft)],
                    check=True,
                    capture_output=True,
                )
                add_skill(
                    draft,
                    skill_id=f"{mode}-second",
                    display_name=f"{mode} second",
                    purpose="Force a verified change",
                    pack_id=f"{mode}-pack",
                )
                transaction = LibraryTransaction(case / "state", f"repair-{mode}")
                transaction.prepare(draft=draft, remote=str(remote), branch="main")
                transaction.publish(confirmed=True, direct=True, create_pr=False)
                config_path = case / "skill-magnet.json"
                invalid_original: bytes | None = None
                if mode in {"corrupt", "existing-backup"}:
                    invalid_original = b"{broken"
                    config_path.write_bytes(invalid_original)
                elif mode == "schema-null":
                    invalid_original = json.dumps(
                        {
                            "version": 1,
                            "allowed_github_owners": ["local"],
                            "state_dir": ".state",
                            "packs": [
                                {
                                    "id": "invalid-pack",
                                    "repo_url": str(remote),
                                    "expected_commit": "0" * 40,
                                    "skills": None,
                                }
                            ],
                        }
                    ).encode("utf-8")
                    config_path.write_bytes(invalid_original)
                if mode == "existing-backup":
                    backup = config_path.with_name(
                        f"{config_path.name}.pre-repair-repair-{mode}.bak"
                    )
                    backup.write_bytes(b"pre-existing user recovery evidence")
                    with self.assertRaisesRegex(
                        SkillMagnetError, "設定復旧バックアップが既に存在"
                    ):
                        transaction.activate(
                            config_path=config_path,
                            confirmed=True,
                            menu_update=lambda _: {"updated": True},
                        )
                    self.assertEqual(
                        backup.read_bytes(), b"pre-existing user recovery evidence"
                    )
                    self.assertEqual(config_path.read_bytes(), invalid_original)
                    continue
                receipt = transaction.activate(
                    config_path=config_path,
                    confirmed=True,
                    menu_update=lambda _: {"updated": True},
                )
                self.assertTrue(receipt["config_repaired"])
                activated = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(activated["version"], 1)
                self.assertEqual(len(activated["packs"]), 1)
                if invalid_original is not None:
                    backup = Path(str(receipt["config_repair_backup"]))
                    self.assertEqual(backup.read_bytes(), invalid_original)
                else:
                    self.assertIsNone(receipt["config_repair_backup"])

    def test_corrupt_managed_repository_can_be_restored_from_github_with_backup(self) -> None:
        _, remote = self.make_remote()
        repository = self.root / "managed"
        repository.mkdir()
        (repository / CATALOG_FILENAME).write_text("{broken", encoding="utf-8")
        result = restore_managed_repository_from_github(repository, str(remote))
        self.assertTrue(validate_library(repository).as_dict()["valid"])
        backup = Path(str(result["backup"]))
        self.assertEqual((backup / CATALOG_FILENAME).read_text(encoding="utf-8"), "{broken")

    def test_nonempty_managed_repository_without_catalog_opens_recovery_without_overwrite(
        self,
    ) -> None:
        _, remote = self.make_remote()
        repository = self.root / "managed-missing-catalog"
        repository.mkdir()
        original = repository / "unpublished-user-skill.txt"
        original.write_text("preserve me\n", encoding="utf-8")

        error = prepare_managed_repository(repository)

        self.assertIsNotNone(error)
        self.assertIn(CATALOG_FILENAME, str(error))
        self.assertEqual(original.read_text(encoding="utf-8"), "preserve me\n")
        self.assertFalse((repository / CATALOG_FILENAME).exists())

        result = restore_managed_repository_from_github(repository, str(remote))
        self.assertTrue(validate_library(repository).as_dict()["valid"])
        backup = Path(str(result["backup"]))
        self.assertEqual(
            (backup / "unpublished-user-skill.txt").read_text(encoding="utf-8"),
            "preserve me\n",
        )

    def test_catalog_directory_opens_recovery_without_overwrite(self) -> None:
        _, remote = self.make_remote()
        repository = self.root / "managed-catalog-directory"
        catalog_directory = repository / CATALOG_FILENAME
        catalog_directory.mkdir(parents=True)
        marker = catalog_directory / "unpublished-user-data.txt"
        marker.write_text("preserve directory\n", encoding="utf-8")

        error = prepare_managed_repository(repository)

        self.assertIsNotNone(error)
        self.assertIn("通常のファイルではありません", str(error))
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve directory\n")

        result = restore_managed_repository_from_github(repository, str(remote))
        self.assertTrue(validate_library(repository).as_dict()["valid"])
        backup = Path(str(result["backup"]))
        self.assertEqual(
            (backup / CATALOG_FILENAME / "unpublished-user-data.txt").read_text(
                encoding="utf-8"
            ),
            "preserve directory\n",
        )

    def test_publish_overlays_library_and_preserves_existing_repository_files(self) -> None:
        seed, remote = self.make_remote()
        (seed / "README.md").write_text("keep this documentation\n", encoding="utf-8")
        (seed / "audit").mkdir()
        (seed / "audit" / "release.json").write_text('{"keep": true}\n', encoding="utf-8")
        (seed / "legacy-skill").mkdir()
        (seed / "legacy-skill" / "SKILL.md").write_text(
            "---\nname: legacy-skill\ndescription: Existing remote skill\n---\n\n"
            "# Legacy\n\n## Trigger\n\nExisting use.\n\n## Boundary\n\nDo not modify.\n",
            encoding="utf-8",
        )
        (seed / "legacy-skill" / "acceptance.json").write_text(
            '{"version": 1, "assertions": [{"path": "result.applied", "equals": true}]}\n',
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
            "add unmanaged files",
        )
        self.git(seed, "push", str(remote), "main")
        draft = self.root / "overlay-draft"
        shutil.copytree(
            seed,
            draft,
            ignore=shutil.ignore_patterns(".git", "README.md", "audit", "legacy-skill"),
        )
        add_skill(
            draft,
            skill_id="second-skill",
            display_name="Second skill",
            purpose="Apply a second bounded operation",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(self.root / "state", "transaction-overlay")
        preview = transaction.prepare(draft=draft, remote=str(remote), branch="main")
        self.assertFalse(any(line[:2].find("D") >= 0 for line in preview["changed_files"]))
        self.assertEqual((transaction.workspace / "README.md").read_text(), "keep this documentation\n")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        checkout = self.root / "published-checkout"
        subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
        self.assertEqual((checkout / "README.md").read_text(), "keep this documentation\n")
        self.assertTrue((checkout / "audit" / "release.json").is_file())
        self.assertTrue((checkout / "legacy-skill" / "SKILL.md").is_file())

    def test_prepare_fails_closed_if_a_future_change_deletes_remote_files(self) -> None:
        seed, remote = self.make_remote()
        (seed / "README.md").write_text("must survive\n", encoding="utf-8")
        self.git(seed, "add", "README.md")
        self.git(
            seed,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "protected file",
        )
        self.git(seed, "push", str(remote), "main")
        draft = self.root / "delete-draft"
        subprocess.run(["git", "clone", str(remote), str(draft)], check=True, capture_output=True)
        original_copy = manager_module._copy_library

        def unsafe_copy(source: Path, destination: Path, managed_paths: object) -> None:
            original_copy(source, destination, managed_paths)
            (destination / "README.md").unlink()

        transaction = LibraryTransaction(self.root / "state", "transaction-delete")
        with mock.patch.object(manager_module, "_copy_library", side_effect=unsafe_copy):
            with self.assertRaisesRegex(SkillMagnetError, "既存GitHubファイルを削除"):
                transaction.prepare(draft=draft, remote=str(remote), branch="main")

    def test_delete_publishes_only_previously_cataloged_files(self) -> None:
        seed, remote = self.make_remote()
        add_skill(
            seed,
            skill_id="second-skill",
            display_name="Second skill",
            purpose="Keep the library non-empty",
            pack_id="second-pack",
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
            "add second pack",
        )
        self.git(seed, "push", str(remote), "main")
        draft = self.root / "delete-managed-draft"
        shutil.copytree(seed, draft, ignore=shutil.ignore_patterns(".git"))
        delete_pack(draft, "starter-pack", confirmed=True)
        transaction = LibraryTransaction(self.root / "state", "transaction-managed-delete")
        preview = transaction.prepare(draft=draft, remote=str(remote), branch="main")
        self.assertEqual(
            preview["deleted_managed_files"],
            ["first-skill/SKILL.md", "first-skill/acceptance.json"],
        )
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        checkout = self.root / "deleted-checkout"
        subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
        self.assertFalse((checkout / "first-skill").exists())
        self.assertTrue((checkout / "second-skill" / "SKILL.md").is_file())

    def test_recover_and_abandon_keep_user_control_after_interruption(self) -> None:
        _, remote = self.make_remote()
        draft = self.root / "recover-draft"
        subprocess.run(["git", "clone", str(remote), str(draft)], check=True, capture_output=True)
        add_skill(
            draft,
            skill_id="recovered-skill",
            display_name="Recovered",
            purpose="Recover an interrupted operation",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(self.root / "state", "transaction-recover")
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        self.assertEqual(transaction.cleanup(), [])
        recovered = transaction.recover()
        self.assertEqual(recovered["status"], "prepared")
        self.assertTrue(recovered["workspace_rebuilt"])
        self.assertTrue(transaction.workspace.is_dir())
        with self.assertRaisesRegex(SkillMagnetError, "確認"):
            transaction.abandon(confirmed=False)
        abandoned = transaction.abandon(confirmed=True)
        self.assertEqual(abandoned["status"], "abandoned")
        self.assertTrue(transaction.journal_path.is_file())
        self.assertFalse(transaction.workspace.exists())

    def test_remote_side_effects_cannot_be_abandoned_locally(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-remote")
        transaction._write_journal(
            {
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "pr_url": "https://github.com/example/skills/pull/1",
            }
        )
        with self.assertRaisesRegex(SkillMagnetError, "ローカル作業だけを破棄できません"):
            transaction.abandon(confirmed=True)

    def test_no_changes_is_terminal_and_never_publishes(self) -> None:
        _, remote = self.make_remote()
        draft = self.root / "unchanged"
        subprocess.run(["git", "clone", str(remote), str(draft)], check=True, capture_output=True)
        transaction = LibraryTransaction(self.root / "state", "transaction-unchanged")
        preview = transaction.prepare(draft=draft, remote=str(remote), branch="main")
        self.assertTrue(preview["no_changes"])
        self.assertFalse(preview["requires_confirmation"])
        self.assertEqual(transaction._journal()["status"], "verified")
        self.assertEqual(len(transaction._journal()["commit"]), 40)
        published = transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.assertEqual(published["status"], "verified")
        self.assertNotIn("pr_url", published)
        self.assertFalse(transaction.workspace.exists())

    def test_cleanup_retries_windows_access_denied_without_losing_journal(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-cleanup")
        transaction.root.mkdir(parents=True)
        transaction.verifier.mkdir()
        (transaction.verifier / "locked.idx").write_text("temporary", encoding="utf-8")
        transaction._write_journal(transaction._journal())
        original_rmtree = shutil.rmtree
        calls = 0

        def fail_once(path: Path, *args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(5, "access denied", str(path))
            original_rmtree(path, *args, **kwargs)

        with mock.patch.object(manager_module.shutil, "rmtree", side_effect=fail_once):
            self.assertEqual(transaction.cleanup(), [])
        self.assertGreaterEqual(calls, 2)
        self.assertTrue(transaction.journal_path.is_file())
        self.assertFalse(transaction.verifier.exists())

    def test_open_pull_request_is_a_wait_state_not_an_error(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-open")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "remote": "https://github.com/example/skills.git",
                "pr_url": "https://github.com/example/skills/pull/1",
                "preview": {"manifest": {"INDEX.md": "digest"}},
            }
        )
        completed = subprocess.CompletedProcess([], 0, '{"state":"OPEN","mergeCommit":null}', "")
        transaction.run = mock.Mock(return_value=completed)
        with mock.patch.object(transaction, "_remote_manifest") as remote_manifest:
            result = transaction.mark_merged()
        self.assertEqual(result["status"], "published_pending")
        self.assertEqual(result["wait_state"], "waiting_for_merge")
        remote_manifest.assert_not_called()

    def test_closed_pull_request_is_distinct_from_waiting_and_failure(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-closed")
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "remote": "https://github.com/example/skills.git",
                "pr_url": "https://github.com/example/skills/pull/1",
                "preview": {"manifest": {}},
            }
        )
        transaction.run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, '{"state":"CLOSED","mergeCommit":null}', ""
            )
        )
        with mock.patch.object(transaction, "_remote_manifest") as remote_manifest:
            result = transaction.mark_merged()
        self.assertEqual(result["wait_state"], "closed_unmerged")
        remote_manifest.assert_not_called()

    def test_merged_pull_request_verifies_the_merge_commit(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-merged")
        merge_commit = "b" * 40
        manifest = {"INDEX.md": "digest"}
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "remote": "https://github.com/example/skills.git",
                "pr_url": "https://github.com/example/skills/pull/1",
                "preview": {"manifest": manifest},
            }
        )
        transaction.run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"state": "MERGED", "mergeCommit": {"oid": merge_commit}}), ""
            )
        )
        verified = SimpleNamespace(manifest=manifest, menu_shape="menu")
        with mock.patch.object(transaction, "_remote_manifest", return_value=verified) as check:
            result = transaction.mark_merged()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["commit"], merge_commit)
        check.assert_called_once_with("https://github.com/example/skills.git", merge_commit)

    def test_automatic_merge_is_requested_once_and_then_only_polled(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-auto-merge")
        merge_commit = "b" * 40
        manifest = {"INDEX.md": "digest"}
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "remote": "https://github.com/example/skills.git",
                "pr_url": "https://github.com/example/skills/pull/1",
                "preview": {"manifest": manifest},
            }
        )
        transaction.root.mkdir(parents=True, exist_ok=True)
        transaction.run = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess(
                    [], 0, '{"state":"OPEN","mergeCommit":null}', ""
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {"state": "MERGED", "mergeCommit": {"oid": merge_commit}}
                    ),
                    "",
                ),
            ]
        )
        verified = SimpleNamespace(manifest=manifest, menu_shape="menu")
        with mock.patch.object(
            transaction, "_remote_manifest", return_value=verified
        ):
            waiting = transaction.merge_pull_request(confirmed=True)
            completed = transaction.merge_pull_request(confirmed=True)
        self.assertEqual(waiting["wait_state"], "waiting_for_merge")
        self.assertEqual(completed["status"], "verified")
        merge_commands = [
            call.args[0]
            for call in transaction.run.call_args_list
            if call.args[0][:3] == ["gh", "pr", "merge"]
        ]
        self.assertEqual(len(merge_commands), 1)
        self.assertIn("--auto", merge_commands[0])
        self.assertIn("--delete-branch", merge_commands[0])

    def test_automatic_sync_runs_every_state_to_active(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-auto-sync")
        draft = self.root / "draft-auto-sync"
        draft.mkdir()
        remote = "https://github.com/example/skills.git"
        config = self.root / "config-auto-sync.json"
        config.write_text("{}", encoding="utf-8")

        def prepare(**_: object) -> dict[str, object]:
            transaction._write_journal(
                {
                    "status": "prepared",
                    "transaction_id": transaction.transaction_id,
                    "draft": str(draft),
                    "remote": remote,
                }
            )
            return transaction._journal()

        def publish(**_: object) -> dict[str, object]:
            journal = transaction._journal()
            journal.update(status="published_pending", pr_url=remote + "/pull/1")
            transaction._write_journal(journal)
            return journal

        def merge(**_: object) -> dict[str, object]:
            journal = transaction._journal()
            journal["status"] = "verified"
            transaction._write_journal(journal)
            return journal

        receipt = {"status": "active", "transaction_id": transaction.transaction_id}
        with (
            mock.patch.object(transaction, "prepare", side_effect=prepare) as prepared,
            mock.patch.object(transaction, "publish", side_effect=publish) as published,
            mock.patch.object(
                transaction, "merge_pull_request", side_effect=merge
            ) as merged,
            mock.patch.object(transaction, "activate", return_value=receipt) as activated,
        ):
            result = transaction.complete_automatically(
                draft=draft,
                remote=remote,
                config_path=config,
                confirmed=True,
            )
        self.assertEqual(result, receipt)
        prepared.assert_called_once()
        published.assert_called_once_with(confirmed=True)
        merged.assert_called_once_with(confirmed=True)
        activated.assert_called_once()

    def test_automatic_merge_falls_back_when_repository_disables_auto_merge(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-merge-fallback")
        merge_commit = "c" * 40
        manifest = {"INDEX.md": "digest"}
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "published_pending",
                "commit": "a" * 40,
                "remote": "https://github.com/example/skills.git",
                "pr_url": "https://github.com/example/skills/pull/1",
                "preview": {"manifest": manifest},
            }
        )
        transaction.root.mkdir(parents=True, exist_ok=True)
        transaction.run = mock.Mock(
            side_effect=[
                SkillMagnetError(
                    "Command failed (gh): GraphQL: Auto merge is not allowed for this repository"
                ),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps({"state": "MERGED", "mergeCommit": {"oid": merge_commit}}),
                    "",
                ),
            ]
        )
        verified = SimpleNamespace(manifest=manifest, menu_shape="menu")
        with mock.patch.object(transaction, "_remote_manifest", return_value=verified):
            completed = transaction.merge_pull_request(confirmed=True)
        self.assertEqual(completed["status"], "verified")
        self.assertEqual(completed["merge_strategy"], "github_immediate_merge_fallback")
        commands = [call.args[0] for call in transaction.run.call_args_list]
        self.assertIn("--auto", commands[0])
        self.assertNotIn("--auto", commands[1])

    def test_custom_skill_collection_activates_as_individual_skill_actions(self) -> None:
        generated = LibraryTransaction._config_pack(
            {
                "id": "custom-skills",
                "display_name": "Custom skills",
                "purpose": "Loose skills",
                "skills": ["cma-004"],
                "skill_metadata": {
                    "cma-004": {
                        "display_name": "CMA004 — AI NEWS Podcast Audio",
                        "purpose": "Create the requested podcast",
                    }
                },
            },
            "https://github.com/example/skills.git",
            "a" * 40,
        )
        self.assertEqual(generated["selection_kind"], "skill")
        self.assertEqual(generated["skills"], ["cma-004"])

    def test_activation_does_not_reinstall_direct_root_menu_for_config_content(self) -> None:
        transaction = LibraryTransaction(self.root / "state", "transaction-menu-shape")
        remote = "https://github.com/example/skills.git"
        commit = "a" * 40
        manifest = {"INDEX.md": "digest"}
        catalog = {
            "packs": [
                {
                    "id": "custom-skills",
                    "display_name": "Custom skills",
                    "purpose": "Loose skills",
                    "skills": ["cma-004"],
                    "skill_metadata": {
                        "cma-004": {
                            "display_name": "CMA004 — AI NEWS Podcast Audio",
                            "purpose": "Create the requested podcast",
                        }
                    },
                }
            ]
        }
        transaction.root.mkdir(parents=True, exist_ok=True)
        transaction.verifier.mkdir(parents=True, exist_ok=True)
        (transaction.verifier / CATALOG_FILENAME).write_text(
            json.dumps(catalog), encoding="utf-8"
        )
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "verified",
                "remote": remote,
                "commit": commit,
                "preview": {
                    "changed_files": [],
                    "pack_ids": ["custom-skills"],
                    "skill_ids": ["cma-004"],
                },
                "remote_manifest": manifest,
            }
        )
        config = self.root / "config-menu-shape.json"
        old_pack = LibraryTransaction._config_pack(catalog["packs"][0], remote, commit)
        old_pack["selection_kind"] = "package"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["example"],
                    "state_dir": str(self.root / "runtime"),
                    "packs": [old_pack],
                }
            ),
            encoding="utf-8",
        )
        menu_updates: list[Path] = []
        verified = SimpleNamespace(manifest=manifest, menu_shape="catalog-shape")
        with (
            mock.patch.object(transaction, "_remote_manifest", return_value=verified),
            mock.patch.object(transaction, "cleanup", return_value=[]),
        ):
            result = transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=menu_updates.append,
            )
        self.assertFalse(result["menu_changed"])
        self.assertEqual(menu_updates, [])
        self.assertEqual(
            json.loads(config.read_text(encoding="utf-8"))["packs"][0]["selection_kind"],
            "skill",
        )

    def test_find_resumable_transaction_reuses_latest_matching_work(self) -> None:
        state = self.root / "state"
        draft = self.root / "draft"
        draft.mkdir()
        remote = "https://github.com/example/skills.git"
        old = LibraryTransaction(state, "transaction-old")
        old._write_journal(
            {"transaction_id": old.transaction_id, "status": "prepared", "draft": str(draft), "remote": remote}
        )
        abandoned = LibraryTransaction(state, "transaction-abandoned")
        abandoned._write_journal(
            {"transaction_id": abandoned.transaction_id, "status": "abandoned", "draft": str(draft), "remote": remote}
        )
        found = find_resumable_transaction(state, draft=draft, remote=remote)
        self.assertIsNotNone(found)
        self.assertEqual(found.transaction_id, old.transaction_id)

    def test_activation_failure_restores_previous_config(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "draft"
        subprocess.run(["git", "clone", str(seed), str(draft)], check=True, capture_output=True)
        add_skill(
            draft,
            skill_id="next-skill",
            display_name="Next",
            purpose="Apply the next bounded operation",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(self.root / "state", "transaction-rollback")
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        old_commit = self.git(seed, "rev-parse", "HEAD")
        config = self.root / "config.json"
        original = {
            "version": 1,
            "allowed_github_owners": ["example"],
            "state_dir": str(self.root / "runtime"),
            "packs": [
                {
                    "id": "starter-pack",
                    "menu_label": "Old",
                    "selection_kind": "package",
                    "repo_url": str(remote),
                    "expected_commit": old_commit,
                    "purpose": "Old",
                    "approved_by": "test",
                    "approved_at": "2026-09-02T00:00:00+00:00",
                    "skill_metadata": {
                        "first-skill": {"display_name": "First", "purpose": "Old"}
                    },
                    "skills": ["first-skill"],
                }
            ],
        }
        config.write_text(json.dumps(original), encoding="utf-8")
        original_bytes = config.read_bytes()

        def fail_menu(_: Path) -> None:
            raise RuntimeError("injected menu failure")

        with self.assertRaisesRegex(RuntimeError, "injected menu failure"):
            transaction.activate(
                config_path=config, confirmed=True, menu_update=fail_menu
            )
        self.assertEqual(config.read_bytes(), original_bytes)
        self.assertEqual(transaction.status(config)["status"], "published_but_inactive")

    def test_activation_interruption_retries_pending_menu_update(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "interrupt-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="interrupt-skill",
            display_name="Interrupt recovery",
            purpose="Recover a stopped context-menu activation",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(
            self.root / "interrupt-state", "transaction-interrupt-activation"
        )
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "interrupt-config.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "interrupt-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        original = config.read_bytes()
        attempts: list[Path] = []

        def stop_after_config(path: Path) -> None:
            attempts.append(path)
            raise KeyboardInterrupt("injected abrupt stop")

        with self.assertRaisesRegex(KeyboardInterrupt, "injected abrupt stop"):
            transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=stop_after_config,
            )

        self.assertNotEqual(config.read_bytes(), original)
        self.assertEqual(transaction._journal()["status"], "menu_pending")
        self.assertEqual(transaction.status(config)["status"], "interrupted")
        self.assertEqual(transaction.status(config)["resume_stage"], "activate")
        self.assertFalse(transaction.receipt_path.exists())

        receipt = transaction.complete_automatically(
            draft=draft,
            remote=str(remote),
            config_path=config,
            confirmed=True,
            menu_update=lambda path: attempts.append(path) or {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(attempts, [config.resolve(), config.resolve()])
        self.assertFalse((transaction.root / "activation-candidate.json").exists())
        self.assertFalse((transaction.root / "activation-previous.bin").exists())

    def test_activation_recovers_after_forced_process_exit(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "forced-exit-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="forced-exit-skill",
            display_name="Forced exit recovery",
            purpose="Resume when no Python exception handler can run",
            pack_id="starter-pack",
        )
        state = self.root / "forced-exit-state"
        transaction_id = "transaction-forced-exit"
        transaction = LibraryTransaction(state, transaction_id)
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "forced-exit.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "forced-exit-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "from skill_magnet.library_manager import LibraryTransaction; "
                    f"transaction=LibraryTransaction(Path({str(state)!r}), {transaction_id!r}); "
                    f"transaction.activate(config_path=Path({str(config)!r}), confirmed=True, "
                    "menu_update=lambda _: os._exit(73))"
                ),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(transaction._journal()["status"], "menu_pending")
        self.assertTrue((transaction.root / "activation-candidate.json").is_file())
        self.assertTrue((transaction.root / "activation-previous.bin").is_file())

        menu_calls: list[Path] = []
        receipt = transaction.complete_automatically(
            draft=draft,
            remote=str(remote),
            config_path=config,
            confirmed=True,
            menu_update=lambda path: menu_calls.append(path) or {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(menu_calls, [config.resolve()])

    def test_activation_resumes_when_stopped_between_config_and_journal(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "journal-boundary-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="journal-boundary-skill",
            display_name="Journal boundary",
            purpose="Resume after config replacement but before its journal checkpoint",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(
            self.root / "journal-boundary-state", "transaction-journal-boundary"
        )
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "journal-boundary.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "journal-boundary-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        original_write_journal = transaction._write_journal

        def stop_before_menu_pending_checkpoint(journal: dict[str, object]) -> None:
            if journal.get("status") == "menu_pending":
                raise SystemExit("injected stop before menu-pending journal")
            original_write_journal(journal)

        with (
            mock.patch.object(
                transaction,
                "_write_journal",
                stop_before_menu_pending_checkpoint,
            ),
            self.assertRaisesRegex(SystemExit, "menu-pending journal"),
        ):
            transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=lambda _: {"updated": True},
            )

        self.assertEqual(transaction._journal()["status"], "activating")
        candidate = transaction.root / "activation-candidate.json"
        self.assertEqual(config.read_bytes(), candidate.read_bytes())
        menu_calls: list[Path] = []
        receipt = transaction.complete_automatically(
            draft=draft,
            remote=str(remote),
            config_path=config,
            confirmed=True,
            menu_update=lambda path: menu_calls.append(path) or {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(menu_calls, [config.resolve()])

    def test_activation_resume_preserves_external_config_change_and_stops(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "config-drift-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="config-drift-skill",
            display_name="Config drift recovery",
            purpose="Preserve a configuration changed during interrupted activation",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(
            self.root / "config-drift-state", "transaction-config-drift"
        )
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "config-drift.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "config-drift-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        original = config.read_bytes()

        with self.assertRaises(KeyboardInterrupt):
            transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        external_config = json.loads(config.read_text(encoding="utf-8"))
        external_config["allowed_github_owners"].append("external-change")
        externally_changed = (json.dumps(external_config, sort_keys=True) + "\n").encode()
        config.write_bytes(externally_changed)
        menu_calls: list[Path] = []

        with self.assertRaisesRegex(
            SkillMagnetError, "changed after activation was interrupted"
        ):
            transaction.complete_automatically(
                draft=draft,
                remote=str(remote),
                config_path=config,
                confirmed=True,
                menu_update=menu_calls.append,
            )

        self.assertEqual(config.read_bytes(), externally_changed)
        self.assertEqual(menu_calls, [])
        journal = transaction._journal()
        conflict_backup = Path(journal["conflict_backup"])
        previous_backup = Path(journal["previous_backup"])
        self.assertEqual(journal["status"], "verified")
        self.assertEqual(journal["failed_stage"], "activation_config_conflict")
        self.assertEqual(conflict_backup.read_bytes(), externally_changed)
        self.assertEqual(previous_backup.read_bytes(), original)
        self.assertFalse((transaction.root / "activation-candidate.json").exists())
        self.assertFalse((transaction.root / "activation-previous.bin").exists())

        receipt = transaction.complete_automatically(
            draft=draft,
            remote=str(remote),
            config_path=config,
            confirmed=True,
            menu_update=lambda path: menu_calls.append(path) or {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")
        self.assertEqual(menu_calls, [config.resolve()])
        self.assertIn(
            "external-change",
            json.loads(config.read_text(encoding="utf-8"))["allowed_github_owners"],
        )

    def test_interrupted_activation_remains_visible_when_config_is_corrupt(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "corrupt-resume-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="corrupt-resume-skill",
            display_name="Corrupt resume",
            purpose="Keep an interrupted transaction visible through config damage",
            pack_id="starter-pack",
        )
        state = self.root / "corrupt-resume-state"
        transaction = LibraryTransaction(state, "transaction-corrupt-resume")
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "corrupt-resume.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "corrupt-resume-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(KeyboardInterrupt):
            transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        config.write_text("{broken", encoding="utf-8")

        listed = list_transactions(state, config)["transactions"]

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["status"], "interrupted")
        self.assertEqual(listed[0]["resume_stage"], "activate")
        self.assertIn("Cannot read JSON", listed[0]["config_error"])

    def test_activation_rollback_failure_names_real_recovery_evidence(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "rollback-evidence-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="rollback-evidence-skill",
            display_name="Rollback evidence",
            purpose="Name durable evidence when automatic rollback fails",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(
            self.root / "rollback-evidence-state", "transaction-rollback-evidence"
        )
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.require_menu_repair(transaction)
        config = self.root / "rollback-evidence.json"
        catalog = json.loads((seed / CATALOG_FILENAME).read_text(encoding="utf-8"))
        old_pack = LibraryTransaction._config_pack(
            catalog["packs"][0], str(remote), self.git(seed, "rev-parse", "HEAD")
        )
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "rollback-evidence-runtime"),
                    "packs": [old_pack],
                }
            ),
            encoding="utf-8",
        )
        original_atomic_bytes = manager_module._atomic_bytes
        config_writes = 0

        def fail_config_rollback(path: Path, value: bytes) -> None:
            nonlocal config_writes
            if path.resolve() == config.resolve():
                config_writes += 1
                if config_writes == 2:
                    raise PermissionError("injected rollback write failure")
            original_atomic_bytes(path, value)

        with (
            mock.patch.object(manager_module, "_atomic_bytes", fail_config_rollback),
            self.assertRaisesRegex(SkillMagnetError, "Recovery evidence is preserved"),
        ):
            transaction.activate(
                config_path=config,
                confirmed=True,
                menu_update=lambda _: (_ for _ in ()).throw(
                    RuntimeError("injected menu failure")
                ),
            )

        journal = transaction._journal()
        recovery_backup = Path(journal["recovery_backup"])
        self.assertEqual(journal["status"], "activating")
        self.assertEqual(journal["failed_stage"], "activation_rollback")
        self.assertEqual(recovery_backup, transaction.root / "activation-previous.bin")
        self.assertTrue(recovery_backup.is_file())

        receipt = transaction.complete_automatically(
            draft=draft,
            remote=str(remote),
            config_path=config,
            confirmed=True,
            menu_update=lambda _: {"updated": True},
        )
        self.assertEqual(receipt["status"], "active")

    def test_normal_crud_and_commit_activation_does_not_require_menu_updater(self) -> None:
        seed, remote = self.make_remote()
        draft = self.root / "no-updater-draft"
        subprocess.run(
            ["git", "clone", str(seed), str(draft)],
            check=True,
            capture_output=True,
        )
        add_skill(
            draft,
            skill_id="menu-required-skill",
            display_name="Menu required",
            purpose="Require a context-menu updater",
            pack_id="starter-pack",
        )
        transaction = LibraryTransaction(
            self.root / "no-updater-state", "transaction-no-updater"
        )
        transaction.prepare(draft=draft, remote=str(remote), branch="main")
        transaction.publish(confirmed=True, direct=True, create_pr=False)
        config = self.root / "no-updater-config.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["local"],
                    "state_dir": str(self.root / "no-updater-runtime"),
                    "packs": [],
                }
            ),
            encoding="utf-8",
        )
        receipt = transaction.activate(config_path=config, confirmed=True)

        self.assertEqual(receipt["status"], "active")
        self.assertFalse(receipt["menu_changed"])
        self.assertEqual(transaction._journal()["status"], "active")
        self.assertTrue(json.loads(config.read_text(encoding="utf-8"))["packs"])

    def test_cli_exposes_guided_library_flow(self) -> None:
        self.assertEqual(library_wizard_steps(), ("Library Manager",))
        self.assertEqual(
            library_action_label("sync"), "GitHubへ反映"
        )
        self.assertEqual(
            [library_action_label(stage) for stage in ("prepare", "publish", "open_pr", "verify", "activate")],
            [
                "送信内容を確認する",
                "GitHubへ送る",
                "GitHubでPRを開く",
                "GitHubのマージを確認する",
                "Skill Magnetへ反映",
            ],
        )
        with self.assertRaisesRegex(SkillMagnetError, "Unknown library action stage"):
            library_action_label("invalid")
        library = self.root / "cli-library"
        self.assertEqual(
            cli_main(["library", "init", "--repository", str(library)]), 0
        )
        self.assertEqual(
            cli_main(
                [
                    "library",
                    "add",
                    "--repository",
                    str(library),
                    "--skill-id",
                    "cli-skill",
                    "--display-name",
                    "CLI skill",
                    "--purpose",
                    "Apply CLI boundary",
                    "--pack-id",
                    "cli-pack",
                ]
            ),
            0,
        )
        self.assertEqual(
            cli_main(["library", "validate", "--repository", str(library)]), 0
        )

    def test_context_entry_can_preselect_library_manager_repository(self) -> None:
        selected = self.root / "selected library"
        selected.mkdir()
        with mock.patch(
            "skill_magnet.cli.show_library_manager",
            return_value={"status": "closed_without_activation"},
        ) as show:
            self.assertEqual(
                cli_main(["library", "ui", "--repository", str(selected)]), 0
            )
        self.assertEqual(show.call_args.kwargs["initial_repository"], selected)

    def test_context_entry_can_register_selected_repository(self) -> None:
        selected = self.root / "selected library"
        selected.mkdir()
        with mock.patch(
            "skill_magnet.cli.show_library_manager",
            return_value={"status": "closed_without_activation"},
        ) as show:
            self.assertEqual(
                cli_main(
                    [
                        "library",
                        "ui",
                        "--repository",
                        str(selected),
                        "--register-selected",
                    ]
                ),
                0,
            )
        self.assertEqual(show.call_args.kwargs["initial_repository"], selected)
        self.assertTrue(show.call_args.kwargs["register_selected"])

    def test_library_ui_unexpected_startup_failure_is_actionable(self) -> None:
        config = self.root / "broken config" / "skill-magnet.json"
        state = self.root / "broken state"
        for failure in (
            OSError("state directory is unavailable"),
            SkillMagnetError("Tk is required for the Skill Library Manager UI"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch(
                    "skill_magnet.cli.show_library_manager", side_effect=failure
                ),
                mock.patch("skill_magnet.cli.show_context_error") as error_ui,
            ):
                exit_code = cli_main(
                    [
                        "--config",
                        str(config),
                        "--state-dir",
                        str(state),
                        "library",
                        "ui",
                    ]
                )

            self.assertEqual(exit_code, 2)
            error_ui.assert_called_once()
            message = error_ui.call_args.args[0]
            self.assertIn(str(failure), message)
            self.assertIn("原因", message)
            self.assertIn("次の操作", message)
            self.assertIn(str(config.resolve()), message)
            self.assertIn(str(state.resolve()), message)


if __name__ == "__main__":
    unittest.main()
