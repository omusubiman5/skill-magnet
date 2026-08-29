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
                skill_member = next(
                    name
                    for name in names
                    if name.endswith(
                        "skill_magnet/_packs/codex-delivery-assurance-8f12af5/"
                        "codex-sandbox-approval-boundary/SKILL.md"
                    )
                )
                bundled_skill = archive.read(skill_member)
            canonical_skill = subprocess.run(
                [
                    "git",
                    "-C",
                    str(ROOT / ".approved-snapshots" / "codex-delivery-assurance-8f12af5"),
                    "show",
                    "8f12af5ddfdd3b985f26d33dad09d6061d675342:"
                    "codex-sandbox-approval-boundary/SKILL.md",
                ],
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(bundled_skill, canonical_skill)
            expected_suffixes = (
                "skill_magnet/skill-magnet.json",
                "skill_magnet/_native/windows-modern-context-menu/build.ps1",
                "skill_magnet/_native/windows-modern-context-menu/package.ps1",
                "skill_magnet/_packs/codex-delivery-assurance-8f12af5/INDEX.md",
                "skill_magnet/_packs/codex-delivery-assurance-8f12af5/.skill-magnet-snapshot.json",
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
pack = config.packs["codex-delivery-assurance"]
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
        repr(str(config_path.parent.parent))[1:-1] in next(
            part for part in leaves[0].command if "runpy.run_module" in part
        )
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
            self.assertEqual(result["commit"], "8f12af5ddfdd3b985f26d33dad09d6061d675342")
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

    def test_windows_release_packages_native_extension_inside_msix(self) -> None:
        manifest = (
            ROOT / "native" / "windows-modern-context-menu" / "AppxManifest.xml"
        ).read_text(encoding="utf-8")
        builder = (
            ROOT / "native" / "windows-modern-context-menu" / "build-package.ps1"
        ).read_text(encoding="utf-8")
        installer = (
            ROOT / "native" / "windows-modern-context-menu" / "package.ps1"
        ).read_text(encoding="utf-8")
        self.assertNotIn("AllowExternalContent", manifest)
        self.assertIn('"SkillMagnetCommand.dll"', builder)
        self.assertIn('"SkillMagnetIdentity.exe"', builder)
        self.assertIn('"SkillMagnetMenu.tsv"', builder)
        self.assertNotIn(
            "-ExternalLocation $ExternalLocation -ForceApplicationShutdown",
            installer,
        )
        self.assertIn(
            "Add-AppxPackage -Path $package -ForceApplicationShutdown", installer
        )
        self.assertIn("if (Test-Path -LiteralPath $machinePath)", installer)
        self.assertIn("if (Test-Path -LiteralPath $userPath)", installer)
        command_source = (
            ROOT / "native" / "windows-modern-context-menu" / "SkillMagnetCommand.cpp"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\\SkillMagnet\\ContextMenu\\SkillMagnetMenu.tsv", command_source)

    def test_windows_lifecycle_refuses_to_destroy_existing_installation(self) -> None:
        lifecycle = (
            ROOT / "tests" / "powershell" / "windows-release-lifecycle-tests.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('Get-AppxPackage -Name "SkillMagnet.ContextMenu"', lifecycle)
        self.assertIn('"ContextMenu.rollback"', lifecycle)
        self.assertIn("SKILL_MAGNET_ALLOW_DESTRUCTIVE_LIFECYCLE", lifecycle)
        self.assertIn("Refusing to run the destructive release lifecycle", lifecycle)

    @unittest.skipUnless(sys.platform == "win32", "Windows Appx preflight required")
    def test_windows_lifecycle_preflight_fails_before_existing_state_is_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            product_root = Path(temporary) / "SkillMagnet" / "ContextMenu"
            product_root.mkdir(parents=True)
            marker = product_root / "preserve.marker"
            marker.write_text("must survive preflight", encoding="utf-8")
            environment = dict(os.environ)
            environment["LOCALAPPDATA"] = temporary
            environment.pop("SKILL_MAGNET_ALLOW_DESTRUCTIVE_LIFECYCLE", None)
            result = subprocess.run(
                [
                    "pwsh.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(
                        ROOT
                        / "tests"
                        / "powershell"
                        / "windows-release-lifecycle-tests.ps1"
                    ),
                ],
                env=environment,
                capture_output=True,
                text=True,
            )
            diagnostic = result.stderr + result.stdout
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to run the destructive release lifecycle", diagnostic)
            self.assertIn("ContextMenu", diagnostic)
            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive preflight")

    def test_repository_root_has_no_native_build_residue(self) -> None:
        residue = sorted(
            path.name
            for path in ROOT.iterdir()
            if path.is_file() and path.suffix.casefold() in {".obj", ".lib", ".exp"}
        )
        self.assertEqual(residue, [])


if __name__ == "__main__":
    unittest.main()
