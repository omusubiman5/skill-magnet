from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill_magnet.cli import main as cli_main
from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LibraryTransaction,
    add_skill,
    initialize_library,
    validate_library,
)
from skill_magnet.library_ui import library_wizard_steps


class LibraryManagerTests(unittest.TestCase):
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

    def test_validation_rejects_unknown_cycle_and_contrast(self) -> None:
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
        with self.assertRaisesRegex(SkillMagnetError, "contrasting skills"):
            validate_library(library)

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
                        }
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
        self.assertEqual(len(library_wizard_steps()), 7)
        self.assertEqual(library_wizard_steps()[0], "1. Repository")
        self.assertEqual(library_wizard_steps()[-1], "7. Activate & Receipt")
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
