from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent
NATIVE_SOURCE = ROOT / "native" / "windows-modern-context-menu"


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


class BuildPyWithRuntimeAssets(build_py):
    def run(self) -> None:
        # setuptools reuses build/lib by default.  Runtime packs have changed
        # names across releases, so a reused directory can silently retain an
        # obsolete pack and contaminate the next wheel.  Always construct the
        # package tree from the current source/configuration only.
        package_root = Path(self.build_lib) / "skill_magnet"
        if package_root.exists():
            shutil.rmtree(package_root)
        super().run()
        bundled_native = package_root / "_native" / "windows-modern-context-menu"
        if bundled_native.exists():
            shutil.rmtree(bundled_native)
        _copy_tree(NATIVE_SOURCE, bundled_native)
        shutil.copy2(ROOT / "skill-magnet.json", package_root / "skill-magnet.json")


setup(cmdclass={"build_py": BuildPyWithRuntimeAssets})
