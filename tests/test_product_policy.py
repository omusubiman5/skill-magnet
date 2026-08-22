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
        self.assertEqual(
            activation["trigger"], "manual_user_selection_in_application"
        )
        self.assertEqual(activation["default_active_packs"], 0)
        self.assertEqual(activation["selection"], "explicit_single_pack")
        self.assertEqual(activation["runtime_selection"], "explicit")
        self.assertEqual(
            set(activation["prohibited_defaults"]),
            {
                "all_packs",
                "persistent_install",
                "implicit_sync",
                "automatic_suggestion",
                "automatic_distribution",
                "automatic_activation",
                "local_placement_as_activation",
                "unverified_skill_use",
            },
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

    def test_placement_is_not_skill_use_and_missing_evidence_fails_closed(self) -> None:
        verification = self.policy["skill_use_verification"]
        self.assertEqual(
            set(verification["success_requires_all"]),
            {
                "task_delivery_evidence",
                "skill_read_evidence",
                "skill_specific_application_evidence",
            },
        )
        self.assertEqual(
            set(verification["placement_is_not_evidence_of"]),
            {"skill_read", "skill_application"},
        )
        self.assertFalse(verification["self_report_alone_is_sufficient"])
        self.assertEqual(verification["missing_evidence_result"], "fail_closed")

    def test_codex_defaults_to_explicit_task_injection_not_temp_placement(self) -> None:
        codex = self.policy["codex"]
        self.assertEqual(codex["default_route"], "explicit_task_injection")
        self.assertFalse(codex["temporary_native_skill_placement_default"])
        self.assertFalse(
            codex["session_only_native_skill_addition_officially_confirmed"]
        )
        self.assertEqual(
            set(codex["inject_required"]),
            {
                "pack_id",
                "repository_url",
                "commit_sha",
                "skill_ids",
                "instruction_digest",
            },
        )

    def test_scope_and_completion_require_both_deliverables_and_real_use(self) -> None:
        self.assertEqual(
            set(self.policy["product_scope"]["deliverables"]),
            {"skill_magnet_application", "separate_user_owned_skill_repository"},
        )
        completion = self.policy["completion"]
        self.assertTrue(completion["requires_end_to_end_automated_test"])
        self.assertTrue(completion["test_must_use_both_deliverables"])
        self.assertFalse(completion["local_placement_only_is_complete"])

    def test_context_menu_flow_is_manual_and_cross_platform(self) -> None:
        ui = self.policy["launch_ui"]
        self.assertEqual(ui["entrypoint"], "operating_system_context_menu")
        self.assertFalse(ui["automatic_execution"])
        self.assertEqual(
            ui["required_flow"],
            [
                "open_context_menu",
                "choose_skill_magnet",
                "explicitly_select_pack",
                "confirm_target_version_and_purpose",
                "launch",
            ],
        )
        self.assertEqual(
            set(ui["supported_platforms"]),
            {"windows_explorer", "macos_finder"},
        )
        self.assertTrue(ui["semantic_and_safety_parity_required"])
        self.assertFalse(ui["platform_menu_visual_parity_required"])
        completion = self.policy["completion"]
        self.assertTrue(completion["requires_all_supported_platform_adapters"])
        self.assertFalse(completion["single_platform_is_complete"])

    def test_documented_policy_is_generated_from_canonical_principles(self) -> None:
        for path in DOC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(
                    documented_principles(path),
                    self.policy["principles_ja"],
                )


if __name__ == "__main__":
    unittest.main()
