import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config
from skill_magnet.platforms import render_windows_modern_menu_manifest
from skill_magnet import ui


class EntryContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(__file__).resolve().parents[1] / "skill-magnet.json"

    def test_config_changes_update_choices_without_changing_the_os_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            content = json.loads(self.fixture.read_text(encoding="utf-8"))
            config.write_text(json.dumps(content), encoding="utf-8")
            before_entry = render_windows_modern_menu_manifest(config)
            before_choices = ui.context_selection_choice_map(ActivationEngine(Config.load(config), root / "state"))
            for pack in content["packs"]:
                pack["expected_commit"] = "a" * 40
                pack["menu_label"] = "Updated " + pack["menu_label"]
                for metadata in pack.get("skill_metadata", {}).values():
                    metadata["display_name"] = "Updated " + metadata["display_name"]
            config.write_text(json.dumps(content), encoding="utf-8")
            after_choices = ui.context_selection_choice_map(ActivationEngine(Config.load(config), root / "state"))
            self.assertNotEqual(before_choices, after_choices)
            self.assertEqual(before_entry, render_windows_modern_menu_manifest(config))
            records = before_entry.splitlines()[1:]
            self.assertEqual(len(records), 1)
            self.assertIn("\tlauncher\troot\t", records[0])
            self.assertNotIn("a" * 40, records[0])

            # Adding and removing packs must take effect on the next launch,
            # with exactly the same OS registration bytes.
            added = json.loads(json.dumps(content["packs"][0]))
            added["id"] = "new-pack"
            added["menu_label"] = "New pack"
            content["packs"].append(added)
            config.write_text(json.dumps(content), encoding="utf-8")
            added_choices = ui.context_selection_choice_map(ActivationEngine(Config.load(config), root / "state"))
            self.assertTrue(any(value[0] == "new-pack" for value in added_choices.values()))
            self.assertEqual(before_entry, render_windows_modern_menu_manifest(config))
            content["packs"].pop()
            removed_id = content["packs"].pop(0)["id"]
            config.write_text(json.dumps(content), encoding="utf-8")
            remaining_choices = ui.context_selection_choice_map(ActivationEngine(Config.load(config), root / "state"))
            self.assertFalse(any(value[0] in {"new-pack", removed_id} for value in remaining_choices.values()))
            self.assertEqual(before_entry, render_windows_modern_menu_manifest(config))

    @unittest.skipUnless(os.name == "nt", "actual Windows Tk visibility")
    def test_manager_and_registration_are_visible_in_the_real_common_window(self):
        import tkinter as tk

        with tempfile.TemporaryDirectory() as temporary:
            root_path = Path(temporary)
            engine = ActivationEngine(Config.load(self.fixture), root_path / "state")
            lease = ui.acquire_context_ui_lease(engine.state_dir, root_path)
            observed = {}

            def ready(hwnd):
                lease.publish_window(window_handle=hwnd, phase="context_selection")
                root = tk._default_root

                def measure():
                    observed.update({str(w.cget("text")): bool(w.winfo_ismapped()) for w in root.winfo_children() if w.winfo_class() == "TButton"})
                    root.tk.call(root.protocol("WM_DELETE_WINDOW"))

                root.after(200, measure)

            try:
                with mock.patch.object(ui, "context_selection_details", return_value={}):
                    ui.show_context_selection(engine, platform="windows", project=root_path, allow_dynamic_selection=True, library_manager=lambda: None, register_selected=lambda: None, window_ready=ready)
            finally:
                lease.release()
            self.assertTrue(observed.get("Library Manager"), observed)
            self.assertTrue(observed.get("このフォルダーのスキルを登録"), observed)
