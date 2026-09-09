from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.activation import (
    _AcceptanceFailed, _CleanupFailed, _LaunchFailed, _OutputFailed, _RuntimeFailed,
)
from skill_magnet.core import Config, SkillMagnetError
from skill_magnet.library_ui import library_failure_message
from skill_magnet.ui import context_failure_message, context_failure_surface


class FailureDiagnosticsTest(unittest.TestCase):
    def test_permission_error_through_real_config_loader_keeps_os_recovery(self) -> None:
        config = Path("C:/example/skill-magnet.json")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "Permission denied", str(config))):
            try:
                Config.load(config)
            except SkillMagnetError as error:
                self.assertIsInstance(error.__cause__, PermissionError)
                for message in (context_failure_message(error), library_failure_message(error)):
                    self.assertIn("SM-E302", message)
                    self.assertIn("アクセス権限", message)
                    self.assertIn(str(config), message)
                    self.assertNotIn("GitHubへ反映", message)
                    self.assertNotIn("GitHub URL", message)
            else:
                self.fail("The actual config loader must propagate the failure")

    def test_permission_errors_are_not_reclassified_by_path(self) -> None:
        for path in ("config.json", "SKILL.md", "github/remote", "folder/transaction"):
            with self.subTest(path=path):
                error = PermissionError(13, "Permission denied", path)
                surface = context_failure_surface(error)
                self.assertEqual(surface["code"], "SM-E302 (PERMISSION_DENIED)")
                for message in (context_failure_message(error), library_failure_message(error)):
                    self.assertIn("SM-E302", message)
                    self.assertIn("PermissionError", message)
                    self.assertIn(path, message)
                    self.assertIn("アクセス権限", message)
                    self.assertNotIn("GitHub URL", message)
                    self.assertNotIn("GitHubへ反映", message)

    def test_missing_file_and_os_errors_keep_type_and_os_details(self) -> None:
        for error, code, action in (
            (FileNotFoundError(2, "No such file", "config.json"), "SM-E301", "存在するか"),
            (OSError(28, "No space left", "github/config.json"), "SM-E303", "OSエラー番号"),
        ):
            with self.subTest(code=code):
                for message in (context_failure_message(error), library_failure_message(error)):
                    self.assertIn(code, message)
                    self.assertIn(str(error), message)
                    self.assertIn(type(error).__name__, message)
                    self.assertIn(action, message)
                    self.assertNotIn("GitHubへ反映", message)

    def test_launch_failure_keeps_underlying_path_and_permission_recovery(self) -> None:
        try:
            try:
                raise PermissionError(13, "Permission denied", "runtime.exe")
            except PermissionError as cause:
                raise _LaunchFailed("Runtime could not be started") from cause
        except _LaunchFailed as error:
            message = context_failure_message(error)
        self.assertEqual(message.count("SM-E101"), 1)
        self.assertIn("_LaunchFailed: Runtime could not be started", message)
        self.assertIn("PermissionError", message)
        self.assertIn("runtime.exe", message)
        self.assertIn("アクセス権限", message)

    def test_typed_failures_preserve_specific_diagnostics(self) -> None:
        cases = (
            (_LaunchFailed("executable missing"), "executable missing"),
            (_AcceptanceFailed("digest mismatch: expected abc, observed def"), "expected abc, observed def"),
            (_OutputFailed("result.task_output missing"), "result.task_output missing"),
            (_CleanupFailed((Path("remaining-artifact.json"),)), "remaining-artifact.json"),
        )
        for error, detail in cases:
            with self.subTest(error=type(error).__name__):
                message = context_failure_message(error)
                self.assertIn(detail, message)
                self.assertIn(type(error).__name__, message)
                self.assertNotEqual(context_failure_surface(error)["state"], "success")

    def test_runtime_failure_shows_existing_sanitized_diagnostic(self) -> None:
        error = _RuntimeFailed(exit_code=7, stderr="authentication failed private-marker")
        message = context_failure_message(error)
        self.assertIn(json.dumps(error.diagnostic, ensure_ascii=False), message)
        self.assertIn("authentication", message)
        self.assertNotIn("private-marker", message)

    def test_actual_config_failure_still_offers_config_repair(self) -> None:
        error = SkillMagnetError("config JSON is invalid")
        self.assertEqual(context_failure_surface(error)["code"], "SM-E203 (CONFIG_INVALID)")
        self.assertIn("GitHub URL", context_failure_surface(error)["next_action"])
        self.assertIn("GitHubへ反映", library_failure_message(error))


if __name__ == "__main__":
    unittest.main()
