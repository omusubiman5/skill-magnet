from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent
PACK_NAME = "codex-pmo-skills-c7747bba"
PACK_SOURCE = ROOT / ".approved-snapshots" / PACK_NAME
NATIVE_SOURCE = ROOT / "native" / "windows-modern-context-menu"


def _hash_directory(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RuntimeError(f"Links are not allowed in bundled skills: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    def ignored(_directory: str, names: list[str]) -> set[str]:
        blocked = {".git", "out", "__pycache__"}
        return {
            name
            for name in names
            if name in blocked
            or name.endswith((".obj", ".lib", ".exp", ".pyc"))
        }

    shutil.copytree(source, destination, ignore=ignored)


def _copy_git_tree(source: Path, destination: Path, commit: str) -> None:
    """Copy canonical Git blob bytes, independent of checkout EOL conversion."""
    listing = subprocess.run(
        ["git", "-C", str(source), "ls-tree", "-r", "-z", commit],
        check=True,
        capture_output=True,
    ).stdout
    destination.mkdir(parents=True)
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, relative_bytes = entry.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        relative = relative_bytes.decode("utf-8")
        parts = Path(relative).parts
        if (
            object_type != "blob"
            or mode == "120000"
            or not parts
            or any(part in {"", ".", "..", ".git", "out", "__pycache__"} for part in parts)
            or relative.endswith((".obj", ".lib", ".exp", ".pyc"))
        ):
            if mode == "120000":
                raise RuntimeError(f"Links are not allowed in bundled skills: {relative}")
            continue
        target = destination.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            subprocess.run(
                ["git", "-C", str(source), "cat-file", "blob", object_id],
                check=True,
                capture_output=True,
            ).stdout
        )


class BuildPyWithRuntimeAssets(build_py):
    def run(self) -> None:
        super().run()
        package_root = Path(self.build_lib) / "skill_magnet"
        if not PACK_SOURCE.is_dir():
            raise RuntimeError(
                "Pinned skill pack is missing; run git submodule update --init --recursive"
            )

        config = json.loads((ROOT / "skill-magnet.json").read_text(encoding="utf-8"))
        pack = config["packs"][0]
        actual_commit = subprocess.run(
            ["git", "-C", str(PACK_SOURCE), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip().lower()
        if actual_commit != pack["expected_commit"]:
            raise RuntimeError(
                f"Pinned pack mismatch: expected {pack['expected_commit']}, got {actual_commit}"
            )
        dirty = subprocess.run(
            ["git", "-C", str(PACK_SOURCE), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        if dirty:
            raise RuntimeError("Pinned pack contains uncommitted content")

        bundled_pack = package_root / "_packs" / PACK_NAME
        if bundled_pack.exists():
            shutil.rmtree(bundled_pack)
        _copy_git_tree(PACK_SOURCE, bundled_pack, actual_commit)
        manifest = {
            "version": 1,
            "repo_url": pack["repo_url"],
            "commit": actual_commit,
            "index_sha256": hashlib.sha256(
                (bundled_pack / "INDEX.md").read_bytes()
            ).hexdigest(),
            "skills": {
                skill: _hash_directory(bundled_pack / skill)
                for skill in pack["skills"]
            },
        }
        (bundled_pack / ".skill-magnet-snapshot.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        bundled_native = package_root / "_native" / "windows-modern-context-menu"
        if bundled_native.exists():
            shutil.rmtree(bundled_native)
        _copy_tree(NATIVE_SOURCE, bundled_native)
        shutil.copy2(ROOT / "skill-magnet.json", package_root / "skill-magnet.json")


setup(cmdclass={"build_py": BuildPyWithRuntimeAssets})
