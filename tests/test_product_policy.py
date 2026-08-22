from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "policy" / "product-policy.json"
DOC_PATHS = (ROOT / "README.md", ROOT / "docs" / "mvp-redesign.md")


def documented_principles(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    start_marker = "<!-- product-policy:begin -->"
    end_marker = "<!-- product-policy:end -->"
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    return [
        line.removeprefix("- ")
        for line in text[start:end].splitlines()
        if line.startswith("- ")
    ]


class ProductPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def test_policy_is_github_source_of_truth_and_explicit_opt_in(self) -> None:
        self.assertTrue(self.policy["source_of_truth"]["sole"])
        self.assertEqual(
            self.policy["source_of_truth"]["kind"],
            "user_owned_github_repository",
        )
        activation = self.policy["activation"]
        self.assertEqual(activation["default_active_packs"], 0)
        self.assertEqual(activation["selection"], "explicit_single_pack")
        self.assertEqual(activation["runtime_selection"], "explicit")
        self.assertEqual(
            set(activation["prohibited_defaults"]),
            {"all_packs", "persistent_install", "implicit_sync"},
        )

    def test_temporary_materialization_requires_expiry_and_cleanup(self) -> None:
        temporary = self.policy["temporary_materialization"]
        self.assertTrue(temporary["allowed_only_when_runtime_requires"])
        self.assertEqual(
            set(temporary["required_fields"]),
            {"target", "reason", "expires_at", "cleanup_plan"},
        )
        self.assertEqual(temporary["required_final_state"], "no_residue")
        self.assertFalse(self.policy["legacy_persistent_sync"]["default_enabled"])

    def test_version_provenance_and_approval_are_required(self) -> None:
        provenance = self.policy["provenance"]
        self.assertTrue(provenance["required"])
        self.assertEqual(
            set(provenance["required_fields"]),
            {"repository_url", "commit_sha", "approved_by", "approved_at"},
        )

    def test_documented_policy_is_generated_from_canonical_principles(self) -> None:
        for path in DOC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(
                    documented_principles(path),
                    self.policy["principles_ja"],
                )


if __name__ == "__main__":
    unittest.main()
