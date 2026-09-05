from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from skill_magnet.core import SkillMagnetError
from skill_magnet.library_manager import (
    ExternalCommandCancelled,
    ExternalCommandTimeout,
    LibraryTransaction,
)
from skill_magnet.library_ui import start_library_background_operation


class TransactionResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def pending_transaction(self, name: str) -> LibraryTransaction:
        transaction = LibraryTransaction(self.root / "state", name)
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
        return transaction

    def inject_post_merge_checkpoint_crash(
        self, transaction: LibraryTransaction
    ) -> mock._patch:
        original = transaction._write_journal

        def write(journal: dict[str, object]) -> None:
            if journal.get("merge_requested_at"):
                raise SystemExit("injected crash after GitHub merge side effect")
            original(journal)

        return mock.patch.object(transaction, "_write_journal", side_effect=write)

    def test_auto_merge_crash_reentry_reads_state_before_any_second_merge(self) -> None:
        transaction = self.pending_transaction("auto-merge-crash")
        merge_commit = "b" * 40
        state: dict[str, object] = {"value": "OPEN", "auto_reserved": False}
        commands: list[list[str]] = []

        def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            if args[:3] == ["gh", "pr", "merge"]:
                state["auto_reserved"] = True
                return subprocess.CompletedProcess(args, 0, "", "")
            self.assertEqual(args[:3], ["gh", "pr", "view"])
            payload = {
                "state": state["value"],
                "mergeCommit": {"oid": merge_commit} if state["value"] == "MERGED" else None,
                "autoMergeRequest": (
                    {"enabledAt": "2026-09-05T00:00:00Z"}
                    if state["auto_reserved"]
                    else None
                ),
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

        transaction.run = mock.Mock(side_effect=run)
        verified = SimpleNamespace(manifest={"INDEX.md": "digest"}, menu_shape="menu")
        with (
            mock.patch.object(transaction, "_remote_manifest", return_value=verified),
            self.inject_post_merge_checkpoint_crash(transaction),
            self.assertRaisesRegex(SystemExit, "after GitHub merge"),
        ):
            transaction.merge_pull_request(confirmed=True)

        first_attempt_count = len(commands)
        with mock.patch.object(transaction, "_remote_manifest", return_value=verified):
            waiting = transaction.merge_pull_request(confirmed=True)

        self.assertEqual(waiting["status"], "published_pending")
        self.assertEqual(waiting["wait_state"], "waiting_for_merge")
        self.assertEqual(waiting["merge_strategy"], "github_auto_merge_recovered")
        state["value"] = "MERGED"
        with mock.patch.object(transaction, "_remote_manifest", return_value=verified):
            recovered = transaction.merge_pull_request(confirmed=True)

        self.assertEqual(recovered["status"], "verified")
        self.assertEqual(commands[first_attempt_count][:3], ["gh", "pr", "view"])
        self.assertEqual(
            sum(command[:3] == ["gh", "pr", "merge"] for command in commands),
            1,
        )

    def test_immediate_fallback_crash_reentry_does_not_repeat_merge(self) -> None:
        transaction = self.pending_transaction("fallback-merge-crash")
        merge_commit = "c" * 40
        state = {"value": "OPEN"}
        commands: list[list[str]] = []

        def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            if args[:3] == ["gh", "pr", "merge"] and "--auto" in args:
                raise SkillMagnetError(
                    "Command failed (gh): Auto merge is not allowed for this repository"
                )
            if args[:3] == ["gh", "pr", "merge"]:
                state["value"] = "MERGED"
                return subprocess.CompletedProcess(args, 0, "", "")
            self.assertEqual(args[:3], ["gh", "pr", "view"])
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "state": state["value"],
                        "mergeCommit": {"oid": merge_commit},
                    }
                ),
                "",
            )

        transaction.run = mock.Mock(side_effect=run)
        verified = SimpleNamespace(manifest={"INDEX.md": "digest"}, menu_shape="menu")
        with (
            mock.patch.object(transaction, "_remote_manifest", return_value=verified),
            self.inject_post_merge_checkpoint_crash(transaction),
            self.assertRaisesRegex(SystemExit, "after GitHub merge"),
        ):
            transaction.merge_pull_request(confirmed=True)

        first_attempt_count = len(commands)
        with mock.patch.object(transaction, "_remote_manifest", return_value=verified):
            recovered = transaction.merge_pull_request(confirmed=True)

        self.assertEqual(recovered["status"], "verified")
        self.assertEqual(commands[first_attempt_count][:3], ["gh", "pr", "view"])
        self.assertEqual(
            sum(command[:3] == ["gh", "pr", "merge"] for command in commands),
            2,
        )

    def test_timeout_is_finite_noninteractive_and_journaled_for_retry(self) -> None:
        transaction = self.pending_transaction("command-timeout")
        with mock.patch.dict(
            os.environ,
            {"SKILL_MAGNET_EXTERNAL_COMMAND_TIMEOUT_SECONDS": "0.05"},
        ):
            started = time.monotonic()
            with self.assertRaises(ExternalCommandTimeout):
                transaction._exec(
                    [sys.executable, "-c", "import time; time.sleep(5)"]
                )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2)
        journal = transaction._journal()
        self.assertEqual(journal["status"], "published_pending")
        self.assertTrue(journal["failed_stage"].endswith("command_timeout"))
        self.assertEqual(journal["recovery_action"], "retry_same_transaction")
        self.assertEqual(journal["command_timeout_seconds"], 0.05)
        self.assertIn("再試行", journal["last_error"])

    def test_external_process_receives_noninteractive_environment(self) -> None:
        transaction = self.pending_transaction("noninteractive-environment")
        code = (
            "import json,os; print(json.dumps({k:os.environ.get(k) for k in "
            "['GIT_TERMINAL_PROMPT','GCM_INTERACTIVE','GH_PROMPT_DISABLED',"
            "'GH_NO_UPDATE_NOTIFIER','SSH_ASKPASS_REQUIRE']}))"
        )
        completed = transaction._exec([sys.executable, "-c", code])
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "GH_PROMPT_DISABLED": "1",
                "GH_NO_UPDATE_NOTIFIER": "1",
                "SSH_ASKPASS_REQUIRE": "never",
            },
        )

    def test_close_cancellation_stops_child_and_records_recovery(self) -> None:
        cancel = threading.Event()
        transaction = LibraryTransaction(
            self.root / "state", "cancel-running-command", cancel_event=cancel
        )
        transaction._write_journal(
            {
                "schema_version": 1,
                "transaction_id": transaction.transaction_id,
                "status": "publishing",
            }
        )
        started = threading.Event()

        def operation(_: threading.Event) -> None:
            started.set()
            transaction._exec([sys.executable, "-c", "import time; time.sleep(5)"])

        event, worker, outcome = start_library_background_operation(
            operation,
            name="test-library-close-cancel",
            cancel_event=cancel,
        )
        self.assertTrue(started.wait(1))
        event.set()
        worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(outcome.get("error"), ExternalCommandCancelled)
        journal = transaction._journal()
        self.assertTrue(journal["failed_stage"].endswith("command_cancelled"))
        self.assertEqual(journal["recovery_action"], "retry_same_transaction")

    def test_background_operation_returns_before_slow_work_finishes(self) -> None:
        release = threading.Event()

        def operation(_: threading.Event) -> str:
            release.wait(2)
            return "done"

        started = time.monotonic()
        _, worker, outcome = start_library_background_operation(
            operation, name="test-library-ui-responsive"
        )
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(1)
        self.assertEqual(outcome, {"value": "done"})


if __name__ == "__main__":
    unittest.main()
