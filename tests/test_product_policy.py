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
                "temporary_skill_materialization",
                "unverified_skill_use",
            },
        )

    def test_skill_content_is_stored_only_in_the_configured_github_repository(self) -> None:
        storage = self.policy["skill_content_storage"]
        self.assertEqual(
            storage["allowed_location"],
            "configured_user_owned_github_repository_only",
        )
        self.assertEqual(
            storage["local_storage"], "ephemeral_authoring_transaction_only"
        )
        self.assertEqual(storage["runtime_verification"], "bounded_in_memory_bytes_only")
        self.assertEqual(storage["local_metadata_json"], "allowed_without_skill_content")
        self.assertEqual(
            storage["authoring_workspace"],
            {
                "owner": "skill_magnet_product",
                "isolated": True,
                "runtime_materialization": False,
                "cleanup_after_completion": True,
            },
        )
        self.assertFalse(self.policy["legacy_persistent_sync"]["default_enabled"])
        self.assertEqual(
            self.policy["legacy_persistent_sync"]["production_use"],
            "permanently_prohibited",
        )
        self.assertFalse(self.policy["legacy_persistent_sync"]["cli_override"])

    def test_task_workspace_is_not_a_skill_store_or_temporary_area(self) -> None:
        workspace = self.policy["task_workspace"]
        self.assertEqual(
            workspace["source"], "explicit_operating_system_context_selection"
        )
        self.assertEqual(
            workspace["purpose"], "requested_work_and_output_context_only"
        )
        self.assertTrue(workspace["must_exist"])
        self.assertEqual(
            set(workspace["prohibited_runtime_skill_roots"]),
            {"~/.codex/skills", "~/.agents/skills", "~/.claude/skills"},
        )
        self.assertFalse(workspace["skill_install_or_storage"])
        self.assertFalse(workspace["temporary_storage"])

    def test_version_provenance_and_approval_are_required(self) -> None:
        provenance = self.policy["provenance"]
        self.assertTrue(provenance["required"])
        self.assertEqual(
            set(provenance["required_fields"]),
            {"repository_url", "commit_sha", "approved_by", "approved_at"},
        )

    def test_handoff_prompt_requires_real_skill_use_without_claiming_completion(self) -> None:
        handoff = self.policy["skill_use_handoff"]
        self.assertEqual(
            set(handoff["prompt_requires"]),
            {
                "read_all_skill_files_and_index_if_present",
                "apply_at_least_one_applicable_skill",
                "apply_selected_skill_rules_to_actual_work_and_deliverable",
                "obey_index_relationships_and_skill_boundaries",
                "complete_actual_request_in_one_unified_answer",
                "do_not_finish_with_skill_reading_summary_or_preparation_only",
                "do_not_restrict_output_format_beyond_request_and_skill",
            },
        )
        self.assertEqual(
            set(handoff["placement_is_not_evidence_of"]),
            {"skill_read", "skill_application"},
        )
        self.assertEqual(handoff["success_state"], "desktop_handoff_ready")
        self.assertEqual(
            set(handoff["target_applications"]),
            {"codex_desktop", "claude_code_desktop"},
        )
        self.assertFalse(handoff["answer_completion_claimed"])
        self.assertEqual(
            handoff["desktop_result_verification"], "not_claimed_by_design"
        )

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
                "approved_by",
                "approved_at",
                "skill_ids",
                "instruction_digest",
                "actual_request_sha256",
            },
        )
        self.assertEqual(
            codex["billing_boundary"],
            "existing_desktop_plan_no_api_key_or_metered_api",
        )
        self.assertFalse(codex["completion_receipt"])

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
                "explicitly_select_runtime",
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
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("windows-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("fail-fast: false", workflow)

    def test_documented_policy_is_generated_from_canonical_principles(self) -> None:
        for path in DOC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(
                    documented_principles(path),
                    self.policy["principles_ja"],
                )

    def test_library_manager_is_reachable_from_context_menu_without_auto_writes(self) -> None:
        manager = self.policy["library_manager_ui"]
        self.assertEqual(manager["entrypoint"], "skill_magnet_context_menu")
        self.assertTrue(manager["selected_folder_prefill"])
        self.assertFalse(manager["automatic_publish"])
        self.assertFalse(manager["automatic_activation"])


if __name__ == "__main__":
    unittest.main()
