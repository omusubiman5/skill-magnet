from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import subprocess
import sys
import time
import shutil

from tests.e2e_guard import E2ECycleTeardown, ProcessRecord, assert_e2e_clean


class E2EResidualGuardTest(unittest.TestCase):
    def test_guard_covers_every_terminal_path_and_rejects_exact_residuals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_root = Path(temporary) / ".e2e-target"
            target_root.mkdir()
            for terminal_path, process_name in (
                ("success", "codex.exe"),
                ("rejected", "pythonw.exe"),
                ("failure", "cmd.exe"),
                ("interruption", "codex.exe"),
            ):
                with self.subTest(path=terminal_path):
                    exact = f'{process_name} --skill-magnet-e2e {terminal_path}'
                    assert_e2e_clean(
                        self,
                        target_root=target_root,
                        exact_command_lines=[exact],
                    )
                    with self.assertRaises(AssertionError):
                        assert_e2e_clean(
                            self,
                            target_root=target_root,
                            exact_command_lines=[exact],
                            records=[ProcessRecord(41, 1, process_name, exact)],
                        )

    def test_guard_rejects_any_e2e_target_child_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_root = Path(temporary) / ".e2e-target"
            (target_root / "leftover").mkdir(parents=True)
            with self.assertRaises(AssertionError):
                assert_e2e_clean(
                    self,
                    target_root=target_root,
                    exact_command_lines=[],
                    records=[],
                )

    @unittest.skipUnless(sys.platform == "win32", "real Windows residual injection")
    def test_four_terminal_paths_reject_live_real_process_residuals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sleeper = root / "sleeper.py"
            sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
            codex_probe = root / "codex.exe"
            shutil.copy2(sys.executable, codex_probe)
            shutil.copy2(Path(sys.base_prefix) / "python312.dll", root / "python312.dll")
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            commands = {
                "success": [str(codex_probe), str(sleeper), "success-marker"],
                "rejected": [str(pythonw), str(sleeper), "rejected-marker"],
                "failure": ["cmd.exe", "/d", "/s", "/c", "ping -n 30 127.0.0.1 >nul & rem failure-marker"],
                "interruption": [str(codex_probe), str(sleeper), "interruption-marker"],
            }
            for outcome, command in commands.items():
                with self.subTest(outcome=outcome):
                    target_root = root / outcome / ".e2e-target"
                    target_root.mkdir(parents=True)
                    process = subprocess.Popen(
                        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    teardown = E2ECycleTeardown(self, target_root=target_root)
                    exact = subprocess.list2cmdline(command)
                    teardown.track_process(process.pid, [exact])
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        try:
                            assert_e2e_clean(
                                self,
                                target_root=target_root,
                                exact_command_lines=[exact],
                                root_process_ids=[process.pid],
                            )
                        except AssertionError:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail(f"{outcome} live residual was not observable")
                    with self.assertRaises(AssertionError):
                        assert_e2e_clean(
                            self,
                            target_root=target_root,
                            exact_command_lines=[exact],
                            root_process_ids=[process.pid],
                        )
                    teardown.finish()
                    process.wait(timeout=5)

    @unittest.skipUnless(sys.platform == "win32", "real Windows process-tree guard")
    def test_real_child_process_and_distinct_descendant_argv_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_root = root / ".e2e-target"
            target = target_root / "owned"
            target.mkdir(parents=True)
            sleeper = root / "sleeper.py"
            sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
            codex_probe = root / "codex.exe"
            shutil.copy2(sys.executable, codex_probe)
            shutil.copy2(Path(sys.base_prefix) / "python312.dll", root / "python312.dll")
            command = ["cmd.exe", "/d", "/s", "/c", str(codex_probe), str(sleeper)]
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            teardown = E2ECycleTeardown(self, target_root=target_root)
            teardown.own_target(target)
            teardown.track_process(process.pid, [subprocess.list2cmdline(command)])
            deadline = time.monotonic() + 5
            while len(teardown.seen_process_ids) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
                teardown.capture_process_tree()
            self.assertGreaterEqual(len(teardown.seen_process_ids), 2)
            with self.assertRaises(AssertionError):
                assert_e2e_clean(
                    self,
                    target_root=target_root,
                    exact_command_lines=teardown.command_lines,
                    root_process_ids=teardown.root_process_ids,
                )
            teardown.finish()
            process.wait(timeout=5)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
