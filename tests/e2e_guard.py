from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


TRACKED_PROCESS_NAMES = frozenset({"pythonw.exe", "codex.exe", "cmd.exe"})


@dataclass(frozen=True)
class ProcessRecord:
    process_id: int
    parent_process_id: int
    name: str
    command_line: str


def _normalized_command_line(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def exact_command_line_residuals(
    records: Iterable[ProcessRecord], expected_command_lines: Iterable[str]
) -> list[ProcessRecord]:
    expected = {_normalized_command_line(value) for value in expected_command_lines}
    return [
        record
        for record in records
        if record.name.casefold() in TRACKED_PROCESS_NAMES
        and _normalized_command_line(record.command_line) in expected
    ]


def descendant_residuals(
    records: Iterable[ProcessRecord], root_process_ids: Iterable[int]
) -> list[ProcessRecord]:
    rows = list(records)
    descendants = set(root_process_ids)
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.parent_process_id in descendants and row.process_id not in descendants:
                descendants.add(row.process_id)
                changed = True
    return [row for row in rows if row.process_id in descendants]


def windows_process_records() -> list[ProcessRecord]:
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object {$_.Name -in @('pythonw.exe','codex.exe','cmd.exe')} | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if not completed.stdout.strip():
        return []
    payload = json.loads(completed.stdout)
    rows = payload if isinstance(payload, list) else [payload]
    return [
        ProcessRecord(
            process_id=int(row["ProcessId"]),
            parent_process_id=int(row.get("ParentProcessId") or 0),
            name=str(row["Name"]),
            command_line=str(row.get("CommandLine") or ""),
        )
        for row in rows
    ]


def assert_e2e_clean(
    testcase: object,
    *,
    target_root: Path,
    exact_command_lines: Sequence[str],
    root_process_ids: Sequence[int] = (),
    records: Iterable[ProcessRecord] | None = None,
) -> None:
    child_directories = (
        sorted(path.name for path in target_root.iterdir() if path.is_dir())
        if target_root.is_dir()
        else []
    )
    testcase.assertEqual(child_directories, [], ".e2e-target child directories remain")
    residuals = exact_command_line_residuals(
        windows_process_records() if records is None else records,
        exact_command_lines,
    )
    testcase.assertEqual(
        residuals,
        [],
        "exact-command-line pythonw/codex/cmd residuals remain",
    )
    process_rows = windows_process_records() if records is None else list(records)
    descendants = descendant_residuals(process_rows, root_process_ids)
    testcase.assertEqual(descendants, [], "tracked PID/descendant residuals remain")


class E2ECycleTeardown:
    """Own removal and process-tree cleanup for one isolated E2E cycle."""

    def __init__(self, testcase: object, *, target_root: Path) -> None:
        self.testcase = testcase
        self.target_root = target_root.resolve()
        self.targets: list[Path] = []
        self.root_process_ids: list[int] = []
        self.command_lines: list[str] = []
        self.seen_process_ids: set[int] = set()

    def own_target(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.target_root:
            raise AssertionError(f"E2E target is outside owned root: {resolved}")
        self.targets.append(resolved)

    def track_process(self, process_id: int, command_lines: Sequence[str]) -> None:
        self.root_process_ids.append(process_id)
        self.command_lines.extend(command_lines)
        self.capture_process_tree()

    def capture_process_tree(self) -> None:
        rows = windows_process_records()
        for row in descendant_residuals(rows, self.root_process_ids):
            self.seen_process_ids.add(row.process_id)
            if row.name.casefold() in TRACKED_PROCESS_NAMES and row.command_line:
                self.command_lines.append(row.command_line)

    def finish(self) -> None:
        self.capture_process_tree()
        # Terminate only PIDs observed in the test-owned process tree.
        for process_id in sorted(self.seen_process_ids, reverse=True):
            subprocess.run(
                ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        for target in self.targets:
            if target.exists():
                shutil.rmtree(target)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rows = windows_process_records()
            if not descendant_residuals(rows, self.seen_process_ids) and not exact_command_line_residuals(
                rows, self.command_lines
            ):
                break
            time.sleep(0.05)
        assert_e2e_clean(
            self.testcase,
            target_root=self.target_root,
            exact_command_lines=self.command_lines,
            root_process_ids=tuple(self.seen_process_ids),
        )
