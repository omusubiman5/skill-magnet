from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Mapping


LEDGER_START = "<!-- explorer-results-ledger:start"
LEDGER_END = "explorer-results-ledger:end -->"
BLOCKER_START = "<!-- explorer-blocker-readback:start"
BLOCKER_END = "explorer-blocker-readback:end -->"
MATRIX_ROW = re.compile(
    r"^\| `(?P<id>SM-INT-\d{3})` \|.*?\| `(?P<status>[^`]+)` \|",
    re.MULTILINE,
)
AGGREGATE_TEST_COUNT = re.compile(
    r"^- 統合テスト: .*?— (?P<count>\d+) tests PASS$", re.MULTILINE
)
SUMMARY_COUNT = re.compile(
    r"統合テスト: `[^`]+` — (?P<count>\d+) tests PASS"
)


def parse_ledger(text: str) -> dict[str, object]:
    start = text.find(LEDGER_START)
    end = text.find(LEDGER_END)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("results ledger markers are missing or out of order")
    payload = text[start + len(LEDGER_START) : end].strip()
    return json.loads(payload)


def parse_blocker_readback(text: str) -> dict[str, object]:
    start = text.find(BLOCKER_START)
    end = text.find(BLOCKER_END)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("blocked residual readback markers are missing or out of order")
    return json.loads(text[start + len(BLOCKER_START) : end].strip())


def parse_detailed_matrix(text: str) -> dict[str, str]:
    rows = {match["id"]: match["status"] for match in MATRIX_ROW.finditer(text)}
    if not rows:
        raise ValueError("detailed Explorer matrix has no SM-INT rows")
    return rows


def validate_consistency(
    text: str,
    *,
    observed_test_count: int,
    bead_statuses: Mapping[str, str],
    bead_metadata: Mapping[str, Mapping[str, object]] | None = None,
    observed_blocked_residual: Mapping[str, object] | None = None,
) -> list[str]:
    ledger = parse_ledger(text)
    errors: list[str] = []
    summary = SUMMARY_COUNT.search(text)
    summary_count = int(summary["count"]) if summary else None
    if summary_count != observed_test_count:
        errors.append(
            "summary test count mismatch: "
            f"summary={summary_count!r}, observed={observed_test_count}"
        )
    if ledger.get("full_test_count") != observed_test_count:
        errors.append(
            "full_test_count mismatch: "
            f"ledger={ledger.get('full_test_count')!r}, observed={observed_test_count}"
        )
    aggregate_match = AGGREGATE_TEST_COUNT.search(text)
    if aggregate_match is None:
        errors.append("human-readable aggregate test count is missing")
    elif int(aggregate_match["count"]) != ledger.get("full_test_count"):
        errors.append(
            "aggregate test count mismatch: "
            f"summary={aggregate_match['count']}, ledger={ledger.get('full_test_count')!r}"
        )

    expected_matrix = ledger.get("explorer_matrix")
    detailed_matrix = parse_detailed_matrix(text)
    if expected_matrix != detailed_matrix:
        errors.append(
            "Explorer matrix mismatch: "
            f"ledger={expected_matrix!r}, detailed={detailed_matrix!r}"
        )

    expected_beads = ledger.get("beads")
    if not isinstance(expected_beads, dict):
        errors.append("ledger beads field must be an object")
    else:
        for issue_id, expected_status in expected_beads.items():
            actual_status = bead_statuses.get(issue_id)
            if actual_status != expected_status:
                errors.append(
                    f"Beads mismatch for {issue_id}: "
                    f"ledger={expected_status!r}, canonical={actual_status!r}"
                )
    expected_metadata = ledger.get("bead_metadata", {})
    actual_metadata = bead_metadata or {}
    if not isinstance(expected_metadata, dict):
        errors.append("ledger bead_metadata field must be an object")
    else:
        for issue_id, expected in expected_metadata.items():
            actual = actual_metadata.get(issue_id, {})
            if not isinstance(expected, dict):
                errors.append(f"ledger bead_metadata for {issue_id} must be an object")
                continue
            for key, value in expected.items():
                if actual.get(key) != value:
                    errors.append(
                        f"Beads metadata mismatch for {issue_id}.{key}: "
                        f"ledger={value!r}, canonical={actual.get(key)!r}"
                    )
    expected_residual = ledger.get("blocked_residual")
    detailed_residual = parse_blocker_readback(text)
    if expected_residual != detailed_residual:
        errors.append("blocked residual detail disagrees with ledger")
    if observed_blocked_residual is not None and expected_residual != dict(observed_blocked_residual):
        errors.append(
            "blocked residual OS mismatch: "
            f"ledger={expected_residual!r}, observed={dict(observed_blocked_residual)!r}"
        )
    canonical_67 = bead_statuses.get("sm-62a.6.7")
    if canonical_67 != "blocked" and re.search(
        r"blocked\s+`?\.6\.7`?|blocked\s+sm-62a\.6\.7", text, re.IGNORECASE
    ):
        errors.append("stale prose says sm-62a.6.7 is blocked")
    if canonical_67 != "in_progress" and re.search(
        r"in_progress\s+`?\.6\.7`?|in_progress\s+sm-62a\.6\.7|"
        r"`?\.6\.7`?\s*(?:は)?in_progress|sm-62a\.6\.7\s*(?:は)?in_progress",
        text,
        re.IGNORECASE,
    ):
        errors.append("stale prose says sm-62a.6.7 is in_progress")
    if bead_statuses.get("sm-62a.7") == "closed" and re.search(
        r"(?:`?\.7`?|sm-62a\.7)\s*(?:は)?未実行", text
    ):
        errors.append("stale prose says closed sm-62a.7 is unexecuted")
    if isinstance(expected_residual, dict) and expected_residual.get("target_dir") is False:
        if "target一件保持" in text or re.search(
            r"復元対象[^\n]{0,80}一件だけを(?:意図的に)?保持", text
        ):
            errors.append("stale prose says a target directory is retained")
    return errors


def read_beads(issue_ids: list[str]) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    statuses: dict[str, str] = {}
    metadata: dict[str, dict[str, object]] = {}
    for issue_id in issue_ids:
        completed = subprocess.run(
            ["bd", "--readonly", "show", issue_id, "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list) or len(payload) != 1:
            raise RuntimeError(f"unexpected bd show response for {issue_id}")
        statuses[issue_id] = str(payload[0]["status"])
        metadata[issue_id] = dict(payload[0].get("metadata") or {})
    return statuses, metadata


def read_blocked_residual(repository: Path, thumbprint: str) -> dict[str, object]:
    if os.name != "nt":
        return {}
    script = (
        "$ErrorActionPreference='Stop';$t=$env:SKILL_MAGNET_GATE_THUMBPRINT;"
        "$target=$env:SKILL_MAGNET_GATE_TARGET;"
        "function Count-Cert($name,$location,$thumb){$s=[Security.Cryptography.X509Certificates.X509Store]::new($name,$location);"
        "$s.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly);try{return @($s.Certificates|Where-Object Thumbprint -eq $thumb).Count}finally{$s.Close()}};"
        "$d='Registry::HKEY_CURRENT_USER\\Software\\Classes\\Directory\\shell\\SkillMagnet';"
        "$b='Registry::HKEY_CURRENT_USER\\Software\\Classes\\Directory\\Background\\shell\\SkillMagnet';"
        "[ordered]@{thumbprint=$t;current_user_my=(Count-Cert 'My' 'CurrentUser' $t);"
        "current_user_trusted_people=(Count-Cert 'TrustedPeople' 'CurrentUser' $t);"
        "local_machine_trusted_people=(Count-Cert 'TrustedPeople' 'LocalMachine' $t);"
        "directory_classic_keys=(1+@(Get-ChildItem -LiteralPath $d -Recurse).Count);"
        "background_classic_keys=(1+@(Get-ChildItem -LiteralPath $b -Recurse).Count);"
        "context_menu_dir=[bool](Test-Path -LiteralPath ($env:LOCALAPPDATA+'\\SkillMagnet\\ContextMenu'));"
        "rollback_dir=[bool](Test-Path -LiteralPath ($env:LOCALAPPDATA+'\\SkillMagnet\\ContextMenu.rollback'));"
        "appx_count=@(Get-AppxPackage -Name 'SkillMagnet.ContextMenu').Count;"
        "target_dir=[bool](Test-Path -LiteralPath $target)}"
        "|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "SKILL_MAGNET_GATE_THUMBPRINT": thumbprint,
            "SKILL_MAGNET_GATE_TARGET": str(
                repository / ".e2e-target" / "Modern 空白 & 日本語 (test)"
            ),
        },
    )
    if completed.returncode:
        raise RuntimeError(f"blocked residual readback failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when Explorer result aggregates diverge from details or Beads."
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--observed-test-count", type=int)
    args = parser.parse_args(argv)

    text = args.results.read_text(encoding="utf-8")
    ledger = parse_ledger(text)
    expected_beads = ledger.get("beads")
    if not isinstance(expected_beads, dict):
        raise SystemExit("ledger beads field must be an object")
    repository = args.results.resolve().parents[1]
    observed_test_count = args.observed_test_count
    if observed_test_count is None:
        sys.path.insert(0, str(repository))
        previous_directory = Path.cwd()
        try:
            os.chdir(repository)
            suite = unittest.defaultTestLoader.discover("tests")
        finally:
            os.chdir(previous_directory)
        observed_test_count = suite.countTestCases()
    statuses, metadata = read_beads(list(expected_beads))
    blocker = ledger.get("blocked_residual")
    if not isinstance(blocker, dict) or not isinstance(blocker.get("thumbprint"), str):
        raise SystemExit("ledger blocked_residual/thumbprint is required")
    errors = validate_consistency(
        text,
        observed_test_count=observed_test_count,
        bead_statuses=statuses,
        bead_metadata=metadata,
        observed_blocked_residual=read_blocked_residual(repository, blocker["thumbprint"]),
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "PASS: results aggregate, detailed Explorer matrix, and canonical Beads agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
