from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionArtifactTest(unittest.TestCase):
    def test_wheel_is_standalone_and_validates_bundled_runtime_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            wheels = temporary_path / "wheels"
            wheels.mkdir()
            built = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    str(ROOT),
                    "--no-deps",
                    "--wheel-dir",
                    str(wheels),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr or built.stdout)
            wheel = next(wheels.glob("skill_magnet-*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
            expected_suffixes = (
                "skill_magnet/skill-magnet.json",
                "skill_magnet/_native/windows-modern-context-menu/build.ps1",
                "skill_magnet/_native/windows-modern-context-menu/package.ps1",
                "skill_magnet/_packs/codex-pmo-skills-c7747bba/INDEX.md",
                "skill_magnet/_packs/codex-pmo-skills-c7747bba/.skill-magnet-snapshot.json",
            )
            for suffix in expected_suffixes:
                self.assertTrue(any(name.endswith(suffix) for name in names), suffix)

            target = temporary_path / "installed"
            installed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr or installed.stdout)
            probe = r'''
import json
from pathlib import Path
from skill_magnet.cli import _default_config_path
from skill_magnet.core import Config, Engine
from skill_magnet.platforms import _windows_modern_paths
from skill_magnet.platforms import windows_menu_leaves
config_path = _default_config_path()
config = Config.load(config_path)
pack = config.packs["codex-pmo-skills"]
commit, hashes = Engine(config)._validate_pack(pack)
native, _, package_script = _windows_modern_paths(Path.cwd() / "external")
leaves = windows_menu_leaves(config_path, "%1")
print(json.dumps({
    "config": config_path.is_file(),
    "commit": commit,
    "skills": len(hashes),
    "native": native.is_dir(),
    "package_script": package_script.is_file(),
    "leaves": len(leaves),
    "command_uses_installed_package": (
        repr(str(config_path.parent.parent))[1:-1] in leaves[0].command[3]
        and str(config_path) in leaves[0].command
    ),
}))
'''
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(target)
            probed = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=temporary_path,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probed.returncode, 0, probed.stderr or probed.stdout)
            result = json.loads(probed.stdout)
            self.assertEqual(result["commit"], "c7747bba0bc391316aa558b3b4e8dd412045d2dc")
            self.assertEqual(result["skills"], 9)
            self.assertTrue(result["config"])
            self.assertTrue(result["native"])
            self.assertTrue(result["package_script"])
            self.assertEqual(result["leaves"], 1)
            self.assertTrue(result["command_uses_installed_package"], result)

    def test_python_and_msix_versions_are_synchronized(self) -> None:
        import tomllib
        import xml.etree.ElementTree as ET

        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        python_version = project["project"]["version"]
        manifest = ET.parse(
            ROOT / "native" / "windows-modern-context-menu" / "AppxManifest.xml"
        ).getroot()
        identity = next(element for element in manifest if element.tag.endswith("Identity"))
        self.assertEqual(identity.attrib["Version"], f"{python_version}.0")


if __name__ == "__main__":
    unittest.main()
