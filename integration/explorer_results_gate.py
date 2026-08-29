from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LEDGER_START = "<!-- explorer-results-ledger:start"
LEDGER_END = "explorer-results-ledger:end -->"


def parse_ledger(text: str) -> dict[str, object]:
    start, end = text.find(LEDGER_START), text.find(LEDGER_END)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("results ledger markers are missing or out of order")
    return json.loads(text[start + len(LEDGER_START) : end].strip())


def validate_consistency(text: str, *, observed_test_count: int,
                         observed_leaf_count: int,
                         observed_selection_kinds: list[str],
                         observed_pack_skill_count: int) -> list[str]:
    ledger = parse_ledger(text)
    errors: list[str] = []
    expected = {"full_test_count": observed_test_count,
                "menu_leaf_count": observed_leaf_count,
                "selection_kinds": observed_selection_kinds,
                "pack_skill_count": observed_pack_skill_count}
    for key, actual in expected.items():
        if ledger.get(key) != actual:
            errors.append(f"{key} mismatch: ledger={ledger.get(key)!r}, observed={actual!r}")
    summary = re.search(r"統合テスト: .*?— (\d+) tests PASS", text)
    if summary is None or int(summary.group(1)) != observed_test_count:
        errors.append("human-readable test count mismatch")
    if ledger.get("release_scope") != "one-package-leaf":
        errors.append("release_scope must be one-package-leaf")
    stale = (r"18\s*(?:個別|immediate)\s*(?:leaf|leaves)",
             r"固定9\s*skills\s*[×x]\s*(?:Codex|Claude)",
             r"保管庫の固定commitから個別skillを選び")
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in stale):
        errors.append("stale individual-skill menu claim remains")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current Explorer release evidence.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--observed-test-count", type=int)
    args = parser.parse_args(argv)
    repository = args.results.resolve().parents[1]
    sys.path.insert(0, str(repository / "src"))
    from skill_magnet.core import Config
    from skill_magnet.platforms import windows_menu_leaves
    config_path = repository / "skill-magnet.json"
    config = Config.load(config_path)
    leaves = windows_menu_leaves(config_path, "%1")
    count = args.observed_test_count
    if count is None:
        counted = subprocess.run(
            [sys.executable, "-c", "import unittest; print(unittest.defaultTestLoader.discover('tests').countTestCases())"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        count = int(counted.stdout.strip())
    errors = validate_consistency(
        args.results.read_text(encoding="utf-8"), observed_test_count=count,
        observed_leaf_count=len(leaves),
        observed_selection_kinds=sorted(
            {config.packs[leaf.pack_id].selection_kind for leaf in leaves}
        ),
        observed_pack_skill_count=len(leaves[0].skill_ids) if leaves else 0)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("PASS: current Explorer evidence matches product configuration and test suite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
