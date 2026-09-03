from __future__ import annotations

import json
import shutil
import subprocess
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
    LibraryTransaction,
    add_skill,
    delete_pack,
    delete_skill,
    discover_skill_sources,
    find_resumable_transaction,
    import_skill_source,
    initialize_library,
    library_inventory,
    render_index,
    update_pack_source,
    update_skill_source,
    validate_library,
)
from skill_magnet.library_ui import (
    configured_repository_url,
    import_selected_skill,
    library_action_label,
    library_wizard_steps,
    managed_repository_path,
    register_skill_source,
    require_registration_source,
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
            "---\nname: first-skill\ndescription: invalid\n---\n\n# Invalid\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "trigger and boundary"):
            update_skill_source(repository, "first-skill", invalid)
        self.assertEqual((repository / "first-skill" / "SKILL.md").read_bytes(), before)
        validate_library(repository)

    def test_delete_rejects_dependency_and_pack_update_changes_members(self) -> None:
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
        with self.assertRaisesRegex(SkillMagnetError, "second-skill"):
            delete_skill(repository, "first-skill", confirmed=True)

        pack_source = self.root / "sources" / "first-pack"
        self.make_source_skill(pack_source, "replacement-skill")
        result = update_pack_source(repository, "first-pack", pack_source)
        self.assertEqual(result["operation"], "update_pack")
        inventory = library_inventory(repository)
        first_pack = next(pack for pack in inventory["packs"] if pack["id"] == "first-pack")
        self.assertEqual([skill["id"] for skill in first_pack["skills"]], ["replacement-skill"])
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

    def test_validation_rejects_secret_symlink_and_missing_boundaries(self) -> None:
        library = self.make_library()
        skill = library / "first-skill" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "\napi_key=abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "Secret candidate"):
            validate_library(library)
        skill.write_text(
            "---\nname: first-skill\ndescription: test\n---\nNo contract sections.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillMagnetError, "trigger and boundary"):
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
        self.assertEqual(len(menu_calls), 1)
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
        draft = self.make_library("overlay-draft")
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
        published = transaction.publish(confirmed=True, direct=True, create_pr=False)
        self.assertEqual(published["status"], "no_changes")
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

    def test_cli_exposes_guided_library_flow(self) -> None:
        self.assertEqual(library_wizard_steps(), ("Skill Library Manager",))
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


if __name__ == "__main__":
    unittest.main()
