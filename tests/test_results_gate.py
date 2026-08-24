from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from integration.explorer_results_gate import parse_ledger, validate_consistency


RESULTS = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "windows-explorer-leaf-launch-results.md"
)


class ExplorerResultsGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = RESULTS.read_text(encoding="utf-8")
        self.ledger = parse_ledger(self.text)
        self.statuses = dict(self.ledger["beads"])
        self.metadata = dict(self.ledger["bead_metadata"])
        self.residual = dict(self.ledger["blocked_residual"])
        self.count = unittest.defaultTestLoader.discover(
            str(Path(__file__).resolve().parent)
        ).countTestCases()

    def test_canonical_results_are_consistent(self) -> None:
        self.assertEqual(
            validate_consistency(
                self.text,
                observed_test_count=self.count,
                bead_statuses=self.statuses,
                bead_metadata=self.metadata,
                observed_blocked_residual=self.residual,
            ),
            [],
        )

    def test_gate_rejects_count_matrix_and_beads_disagreement(self) -> None:
        self.assertTrue(
            validate_consistency(
                self.text,
                observed_test_count=self.count + 1,
                bead_statuses=self.statuses,
                bead_metadata=self.metadata,
                observed_blocked_residual=self.residual,
            )
        )
        changed_summary = self.text.replace(
            f"— {self.count} tests PASS", f"— {self.count - 1} tests PASS", 1
        )
        self.assertTrue(
            validate_consistency(
                changed_summary,
                observed_test_count=self.count,
                bead_statuses=self.statuses,
                bead_metadata=self.metadata,
                observed_blocked_residual=self.residual,
            )
        )
        changed_matrix = self.text.replace(
            "| `SM-INT-001` | pack/skill menu完全性 | `PASS_AUTOMATED` |",
            "| `SM-INT-001` | pack/skill menu完全性 | `FAIL` |",
            1,
        )
        self.assertTrue(
            validate_consistency(
                changed_matrix,
                observed_test_count=self.count,
                bead_statuses=self.statuses,
                bead_metadata=self.metadata,
                observed_blocked_residual=self.residual,
            )
        )
        changed_beads = dict(self.statuses)
        first_issue = next(iter(changed_beads))
        changed_beads[first_issue] = "open"
        self.assertTrue(
            validate_consistency(
                self.text,
                observed_test_count=self.count,
                bead_statuses=changed_beads,
                bead_metadata=self.metadata,
                observed_blocked_residual=self.residual,
            )
        )

    def test_gate_rejects_blocker_detail_os_and_metadata_disagreement(self) -> None:
        changed_detail = self.text.replace('"appx_count": 0', '"appx_count": 1', 1)
        self.assertTrue(validate_consistency(changed_detail, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=self.residual))
        changed_os = dict(self.residual)
        changed_os["target_dir"] = not bool(self.residual["target_dir"])
        self.assertTrue(validate_consistency(self.text, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=changed_os))
        changed_metadata_text = self.text.replace(
            '"bead_metadata": {}',
            '"bead_metadata": {"sm-62a.6.7": {"sentinel": "expected"}}',
            1,
        )
        self.assertNotEqual(changed_metadata_text, self.text)
        self.assertTrue(validate_consistency(changed_metadata_text, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=self.residual))

    def test_gate_rejects_stale_canonical_and_residual_prose(self) -> None:
        stale_status = (
            "in_progress `.6.7`"
            if self.statuses["sm-62a.6.7"] == "blocked"
            else "blocked `.6.7`"
        )
        stale_canonical = self.text + f"\n{stale_status}\n"
        self.assertTrue(validate_consistency(stale_canonical, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=self.residual))
        stale_audit = self.text + "\nsm-62a.7 未実行\n"
        self.assertTrue(validate_consistency(stale_audit, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=self.residual))
        stale_target = self.text + "\ntarget一件保持\n"
        self.assertTrue(validate_consistency(stale_target, observed_test_count=self.count, bead_statuses=self.statuses, bead_metadata=self.metadata, observed_blocked_residual=self.residual))

    def test_cli_explicit_count_returns_canonical_mismatch_not_repository_crash(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(RESULTS.parents[1] / "integration" / "explorer_results_gate.py"),
                str(RESULTS),
                "--observed-test-count",
                str(self.count + 1),
            ],
            cwd=RESULTS.parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("UnboundLocalError", completed.stderr)
        self.assertIn("test count mismatch", completed.stdout)


if __name__ == "__main__":
    unittest.main()
