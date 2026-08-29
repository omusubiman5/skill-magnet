from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from integration.explorer_results_gate import parse_ledger, validate_consistency

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "windows-explorer-leaf-launch-results.md"


class ExplorerResultsGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = RESULTS.read_text(encoding="utf-8")
        self.count = unittest.defaultTestLoader.discover(str(ROOT / "tests")).countTestCases()

    def validate(self, text: str, count: int | None = None) -> list[str]:
        return validate_consistency(
            text, observed_test_count=self.count if count is None else count,
            observed_leaf_count=1, observed_selection_kinds=["package"],
            observed_pack_skill_count=9)

    def test_canonical_results_are_consistent(self) -> None:
        self.assertEqual(self.validate(self.text), [])
        self.assertEqual(parse_ledger(self.text)["release_scope"], "one-package-leaf")

    def test_gate_rejects_counts_menu_shape_and_stale_claims(self) -> None:
        self.assertTrue(self.validate(self.text, self.count + 1))
        self.assertTrue(self.validate(self.text.replace('"menu_leaf_count": 1', '"menu_leaf_count": 18')))
        self.assertTrue(self.validate(self.text + "\n固定9 skills × Codex の18個別leaf\n"))
        self.assertTrue(
            self.validate(
                self.text.replace(
                    parse_ledger(self.text)["release_code_sha"], "not-a-commit"
                )
            )
        )

    def test_cli_returns_nonzero_for_observed_mismatch(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "integration" / "explorer_results_gate.py"),
             str(RESULTS), "--observed-test-count", str(self.count + 1)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("full_test_count mismatch", completed.stdout)


if __name__ == "__main__":
    unittest.main()
