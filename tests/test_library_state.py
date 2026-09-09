from pathlib import Path
import tempfile
import unittest

from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import LibraryTransaction
from skill_magnet.library_state import LibraryState
from skill_magnet.library_ui import automatic_sync_next_stage


class LibraryStateTest(unittest.TestCase):
    def test_remote_work_is_not_discarded_even_without_a_commit_field(self):
        # Acceptance rule: once sending/activation may have started, metadata
        # alone must keep the operation recoverable; missing content is not consent.
        for status in ("publishing", "published_pending", "verified", "activating", "menu_pending", "active"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                current = LibraryTransaction(Path(temporary), "transaction-state-contract")
                current._write_journal({"status": status, "draft_unavailable": True})
                before = current.journal_path.read_bytes()
                state = LibraryState.from_journal(current._journal())
                self.assertFalse(state.can_abandon)
                self.assertFalse(state.needs_reselection)
                with self.assertRaisesRegex(SkillMagnetError, "送信済み"):
                    current.abandon(confirmed=True)
                self.assertEqual(current.journal_path.read_bytes(), before)

    def test_lost_unsent_work_can_be_reselected_after_explicit_discard(self):
        for status in ("draft", "prepared", "preparing", "interrupted"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                current = LibraryTransaction(Path(temporary), "transaction-local-contract")
                current._write_journal({"status": status, "draft_unavailable": True})
                state = LibraryState.from_journal(current._journal())
                self.assertTrue(state.needs_reselection)
                with self.assertRaisesRegex(SkillMagnetError, "確認"):
                    current.abandon(confirmed=False)
                self.assertEqual(current.abandon(confirmed=True)["status"], "abandoned")

    def test_recorded_remote_effect_overrides_an_earlier_local_status(self):
        for reference in ({"commit": "a" * 40}, {"pr_url": "https://github.com/example/skills/pull/1"}, {"resume_status": "publishing"}):
            with self.subTest(reference=reference):
                state = LibraryState.from_journal({"status": "interrupted", "draft_unavailable": True, **reference})
                self.assertFalse(state.can_abandon)
                self.assertFalse(state.can_start_edit)
                self.assertFalse(state.needs_reselection)

    def test_closed_pr_requires_user_action_and_is_not_polled_forever(self):
        self.assertEqual(automatic_sync_next_stage({"status": "published_pending", "wait_state": "closed_unmerged"}), "reopen_pr")
        self.assertEqual(automatic_sync_next_stage({"status": "published_pending", "wait_state": "waiting_for_merge"}), "waiting")
        self.assertEqual(automatic_sync_next_stage({"status": "active"}), "complete")

    def test_unknown_states_cannot_authorize_new_work(self):
        for journal in ({"status": "unexpected"}, {"status": "interrupted", "resume_status": "unexpected"}):
            with self.subTest(journal=journal), self.assertRaises(SkillMagnetError):
                LibraryState.from_journal(journal)
