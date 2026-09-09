import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from skill_magnet.core import SkillMagnetError
from skill_magnet.platforms import _capture_windows_context_backup, _restore_windows_context_backup


class WindowsCertificateRollbackTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "ContextMenu"
        self.root.mkdir()
        self.backup = self.root.with_name("ContextMenu.rollback")
        self.previous = "A" * 40
        self.certificates = {self.previous}
        self.actions = []
        self.installed = True
        self.identity = {
            "name": "SkillMagnet.ContextMenu", "version": "0.5.9.0",
            "architecture": "X64", "publisher": "CN=Skill Magnet Local",
            "package_full_name": "SkillMagnet.ContextMenu_0.5.9.0_x64__test",
        }
        self.write_state(self.previous)
        (self.root / "SkillMagnet.ContextMenu.msix").write_bytes(b"previous package")
        with mock.patch("skill_magnet.platforms.windows_modern_context_menu_status",
                        return_value=self.status()):
            _capture_windows_context_backup(self.backup, install_root=self.root, run=self.run_package)

    def write_state(self, thumbprint):
        (self.root / "certificate-state.json").write_text(json.dumps({
            "thumbprint": thumbprint, "created_my": True,
            "created_trusted_people": True, "created_machine_trusted_people": True,
        }), encoding="utf-8")

    def status(self):
        return {"installed": self.installed,
                "same_name_packages": [self.identity] if self.installed else []}

    def run_package(self, args, **kwargs):
        if args[0] == "reg":
            return SimpleNamespace(returncode=1, stdout="", stderr="__REGISTRY_KEY_NOT_FOUND__")
        action = args[args.index("-Action") + 1]
        self.actions.append(action)
        if action == "uninstall":
            self.installed = False
        elif action == "cleanup-certificate":
            current = json.loads((self.root / "certificate-state.json").read_text())
            self.certificates.discard(current["thumbprint"].upper())
        elif action == "install":
            saved = json.loads((self.root / "certificate-state.json").read_text())
            if saved["thumbprint"].upper() not in self.certificates:
                return SimpleNamespace(returncode=1, stdout="", stderr="matching My certificate is missing")
            self.installed = True
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.status()), stderr="")

    def test_rollback_keeps_certificate_shared_with_previous_package(self):
        self.write_state(self.previous.lower())
        _restore_windows_context_backup(self.backup, install_root=self.root, run=self.run_package)
        self.assertTrue(self.installed)
        self.assertEqual(self.certificates, {self.previous})
        self.assertNotIn("cleanup-certificate", self.actions)
        self.assertEqual((self.root / "certificate-state.json").read_bytes(),
                         (self.backup / "external/certificate-state.json").read_bytes())

    def test_rollback_cleans_replacement_certificate_without_deleting_previous_one(self):
        replacement = "B" * 40
        self.certificates.add(replacement)
        self.write_state(replacement)
        _restore_windows_context_backup(self.backup, install_root=self.root, run=self.run_package)
        self.assertTrue(self.installed)
        self.assertEqual(self.certificates, {self.previous})
        self.assertIn("cleanup-certificate", self.actions)

    def test_invalid_certificate_identity_blocks_before_uninstall(self):
        self.write_state("not-a-thumbprint")
        before = (self.root / "certificate-state.json").read_bytes()
        with self.assertRaises(SkillMagnetError):
            _restore_windows_context_backup(self.backup, install_root=self.root, run=self.run_package)
        self.assertEqual(self.actions, [])
        self.assertTrue(self.installed)
        self.assertEqual((self.root / "certificate-state.json").read_bytes(), before)
