from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import hashlib
import tomllib
import zipfile
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
                         observed_pack_skill_count: int,
                         observed_version: str | None = None) -> list[str]:
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
    if observed_version is not None:
        required_release_state = {
            "release_version": observed_version,
            "distribution_scope": "local-self-signed",
            "automated_status": f"LOCAL_RELEASE_GATE_PASS_{observed_test_count}",
            "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
            "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED",
        }
        for key, expected_value in required_release_state.items():
            if ledger.get(key) != expected_value:
                errors.append(
                    f"{key} mismatch: ledger={ledger.get(key)!r}, "
                    f"observed={expected_value!r}"
                )
    if not re.fullmatch(r"[0-9a-f]{40}", str(ledger.get("release_code_sha", ""))):
        errors.append("release_code_sha must be a lowercase 40-hex commit")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(ledger.get("wheel_payload_sha256", ""))
    ):
        errors.append("wheel_payload_sha256 must be a lowercase 64-hex digest")
    stale = (r"18\s*(?:個別|immediate)\s*(?:leaf|leaves)",
             r"固定9\s*skills\s*[×x]\s*(?:Codex|Claude)",
             r"保管庫の固定commitから個別skillを選び")
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in stale):
        errors.append("stale individual-skill menu claim remains")
    return errors


def wheel_payload_sha256(wheel: Path) -> str:
    """Hash logical wheel payload, excluding RECORD and normalizing text EOLs."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(wheel) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                continue
            content = archive.read(name)
            if b"\0" not in content:
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    content = content.replace(b"\r\n", b"\n")
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
    return digest.hexdigest()


def validate_release_provenance(
    repository: Path, ledger: dict[str, object], wheel: Path | None
) -> list[str]:
    errors: list[str] = []
    release_sha = str(ledger.get("release_code_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        return errors
    commit = subprocess.run(
        ["git", "cat-file", "-e", f"{release_sha}^{{commit}}"],
        cwd=repository,
        capture_output=True,
    )
    if commit.returncode != 0:
        errors.append("release_code_sha does not identify a repository commit")
        return errors
    artifact_inputs = [
        "src",
        "native",
        "setup.py",
        "pyproject.toml",
        "skill-magnet.json",
        ".approved-snapshots",
    ]
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{release_sha}..HEAD", "--", *artifact_inputs],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if changed:
        errors.append("artifact inputs changed after release_code_sha: " + changed)
    worktree_changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *artifact_inputs],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if worktree_changed:
        errors.append("uncommitted artifact inputs remain: " + worktree_changed)
    if wheel is not None:
        actual = wheel_payload_sha256(wheel)
        if actual != ledger.get("wheel_payload_sha256"):
            errors.append(
                "wheel_payload_sha256 mismatch: "
                f"ledger={ledger.get('wheel_payload_sha256')!r}, observed={actual!r}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current Explorer release evidence.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--observed-test-count", type=int)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args(argv)
    repository = args.results.resolve().parents[1]
    sys.path.insert(0, str(repository / "src"))
    from skill_magnet.core import Config
    from skill_magnet.platforms import windows_menu_leaves
    config_path = repository / "skill-magnet.json"
    config = Config.load(config_path)
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(project["project"]["version"])
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
    results_text = args.results.read_text(encoding="utf-8")
    errors = validate_consistency(
        results_text, observed_test_count=count,
        observed_leaf_count=len(leaves),
        observed_selection_kinds=sorted(
            {config.packs[leaf.pack_id].selection_kind for leaf in leaves}
        ),
        observed_pack_skill_count=len(leaves[0].skill_ids) if leaves else 0,
        observed_version=project_version)
    errors.extend(
        validate_release_provenance(repository, parse_ledger(results_text), args.wheel)
    )
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(
        "PASS: local self-signed release evidence matches product configuration "
        "and test suite; public distribution is not claimed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
