from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skill_magnet.platforms import _windows_handle_identity


class DistributionArtifactTest(unittest.TestCase):
    def test_wheel_is_standalone_and_contains_no_local_skill_content(self) -> None:
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
                prohibited = ("/_packs/", "/SKILL.md", "/acceptance.json", "/INDEX.md")
                self.assertFalse(
                    any(any(marker in f"/{name}" for marker in prohibited) for name in names),
                    "wheel must not contain local skill content",
                )
            expected_suffixes = (
                "skill_magnet/skill-magnet.json",
                "skill_magnet/_native/windows-modern-context-menu/build.ps1",
                "skill_magnet/_native/windows-modern-context-menu/package.ps1",
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
pack = config.packs["codex-cli"]
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
        leaves[0].command[1:4] == ("-I", "-m", "skill_magnet")
        and str(config_path) in leaves[0].command
        and all("sys.path.insert" not in part for part in leaves[0].command)
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
            self.assertEqual(result["commit"], "7af6c4f36b7183d9eaaaddf11c510769b632d2b8")
            self.assertEqual(result["skills"], 9)
            self.assertTrue(result["config"])
            self.assertTrue(result["native"])
            self.assertTrue(result["package_script"])
            self.assertEqual(result["leaves"], 3)
            self.assertTrue(result["command_uses_installed_package"], result)

    def test_python_and_msix_versions_are_synchronized(self) -> None:
        import tomllib
        import xml.etree.ElementTree as ET

        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        python_version = project["project"]["version"]
        package_version: dict[str, str] = {}
        exec(
            (ROOT / "src" / "skill_magnet" / "__init__.py").read_text(
                encoding="utf-8"
            ),
            package_version,
        )
        self.assertEqual(package_version["__version__"], python_version)
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
        self.assertIn("same_name_package_count", installer)
        self.assertIn("expected_identity_match_count", installer)
        self.assertIn("unexpected_same_name_package_count", installer)
        self.assertIn("Where-Object { $_.Publisher -eq $expectedPublisher }", installer)
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

    def test_windows_ci_parses_all_powershell_with_windows_powershell(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        step = workflow[
            workflow.index("name: Parse every PowerShell script") :
            workflow.index("- run: python -m pip install -e .")
        ]
        self.assertIn("shell: powershell", step)
        self.assertIn("Language.Parser]::ParseFile", step)
        self.assertIn('Filter "*.ps1"', step)
        self.assertIn("if ($failures.Count -gt 0)", step)

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

    def test_native_build_only_accepts_caller_owned_empty_workspace(self) -> None:
        build_script = (
            ROOT / "native" / "windows-modern-context-menu" / "build.ps1"
        ).read_text(encoding="utf-8-sig")
        generated_outputs = {
            "ContractTest.exe",
            "ContractTest.obj",
            "SkillMagnetCommand.dll",
            "SkillMagnetCommand.exp",
            "SkillMagnetCommand.lib",
            "SkillMagnetCommand.obj",
            "SkillMagnetIdentity.exe",
            "SkillMagnetIdentity.obj",
            "SkillMagnetMenu.tsv",
            "SkillMagnetNativeSource.h",
            "SkillMagnetNativeSource.json",
        }
        for name in generated_outputs:
            self.assertIn(f'"{name}"', build_script)
        self.assertIn("[string]$BuildNonce", build_script)
        self.assertIn(".skill-magnet-native-build.json", build_script)
        self.assertIn("OutDir must be empty", build_script)
        self.assertNotIn("Remove-Item", build_script)
        self.assertNotIn("New-Item", build_script)

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    def test_native_hash_helpers_execute_on_windows_powershell_5_1(self) -> None:
        for script_name in ("build.ps1", "build-package.ps1"):
            with self.subTest(script=script_name):
                script_text = (
                    ROOT / "native" / "windows-modern-context-menu" / script_name
                ).read_text(encoding="utf-8-sig")
                match = re.search(
                    r"(?ms)^function Get-SkillMagnetSha256Hex \{.*?^\}",
                    script_text,
                )
                self.assertIsNotNone(match)
                command = (
                    match.group(0)
                    + "\n$result = Get-SkillMagnetSha256Hex -Bytes "
                    + "([System.Text.Encoding]::UTF8.GetBytes('abc'))\n"
                    + "if ($result -cne "
                    + "'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') "
                    + "{ throw ('Unexpected SHA-256: ' + $result) }\n"
                )
                result = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertNotIn("SHA256]::HashData", script_text)
                self.assertNotIn("[Convert]::ToHexString", script_text)

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    def test_native_build_rejects_existing_bytes_without_changing_them(self) -> None:
        script = ROOT / "native" / "windows-modern-context-menu" / "build.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "owned-workspace"
            output = workspace / "out"
            output.mkdir(parents=True)
            nonce = "a" * 32
            root_identity = _windows_handle_identity(workspace, directory=True)
            output_identity = _windows_handle_identity(output, directory=True)
            marker = {
                "schema_version": 1,
                "contract": "skill-magnet-native-build-workspace-v1",
                "nonce": nonce,
                "volume_serial": root_identity["volume_serial"],
                "root_file_id": root_identity["file_id"],
                "output_file_id": output_identity["file_id"],
            }
            marker_bytes = (
                json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            (workspace / ".skill-magnet-native-build.json").write_bytes(marker_bytes)
            marker_sha256 = hashlib.sha256(marker_bytes).hexdigest()
            sentinel = output / "arbitrary-user-bytes.bin"
            payload = bytes(range(256))
            sentinel.write_bytes(payload)
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-OutDir",
                    str(output),
                    "-BuildNonce",
                    nonce,
                    "-MarkerSha256",
                    marker_sha256,
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OutDir must be empty", result.stderr + result.stdout)
            self.assertEqual(sentinel.read_bytes(), payload)

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell required")
    def test_native_build_rejects_duplicate_marker_and_forged_identity(self) -> None:
        script = ROOT / "native" / "windows-modern-context-menu" / "build.ps1"
        for case in ("duplicate", "forged-identity"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary) / "workspace"
                output = workspace / "out"
                output.mkdir(parents=True)
                nonce = "b" * 32
                root_identity = _windows_handle_identity(workspace, directory=True)
                output_identity = _windows_handle_identity(output, directory=True)
                values = {
                    "contract": "skill-magnet-native-build-workspace-v1",
                    "nonce": nonce,
                    "output_file_id": output_identity["file_id"],
                    "root_file_id": root_identity["file_id"],
                    "schema_version": 1,
                    "volume_serial": root_identity["volume_serial"],
                }
                if case == "duplicate":
                    marker_text = (
                        json.dumps(values, sort_keys=True, separators=(",", ":"))
                        .replace(
                            '"schema_version":1',
                            '"schema_version":1,"schema_version":1',
                        )
                        + "\n"
                    )
                else:
                    values["root_file_id"] = "f" * 32
                    marker_text = (
                        json.dumps(values, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    )
                marker_bytes = marker_text.encode("utf-8")
                (workspace / ".skill-magnet-native-build.json").write_bytes(
                    marker_bytes
                )
                result = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-OutDir",
                        str(output),
                        "-BuildNonce",
                        nonce,
                        "-MarkerSha256",
                        hashlib.sha256(marker_bytes).hexdigest(),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "managed build workspace marker",
                    result.stderr + result.stdout,
                )
                self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
