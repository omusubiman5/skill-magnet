from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
import base64
import hashlib
import io
import json
import struct
from pathlib import Path
from unittest import mock

from integration.explorer_results_gate import (
    _configured_repository_url,
    _configured_selector_choices,
    _field_attestation_payload,
    _normalized_text_bytes,
    _native_source_manifest_from_repository,
    _release_runtime_payload_sha256,
    _selector_choice_map_sha256,
    main,
    parse_ledger,
    validate_consistency,
    validate_field_evidence,
    validate_field_bundle,
    validate_release_provenance,
    wheel_payload_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "windows-explorer-leaf-launch-results.md"


class ExplorerResultsGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = RESULTS.read_text(encoding="utf-8")
        self.count = unittest.defaultTestLoader.discover(str(ROOT / "tests")).countTestCases()

    def validate(self, text: str, count: int | None = None) -> list[str]:
        return validate_consistency(
            text, observed_test_count=self.count if count is None else count,
            observed_leaf_count=3,
            observed_selection_kinds=["package", "skill"],
            observed_pack_skill_counts=[1, 9, 12], observed_version="0.5.9")

    @staticmethod
    def _fake_x64_dll(source_tree_sha256: str) -> bytes:
        payload = bytearray(4096)
        payload[:2] = b"MZ"
        struct.pack_into("<I", payload, 0x3C, 0x80)
        payload[0x80:0x84] = b"PE\0\0"
        struct.pack_into(
            "<HHIIIHH",
            payload,
            0x84,
            0x8664,
            3,
            0,
            0,
            0,
            0xF0,
            0x2022,
        )
        struct.pack_into("<H", payload, 0x98, 0x20B)
        marker = (
            "skill-magnet-native-source-v1:" + source_tree_sha256
        ).encode("utf-16-le")
        payload[512 : 512 + len(marker)] = marker
        return bytes(payload)

    @staticmethod
    def _fake_x64_identity() -> bytes:
        payload = bytearray(4096)
        payload[:2] = b"MZ"
        struct.pack_into("<I", payload, 0x3C, 0x80)
        payload[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", payload, 0x84, 0x8664, 3, 0, 0, 0, 0xF0, 0x0022)
        struct.pack_into("<H", payload, 0x98, 0x20B)
        return bytes(payload)

    @staticmethod
    def _uia_element(name: str, control_type: str, process_id: int, token: int) -> dict[str, object]:
        return {
            "name": name,
            "control_type": control_type,
            "automation_id": "",
            "class_name": "unit-test-uia",
            "framework_id": "Win32",
            "process_id": process_id,
            "native_window_handle": 0 if control_type.endswith("MenuItem") else 1234 + token,
            "is_enabled": True,
            "is_offscreen": False,
            "bounding_rectangle": {"left": 10, "top": 20, "width": 180, "height": 30},
            "runtime_id": [42, token],
        }

    @staticmethod
    def _native_rows(
        source: str,
        project: str,
        invocation: str,
        template: str,
        launch: str,
        second: int,
        process_id: int,
        terminal_event: str = "child_running",
    ) -> list[str]:
        terminal_detail = str(process_id) if terminal_event == "child_running" else "0"
        rows = (
            ("100", "invoke_enter", template, "0", "unavailable"),
            ("200", "selection_succeeded", template, "0", project),
            ("300", "create_process_succeeded", launch, str(process_id), project),
            ("400", terminal_event, launch, terminal_detail, project),
        )
        return [
            f"2026-09-05T00:00:{second:02d}.{millis}Z\tevent={event}"
            f"\tcommand_sha256={command}\tdetail={detail}"
            f"\tselection_source={source}\tproject_sha256={project_value}"
            f"\tinvocation_id={invocation}"
            for millis, event, command, detail, project_value in rows
        ]

    def _field_fixture(
        self, root: Path
    ) -> tuple[dict[str, object], Path, Path, dict[str, object], bytes]:
        config_path = (ROOT / "skill-magnet.json").resolve()
        config_payload = config_path.read_bytes()
        configured_choices = _configured_selector_choices(config_payload)
        configured_labels = [str(choice["label"]) for choice in configured_choices]
        configured_remote = _configured_repository_url(config_payload)
        command = subprocess.list2cmdline(
            [
                sys.executable,
                "-I",
                "-m",
                "skill_magnet",
                "--config",
                str(config_path),
                "context",
                "--platform",
                "windows",
                "--project",
                "__SKILL_MAGNET_PROJECT__",
                "--launcher",
            ]
        )
        template = hashlib.sha256(command.encode("utf-16-le")).hexdigest()
        native_by_role = {
            "selected_item": self._native_rows(
                "selected_item", "a" * 64, "1" * 32, template, "d" * 64, 1, 4242
            ),
            "manager_same_folder": self._native_rows(
                "selected_item", "a" * 64, "2" * 32, template, "d" * 64, 2, 4243,
                "child_exited",
            ),
            "manager_different_folder": self._native_rows(
                "background_site", "c" * 64, "3" * 32, template, "a" * 64, 3, 4244
            ),
            "background_site": self._native_rows(
                "background_site", "b" * 64, "4" * 32, template, "e" * 64, 4, 4343
            ),
            "same_folder_repeat": self._native_rows(
                "background_site", "b" * 64, "5" * 32, template, "e" * 64, 5, 4444,
                "child_exited",
            ),
            "different_folder_busy": self._native_rows(
                "background_site", "d" * 64, "6" * 32, template, "f" * 64, 6, 4545
            ),
            "closed_window_relaunch": self._native_rows(
                "background_site", "b" * 64, "7" * 32, template, "e" * 64, 7, 4646
            ),
            "missing_skill_registration": self._native_rows(
                "selected_item", "a" * 64, "8" * 32, template, "d" * 64, 8, 4747
            ),
            "runtime_skill_projectless": self._native_rows(
                "selected_item", "e" * 64, "9" * 32, template, "c" * 64, 9, 4848
            ),
        }
        invoke_payload = (
            "\r\n".join(
                row
                for role in (
                    "selected_item",
                    "manager_same_folder",
                    "manager_different_folder",
                    "background_site",
                    "same_folder_repeat",
                    "different_folder_busy",
                    "closed_window_relaunch",
                    "missing_skill_registration",
                    "runtime_skill_projectless",
                )
                for row in native_by_role[role]
            )
            + "\r\n"
        ).encode("utf-16-le")
        invoke_log = root / "invoke.log"
        invoke_log.write_bytes(invoke_payload)

        def native_digest(role: str) -> str:
            return hashlib.sha256(
                (("\r\n".join(native_by_role[role])) + "\r\n").encode("utf-16-le")
            ).hexdigest()

        observations: list[dict[str, object]] = []
        gui_elements: dict[str, dict[str, object]] = {}

        def primary_entries(source: str, *, second: int, process_id: int, token: int,
                            invocation: str, project: str) -> list[dict[str, object]]:
            native_rows = native_by_role[source]
            root_element = self._uia_element(
                "Skill Magnet", "ControlType.MenuItem", 100, token
            )
            gui_element = self._uia_element(
                "Skill Magnet — 実行確認", "ControlType.Window", process_id, token + 10
            )
            gui_elements[source] = gui_element
            observations.append(
                {
                    "source": source,
                    "invocation_id": invocation,
                    "project_sha256": project,
                    "root_visible_count": 1,
                    "invoke_pattern_available": True,
                    "expand_collapse_pattern_available": False,
                    "submenu_item_count": 0,
                    "gui_visible": True,
                    "gui_title": "Skill Magnet — 実行確認",
                    "project_binding_visible": True,
                    "selection_choice_count": len(configured_choices),
                    "selection_choice_labels": configured_labels,
                    "selection_combo_exact_match_count": 1,
                    "library_manager_button_count": 1,
                    "register_button_count": 1,
                }
            )
            return [
                    {
                        "observed_at_utc": f"2026-09-05T00:00:{second:02d}.000Z",
                        "session_id": "f" * 32,
                        "event": "context_menu_root_observed",
                        "source": source,
                        "data": {
                            "element": root_element,
                            "root_visible_count": 1,
                            "invoke_pattern_available": True,
                            "expand_collapse_pattern_available": False,
                            "submenu_item_count": 0,
                        },
                    },
                    {
                        "observed_at_utc": f"2026-09-05T00:00:{second:02d}.500Z",
                        "session_id": "f" * 32,
                        "event": "root_invoke_dispatched",
                        "source": source,
                        "data": {"runtime_id": root_element["runtime_id"]},
                    },
                    {
                        "observed_at_utc": f"2026-09-05T00:00:{second:02d}.600Z",
                        "session_id": "f" * 32,
                        "event": "unified_gui_observed",
                        "source": source,
                        "data": {
                            "element": gui_element,
                            "project_sha256": project,
                            "gui_visible": True,
                            "gui_title": "Skill Magnet — 実行確認",
                            "project_binding_visible": True,
                            "selection_choice_count": len(configured_choices),
                            "selection_choice_labels": configured_labels,
                            "selection_combo_exact_match_count": 1,
                            "library_manager_button_count": 1,
                            "register_button_count": 1,
                        },
                    },
                    {
                        "observed_at_utc": f"2026-09-05T00:00:{second:02d}.700Z",
                        "session_id": "f" * 32,
                        "event": "native_sequence_bound",
                        "source": source,
                        "data": {
                            "invocation_id": invocation,
                            "project_sha256": project,
                            "native_sequence_sha256": hashlib.sha256(
                                (("\r\n".join(native_rows)) + "\r\n").encode("utf-16-le")
                            ).hexdigest(),
                        },
                    },
                ]

        selected_entries = primary_entries(
            "selected_item", second=1, process_id=4242, token=1,
            invocation="1" * 32, project="a" * 64,
        )
        manager_element = self._uia_element(
            "Library Manager", "ControlType.Window", 4242, 21
        )
        manager_busy_element = self._uia_element(
            "Skill Magnet エラー", "ControlType.Window", 4244, 22
        )
        state_snapshot = {
            "config_sha256": "1" * 64,
            "library_sha256": "2" * 64,
            "transactions_sha256": "3" * 64,
        }
        manager_observation = {
            "configured_remote": configured_remote,
            "configured_remote_visible": True,
            "create_button_count": 1,
            "update_button_count": 1,
            "delete_button_count": 1,
            "reload_button_count": 1,
            "same_folder_repeat_focused_existing_manager": True,
            "same_folder_repeat_manager_count": 1,
            "same_folder_repeat_error_count": 0,
            "different_folder_busy_text_visible": True,
            "different_folder_actionable_recovery_visible": True,
            "different_folder_ok_button_count": 1,
            "no_persistent_mutation": True,
        }
        manager_entry = {
            "observed_at_utc": "2026-09-05T00:00:03.800Z",
            "session_id": "f" * 32,
            "event": "library_manager_flow_observed",
            "source": "library_manager_flow",
            "data": {
                "element": manager_element,
                "configured_remote": configured_remote,
                "configured_remote_visible": True,
                "create_button_count": 1,
                "update_button_count": 1,
                "delete_button_count": 1,
                "reload_button_count": 1,
                "same_folder_repeat_invocation_id": "2" * 32,
                "same_folder_repeat_project_sha256": "a" * 64,
                "same_folder_repeat_native_sequence_sha256": native_digest(
                    "manager_same_folder"
                ),
                "same_folder_repeat_focused_existing_manager": True,
                "same_folder_repeat_manager_count": 1,
                "same_folder_repeat_error_count": 0,
                "different_folder_invocation_id": "3" * 32,
                "different_folder_project_sha256": "c" * 64,
                "different_folder_native_sequence_sha256": native_digest(
                    "manager_different_folder"
                ),
                "different_folder_busy_element": manager_busy_element,
                "different_folder_busy_text_visible": True,
                "different_folder_actionable_recovery_visible": True,
                "different_folder_ok_button_count": 1,
                "state_before": state_snapshot,
                "state_after": state_snapshot.copy(),
                "no_persistent_mutation": True,
            },
        }
        background_entries = primary_entries(
            "background_site", second=4, process_id=4343, token=2,
            invocation="4" * 32, project="b" * 64,
        )
        background_element = gui_elements["background_site"]
        busy_element = self._uia_element(
            "Skill Magnet エラー", "ControlType.Window", 4545, 31
        )
        relaunch_element = self._uia_element(
            "Skill Magnet — 実行確認", "ControlType.Window", 4646, 32
        )
        recovery_entries = [
                {
                    "observed_at_utc": "2026-09-05T00:00:05.800Z",
                    "session_id": "f" * 32,
                    "event": "same_folder_repeat_observed",
                    "source": "same_folder_repeat",
                    "data": {
                        "element": background_element,
                        "project_sha256": "b" * 64,
                        "original_invocation_id": "4" * 32,
                        "repeat_invocation_id": "5" * 32,
                        "repeat_native_sequence_sha256": native_digest("same_folder_repeat"),
                        "gui_count": 1,
                        "foreground_window_handle": background_element["native_window_handle"],
                        "unexpected_error_count": 0,
                    },
                },
                {
                    "observed_at_utc": "2026-09-05T00:00:06.800Z",
                    "session_id": "f" * 32,
                    "event": "different_folder_busy_observed",
                    "source": "different_folder_busy",
                    "data": {
                        "element": busy_element,
                        "project_sha256": "d" * 64,
                        "invocation_id": "6" * 32,
                        "native_sequence_sha256": native_digest("different_folder_busy"),
                        "busy_text_visible": True,
                        "actionable_recovery_visible": True,
                        "ok_button_count": 1,
                    },
                },
                {
                    "observed_at_utc": "2026-09-05T00:00:07.800Z",
                    "session_id": "f" * 32,
                    "event": "closed_window_relaunch_observed",
                    "source": "closed_window_relaunch",
                    "data": {
                        "element": relaunch_element,
                        "project_sha256": "b" * 64,
                        "original_invocation_id": "4" * 32,
                        "relaunch_invocation_id": "7" * 32,
                        "relaunch_native_sequence_sha256": native_digest(
                            "closed_window_relaunch"
                        ),
                        "original_process_id": background_element["process_id"],
                        "original_native_window_handle": background_element["native_window_handle"],
                        "original_runtime_id": background_element["runtime_id"],
                    },
                },
            ]

        registration_manager = self._uia_element(
            "Library Manager", "ControlType.Window", 4747, 41
        )
        registration_error = self._uia_element(
            "Skill Library Manager", "ControlType.Window", 4747, 42
        )
        registration_observation = {
            "selected_path_sha256": "a" * 64,
            "selected_path_visible": True,
            "missing_skill_cause_visible": True,
            "actionable_recovery_visible": True,
            "ok_button_count": 1,
            "no_persistent_mutation": True,
        }
        registration_entry = {
            "observed_at_utc": "2026-09-05T00:00:08.800Z",
            "session_id": "f" * 32,
            "event": "missing_skill_registration_observed",
            "source": "missing_skill_registration",
            "data": {
                "root_element": self._uia_element(
                    "Skill Magnet", "ControlType.MenuItem", 100, 40
                ),
                "root_visible_count": 1,
                "invoke_pattern_available": True,
                "expand_collapse_pattern_available": False,
                "submenu_item_count": 0,
                "unified_element": self._uia_element(
                    "Skill Magnet — 実行確認", "ControlType.Window", 4747, 43
                ),
                "manager_element": registration_manager,
                "error_element": registration_error,
                "invocation_id": "8" * 32,
                "project_sha256": "a" * 64,
                "native_sequence_sha256": native_digest("missing_skill_registration"),
                "selected_path_sha256": "a" * 64,
                "selected_path_visible": True,
                "missing_skill_cause_visible": True,
                "actionable_recovery_visible": True,
                "ok_button_count": 1,
                "state_before": state_snapshot,
                "state_after": state_snapshot.copy(),
                "no_persistent_mutation": True,
            },
        }

        runtime_content_digest = "4" * 64
        runtime_observation = {
            "clicked_path_sha256": "e" * 64,
            "runtime_path_hidden_as_workspace": True,
            "projectless_semantics_visible": True,
            "skill_content_sha256": runtime_content_digest,
            "read_only": True,
        }
        runtime_entry = {
            "observed_at_utc": "2026-09-05T00:00:09.800Z",
            "session_id": "f" * 32,
            "event": "runtime_skill_projectless_observed",
            "source": "runtime_skill_projectless",
            "data": {
                "root_element": self._uia_element(
                    "Skill Magnet", "ControlType.MenuItem", 100, 50
                ),
                "root_visible_count": 1,
                "invoke_pattern_available": True,
                "expand_collapse_pattern_available": False,
                "submenu_item_count": 0,
                "unified_element": self._uia_element(
                    "Skill Magnet — 実行確認", "ControlType.Window", 4848, 51
                ),
                "invocation_id": "9" * 32,
                "project_sha256": "e" * 64,
                "native_sequence_sha256": native_digest("runtime_skill_projectless"),
                "clicked_path_sha256": "e" * 64,
                "runtime_path_hidden_as_workspace": True,
                "projectless_semantics_visible": True,
                "skill_content_before_sha256": runtime_content_digest,
                "skill_content_after_sha256": runtime_content_digest,
                "state_before": state_snapshot,
                "state_after": state_snapshot.copy(),
                "no_persistent_mutation": True,
                "read_only": True,
            },
        }
        transcript_entries = (
            selected_entries
            + [manager_entry]
            + background_entries
            + recovery_entries
            + [registration_entry, runtime_entry]
        )
        for sequence, entry in enumerate(transcript_entries, 1):
            entry["sequence"] = sequence
        transcript_payload = (
            "\n".join(
                json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                for entry in transcript_entries
            )
            + "\n"
        ).encode("utf-8")
        transcript_digest = hashlib.sha256(transcript_payload).hexdigest()

        menu_payload = (
            "skill-magnet-menu-v4\n"
            "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
            "スキルまたはスキルパックを選び、Codex DesktopまたはClaude Code Desktopへ渡します。\t"
            + command
            + "\n"
        ).encode("utf-8")
        native_source = _native_source_manifest_from_repository(ROOT)
        source_tree_sha256 = str(native_source["source_tree_sha256"])
        command_dll = self._fake_x64_dll(source_tree_sha256)
        identity_exe = self._fake_x64_identity()
        native_source["artifacts"] = [
            {
                "path": "SkillMagnetCommand.dll",
                "size": len(command_dll),
                "sha256": hashlib.sha256(command_dll).hexdigest(),
            },
            {
                "path": "SkillMagnetIdentity.exe",
                "size": len(identity_exe),
                "sha256": hashlib.sha256(identity_exe).hexdigest(),
            },
        ]
        native_source_payload = (
            json.dumps(native_source, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        appx_payload = (
            ROOT / "native" / "windows-modern-context-menu" / "AppxManifest.xml"
        ).read_bytes()
        msix_buffer = io.BytesIO()
        with zipfile.ZipFile(msix_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("AppxManifest.xml", appx_payload)
            archive.writestr("SkillMagnetMenu.tsv", menu_payload)
            archive.writestr("SkillMagnetCommand.dll", command_dll)
            archive.writestr("SkillMagnetIdentity.exe", identity_exe)
            archive.writestr("SkillMagnetNativeSource.json", native_source_payload)
            archive.writestr("AppxSignature.p7x", b"unit-test-signature")
        artifact_bytes = {
            "appx_manifest": appx_payload,
            "menu_manifest": menu_payload,
            "command_dll": command_dll,
            "identity_exe": identity_exe,
            "native_source_manifest": native_source_payload,
            "external_command_dll": command_dll,
            "external_identity_exe": identity_exe,
            "external_native_source_manifest": native_source_payload,
            "signed_msix": msix_buffer.getvalue(),
            "contract_probe_output": (
                b"SkillMagnet direct-root IExplorerCommand contract PASS (Python host)\n"
            ),
            "config": config_payload,
        }
        artifact_contract = {
            "appx_manifest": ("installed_package", "AppxManifest.xml"),
            "menu_manifest": ("installed_package", "SkillMagnetMenu.tsv"),
            "command_dll": ("installed_package", "SkillMagnetCommand.dll"),
            "identity_exe": ("installed_package", "SkillMagnetIdentity.exe"),
            "native_source_manifest": (
                "installed_package",
                "SkillMagnetNativeSource.json",
            ),
            "external_command_dll": (
                "external_install_root",
                "SkillMagnetCommand.dll",
            ),
            "external_identity_exe": (
                "external_install_root",
                "SkillMagnetIdentity.exe",
            ),
            "external_native_source_manifest": (
                "external_install_root",
                "SkillMagnetNativeSource.json",
            ),
            "signed_msix": ("external_install_root", "SkillMagnet.ContextMenu.msix"),
            "contract_probe_output": (
                "isolated_package_artifact_probe",
                "contract-test-output.txt",
            ),
            "config": ("collector_config_argument", "skill-magnet.json"),
        }
        artifacts: dict[str, object] = {}
        for name, payload in artifact_bytes.items():
            source, file_name = artifact_contract[name]
            artifacts[name] = {
                "source": source,
                "file_name": file_name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes_base64": base64.b64encode(payload).decode("ascii"),
            }
        hashes = {
            "appx_manifest_sha256": artifacts["appx_manifest"]["sha256"],
            "menu_manifest_sha256": artifacts["menu_manifest"]["sha256"],
            "dll_sha256": artifacts["command_dll"]["sha256"],
            "identity_sha256": artifacts["identity_exe"]["sha256"],
            "native_source_manifest_sha256": artifacts["native_source_manifest"]["sha256"],
            "external_dll_sha256": artifacts["external_command_dll"]["sha256"],
            "external_identity_sha256": artifacts["external_identity_exe"]["sha256"],
            "external_native_source_manifest_sha256": artifacts[
                "external_native_source_manifest"
            ]["sha256"],
            "signed_msix_sha256": artifacts["signed_msix"]["sha256"],
            "contract_probe_output_sha256": artifacts["contract_probe_output"]["sha256"],
            "config_sha256": artifacts["config"]["sha256"],
            "config_path_sha256": hashlib.sha256(str(config_path).encode("utf-16-le")).hexdigest(),
            "invoke_log_sha256": hashlib.sha256(invoke_payload).hexdigest(),
            "uia_transcript_sha256": transcript_digest,
        }
        collector = ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        bundle: dict[str, object] = {
            "schema_version": 4,
            "release_version": "0.5.9",
            "release_code_sha": "7" * 40,
            "field_status": "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_0_5_9",
            "observed_at_utc": "2026-09-05T00:00:09.900Z",
            "collector_sha256": hashlib.sha256(
                _normalized_text_bytes(collector.read_bytes())
            ).hexdigest(),
            "python_runtime": {
                "module_version": "0.5.9",
                "distribution_version": "0.5.9",
                "distribution_name": "skill-magnet",
                "executable_path_sha256": hashlib.sha256(
                    sys.executable.encode("utf-16-le")
                ).hexdigest(),
                "module_path_sha256": "6" * 64,
                "distribution_module_path_sha256": "6" * 64,
                "payload_sha256": _release_runtime_payload_sha256(ROOT),
            },
            "package": {
                "name": "SkillMagnet.ContextMenu",
                "version": "0.5.9.0",
                "architecture": "X64",
                "publisher": "CN=Skill Magnet Local",
                "package_full_name": "SkillMagnet.ContextMenu_0.5.9.0_x64__abcdef",
                "same_name_package_count": 1,
                "expected_identity_match_count": 1,
                "unexpected_same_name_package_count": 0,
                "usable_installed_state": True,
                "menu_contract_matches_config": True,
                "command_target_signature_valid": True,
            },
            "native_source_binding": {
                "contract": "skill-magnet-native-source-v1",
                "source_tree_sha256": source_tree_sha256,
                "package_manifest_source_tree_sha256": source_tree_sha256,
                "external_manifest_source_tree_sha256": source_tree_sha256,
                "package_dll_export_source_tree_sha256": source_tree_sha256,
                "external_dll_export_source_tree_sha256": source_tree_sha256,
                "package_dll_embedded_binding_count": 1,
                "external_dll_embedded_binding_count": 1,
                "package_external_artifacts_equal": True,
                "signed_msix_payload_matches_package": True,
                "isolated_contract_probe_passed": True,
                "isolated_contract_probe_mode": "full-invoke",
                "status_native_source_tree_sha256": source_tree_sha256,
                "status_native_source_manifest_valid": True,
                "status_native_artifact_hashes_valid": True,
                "status_dll_native_source_binding_valid": True,
                "status_native_build_binding_valid": True,
            },
            "artifacts": artifacts,
            "hashes": hashes,
            "uia_transcript": {
                "encoding": "utf-8-jsonl",
                "line_count": 14,
                "sha256": transcript_digest,
                "bytes_base64": base64.b64encode(transcript_payload).decode("ascii"),
            },
            "selector_contract": {
                "configured_choices": configured_choices,
                "choice_map_sha256": _selector_choice_map_sha256(configured_choices),
                "exact_selector_combo_count": 1,
            },
            "explorer_observations": observations,
            "library_manager_observation": manager_observation,
            "registration_recovery_observation": registration_observation,
            "runtime_skill_observation": runtime_observation,
            "recovery_observations": {
                "same_folder_repeat_focused_existing_window": True,
                "same_folder_repeat_gui_count": 1,
                "different_folder_busy_message_visible": True,
                "different_folder_actionable_recovery_visible": True,
                "closed_window_relaunch_succeeded": True,
            },
            "attestation": {
                "algorithm": "sha256-rsa-cms-detached",
                "signer_thumbprint": "9" * 40,
                "signed_payload_sha256": "",
                "signature_base64": base64.b64encode(b"unit-test-detached-cms").decode("ascii"),
            },
        }
        signed_payload = _field_attestation_payload(bundle)
        bundle["attestation"]["signed_payload_sha256"] = hashlib.sha256(signed_payload).hexdigest()
        bundle_path = root / "field-bundle.json"
        bundle_bytes = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
        bundle_path.write_bytes(bundle_bytes)
        ledger = {
            "release_version": "0.5.9",
            "release_code_sha": "7" * 40,
            "windows_explorer_field_invoke_log_sha256": hashlib.sha256(invoke_payload).hexdigest(),
            "windows_explorer_field_bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
            "windows_explorer_field_signer_thumbprint": "9" * 40,
        }
        return ledger, bundle_path, invoke_log, bundle, signed_payload

    @staticmethod
    def _rewrite_bundle(
        bundle_path: Path, bundle: dict[str, object], ledger: dict[str, object]
    ) -> None:
        bundle["attestation"]["signed_payload_sha256"] = hashlib.sha256(
            _field_attestation_payload(bundle)
        ).hexdigest()
        payload = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
        bundle_path.write_bytes(payload)
        ledger["windows_explorer_field_bundle_sha256"] = hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _replace_transcript(
        bundle: dict[str, object], entries: list[dict[str, object]]
    ) -> None:
        payload = (
            "\n".join(
                json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                for entry in entries
            )
            + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        bundle["uia_transcript"]["bytes_base64"] = base64.b64encode(payload).decode("ascii")
        bundle["uia_transcript"]["line_count"] = len(entries)
        bundle["uia_transcript"]["sha256"] = digest
        bundle["hashes"]["uia_transcript_sha256"] = digest

    def test_canonical_results_are_consistent(self) -> None:
        self.assertEqual(self.validate(self.text), [])
        self.assertEqual(parse_ledger(self.text)["release_scope"], "direct-root-unified-selector")

    def test_gate_rejects_counts_menu_shape_and_stale_claims(self) -> None:
        self.assertTrue(self.validate(self.text, self.count + 1))
        self.assertTrue(self.validate(self.text.replace('"menu_leaf_count": 0', '"menu_leaf_count": 18')))
        self.assertTrue(
            self.validate(
                self.text.replace(
                    '"configured_selection_count": 3',
                    '"configured_selection_count": 18',
                )
            )
        )
        self.assertTrue(self.validate(self.text + "\n固定9 skills × Codex の18個別leaf\n"))
        self.assertTrue(
            self.validate(
                self.text.replace(
                    "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_0_5_9",
                    "PASS_REAL_RIGHT_CLICK_MENU_AND_CONFIRMATION_UI_0_5_1",
                )
            )
        )
        self.assertTrue(
            self.validate(
                self.text.replace(
                    parse_ledger(self.text)["release_code_sha"], "not-a-commit"
                )
            )
        )

    def test_cli_returns_nonzero_for_observed_mismatch(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "integration" / "explorer_results_gate.py"),
                str(RESULTS),
                "--observed-test-count",
                str(self.count + 1),
                "--invoke-log",
                str(ROOT / "native" / "windows-modern-context-menu" / "SkillMagnetMenu.tsv"),
                "--field-evidence",
                str(ROOT / "native" / "windows-modern-context-menu" / "SkillMagnetMenu.tsv"),
            ],
            # The count mismatch is under test; a non-field fixture is enough
            # because the gate reports every independent failure in one run.
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("full_test_count mismatch", completed.stdout)

    def test_windows_cannot_select_cross_platform_field_bypass(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            with self.assertRaises(SystemExit) as raised:
                main([str(RESULTS), "--cross-platform-artifact-only"])
        self.assertEqual(raised.exception.code, 2)

    def test_wheel_payload_hash_is_cross_platform_but_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            windows_wheel = root / "windows.whl"
            macos_wheel = root / "macos.whl"
            changed_wheel = root / "changed.whl"
            fixtures = (
                (windows_wheel, b"line one\r\nline two\r\n", b"windows record"),
                (macos_wheel, b"line one\nline two\n", b"macos record"),
                (changed_wheel, b"line one\nchanged\n", b"changed record"),
            )
            for wheel, text, record in fixtures:
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("skill_magnet/data.txt", text)
                    archive.writestr("skill_magnet/data.bin", b"\0\r\n\xff")
                    archive.writestr("skill_magnet-0.3.0.dist-info/RECORD", record)
            self.assertEqual(
                wheel_payload_sha256(windows_wheel),
                wheel_payload_sha256(macos_wheel),
            )
            self.assertNotEqual(
                wheel_payload_sha256(macos_wheel),
                wheel_payload_sha256(changed_wheel),
            )

    def test_field_evidence_hash_and_both_explorer_sources_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "invoke.log"
            def native_sequence(
                source: str,
                project: str,
                invocation: str,
                second: int,
                launch: str,
                process_id: int,
                terminal_event: str = "child_running",
            ) -> str:
                template = "c" * 64
                prefix = f"2026-09-05T00:00:{second:02d}."
                terminal_detail = str(process_id) if terminal_event == "child_running" else "0"
                rows = (
                    ("000", "invoke_enter", template, "0", "unavailable"),
                    ("001", "selection_succeeded", template, "0", project),
                    ("002", "create_process_succeeded", launch, str(process_id), project),
                    ("003", terminal_event, launch, terminal_detail, project),
                )
                return "".join(
                    f"{prefix}{millis}Z\tevent={event}\tcommand_sha256={command}"
                    f"\tdetail={detail}\tselection_source={source}"
                    f"\tproject_sha256={project_value}\tinvocation_id={invocation}\r\n"
                    for millis, event, command, detail, project_value in rows
                )

            evidence_text = (
                native_sequence(
                    "selected_item", "a" * 64, "1" * 32, 1, "d" * 64, 4242
                )
                + native_sequence(
                    "selected_item", "a" * 64, "2" * 32, 2, "d" * 64,
                    4243, "child_exited",
                )
                + native_sequence(
                    "background_site", "c" * 64, "3" * 32, 3, "a" * 64, 4244
                )
                + native_sequence(
                    "background_site", "b" * 64, "4" * 32, 4, "e" * 64, 4343
                )
                + native_sequence(
                    "background_site", "b" * 64, "5" * 32, 5, "e" * 64,
                    4444, "child_exited",
                )
                + native_sequence(
                    "background_site", "d" * 64, "6" * 32, 6, "f" * 64, 4545
                )
                + native_sequence(
                    "background_site", "b" * 64, "7" * 32, 7, "e" * 64, 4646
                )
                + native_sequence(
                    "selected_item", "a" * 64, "8" * 32, 8, "d" * 64, 4747
                )
                + native_sequence(
                    "selected_item", "e" * 64, "9" * 32, 9, "b" * 64, 4848
                )
            )
            evidence.write_bytes(evidence_text.encode("utf-16-le"))
            ledger = {
                "windows_explorer_field_invoke_log_sha256": hashlib.sha256(
                    evidence.read_bytes()
                ).hexdigest()
            }
            self.assertEqual(validate_field_evidence(ledger, evidence), [])
            evidence.write_bytes("invoke_enter\n".encode("utf-16-le"))
            self.assertTrue(validate_field_evidence(ledger, evidence))

    def test_field_bundle_binds_gui_observations_to_native_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, signed_payload = self._field_fixture(
                Path(temporary)
            )
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ) as verifier:
                self.assertEqual(
                    validate_field_bundle(ledger, bundle_path, invoke_log, ROOT), []
                )
            verifier.assert_called_once()
            self.assertEqual(verifier.call_args.args[0], signed_payload)
            self.assertEqual(verifier.call_args.args[1], b"unit-test-detached-cms")

    def test_field_bundle_rejects_selector_label_id_or_combo_tampering(self) -> None:
        mutations = (
            (
                "internal-id",
                lambda bundle: bundle["selector_contract"]["configured_choices"][0].__setitem__(
                    "pack_id", "forged-pack"
                ),
            ),
            (
                "visible-label",
                lambda bundle: bundle["selector_contract"]["configured_choices"][0].__setitem__(
                    "label", "Skill Pack: forged"
                ),
            ),
            (
                "duplicate-selector-combo",
                lambda bundle: bundle["selector_contract"].__setitem__(
                    "exact_selector_combo_count", 2
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(
                    Path(temporary)
                )
                mutate(bundle)
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                self.assertTrue(
                    any("selector contract does not exactly match" in error for error in errors),
                    errors,
                )

    def test_field_bundle_rejects_raw_selector_label_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
            entries[2]["data"]["selection_choice_labels"][0] = "Skill: forged"
            self._replace_transcript(bundle, entries)
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("selected_item GUI claims mismatch" in error for error in errors), errors)

    def test_field_bundle_rejects_manager_registration_and_runtime_claim_tampering(self) -> None:
        mutations = (
            (
                "manager-focus",
                4,
                lambda data: data.__setitem__(
                    "same_folder_repeat_focused_existing_manager", False
                ),
                "Library Manager claims do not bind",
            ),
            (
                "registration-recovery",
                12,
                lambda data: data.__setitem__("actionable_recovery_visible", False),
                "registration claims do not bind",
            ),
            (
                "runtime-projectless",
                13,
                lambda data: data.__setitem__("projectless_semantics_visible", False),
                "runtime-skill claims do not bind",
            ),
            (
                "runtime-read-only",
                13,
                lambda data: data.__setitem__("read_only", False),
                "runtime-skill claims do not bind",
            ),
        )
        for label, index, mutate, expected in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(
                    Path(temporary)
                )
                transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
                entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
                mutate(entries[index]["data"])
                self._replace_transcript(bundle, entries)
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_field_bundle_rejects_legacy_summary_only_schema_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            bundle["schema_version"] = 1
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn("field bundle schema_version must be 4", errors)

    def test_field_bundle_rejects_native_source_manifest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["native_source_manifest"]
            document = json.loads(base64.b64decode(artifact["bytes_base64"]))
            document["inputs"][0]["sha256"] = "0" * 64
            changed = (json.dumps(document, separators=(",", ":")) + "\n").encode()
            digest = hashlib.sha256(changed).hexdigest()
            artifact.update(
                bytes_base64=base64.b64encode(changed).decode(),
                size=len(changed),
                sha256=digest,
            )
            bundle["hashes"]["native_source_manifest_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("release native source inputs" in error for error in errors), errors)

    def test_field_collector_runs_full_native_invoke_contract_probe(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        probe = collector[collector.index("$contractProbeLines = @(") :]
        probe = probe[: probe.index("$contractProbeBytes")]
        self.assertIn("contract_test.py", probe)
        self.assertRegex(probe, r"--invoke\s+\$contractProbeRoot")
        self.assertIn('isolated_contract_probe_mode = "full-invoke"', collector)

    def test_field_collector_normal_exit_uses_the_owned_cleanup_boundary(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        finalizer = collector[collector.rindex("finally {") :]
        self.assertLess(
            finalizer.index("Register-FieldOwnedProcessesFromInvokeLog"),
            finalizer.index("Close-FieldOwnedUiAndReleaseLease"),
        )
        self.assertLess(
            finalizer.index("Close-FieldOwnedUiAndReleaseLease"),
            finalizer.index("$window.Quit()"),
        )
        self.assertLess(finalizer.index("$window.Quit()"), finalizer.index("Remove-Item"))

    def test_field_collector_mid_assert_recovers_launched_processes_from_log(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        recovery = collector[
            collector.index("function Register-FieldOwnedProcessesFromInvokeLog") :
            collector.index("function Wait-NativeSequence")
        ]
        self.assertIn("$script:FieldInitialInvokeLineCount", recovery)
        self.assertIn('event -eq "selection_succeeded"', recovery)
        self.assertIn('event -eq "create_process_succeeded"', recovery)
        self.assertIn("$script:FieldOwnedProjectDigests.ContainsKey", recovery)
        self.assertIn("Register-FieldOwnedProcess", recovery)

    def test_field_collector_user_closed_process_is_an_idempotent_cleanup(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        identity_test = collector[
            collector.index("function Test-FieldProcessIdentity") :
            collector.index("function Read-FieldContextOwner")
        ]
        cleanup = collector[
            collector.index("function Close-FieldOwnedUiAndReleaseLease") :
            collector.index("function Get-UiaControlValues")
        ]
        self.assertIn("if ($null -eq $current) { return $false }", identity_test)
        self.assertIn("if (-not (Test-FieldProcessIdentity $identity)) { continue }", cleanup)
        self.assertIn("-ErrorAction SilentlyContinue", cleanup)

    def test_field_collector_duplicate_launches_are_registered_by_invocation(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        sequence = collector[
            collector.index("function Wait-NativeSequence") :
            collector.index("function Assert-BusyMessageAndClose")
        ]
        ownership = collector[
            collector.index("function Register-FieldOwnedProcess") :
            collector.index("function Close-FieldOwnedUiAndReleaseLease")
        ]
        self.assertIn("Register-FieldOwnedProcess $processId $id", sequence)
        self.assertIn("$identity.invocation_id = $InvocationId", ownership)
        self.assertIn("Fast duplicate launchers can exit before registration", ownership)

    def test_field_collector_preserves_preexisting_ui_and_owner_generation(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8")
        ownership = collector[
            collector.index("function Register-FieldOwnedProcess") :
            collector.index("function Close-FieldOwnedUiAndReleaseLease")
        ]
        cleanup = collector[
            collector.index("function Close-FieldOwnedUiAndReleaseLease") :
            collector.index("function Get-UiaControlValues")
        ]
        self.assertIn("$script:FieldPreExistingProcessIdentities[$key]", ownership)
        self.assertIn("start_time_utc_ticks", ownership)
        self.assertIn("$script:FieldExpectedExecutablePath", ownership)
        self.assertIn("$script:FieldPreExistingOwnerGeneration", ownership)
        self.assertIn("Test-FieldProcessIdentity $identity", cleanup)
        self.assertIn("$script:FieldOwnedOwnerTokens.ContainsKey", cleanup)
        self.assertIn("Never infer ownership after exit", cleanup)
        self.assertNotIn("$ownedIdentity", cleanup)
        self.assertNotIn('Get-VisibleNamedElements "Skill Magnet', cleanup)

    def test_field_bundle_rejects_native_dll_binding_marker_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["command_dll"]
            changed = base64.b64decode(artifact["bytes_base64"]).replace(
                "skill-magnet-native-source-v1:".encode("utf-16-le"),
                "skill-magnet-native-source-v0:".encode("utf-16-le"),
            )
            digest = hashlib.sha256(changed).hexdigest()
            artifact.update(
                bytes_base64=base64.b64encode(changed).decode(),
                size=len(changed),
                sha256=digest,
            )
            bundle["hashes"]["dll_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("marker is missing or duplicated" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_msix_payload_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["signed_msix"]
            changed = base64.b64decode(artifact["bytes_base64"]).replace(
                b"skill-magnet-menu-v4", b"skill-magnet-menu-v0", 1
            )
            digest = hashlib.sha256(changed).hexdigest()
            artifact.update(
                bytes_base64=base64.b64encode(changed).decode(),
                size=len(changed),
                sha256=digest,
            )
            bundle["hashes"]["signed_msix_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("signed MSIX does not bind" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_raw_uia_transcript_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
            entries[3]["data"]["invocation_id"] = "3" * 32
            changed = (
                "\n".join(
                    json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                    for entry in entries
                )
                + "\n"
            ).encode("utf-8")
            digest = hashlib.sha256(changed).hexdigest()
            bundle["uia_transcript"]["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            bundle["uia_transcript"]["sha256"] = digest
            bundle["hashes"]["uia_transcript_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any("does not bind the exact native sequence" in error for error in errors),
                errors,
            )

    def test_field_bundle_rejects_gui_from_another_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
            entries[2]["data"]["element"]["process_id"] = 31337
            changed = (
                "\n".join(
                    json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                    for entry in entries
                )
                + "\n"
            ).encode("utf-8")
            digest = hashlib.sha256(changed).hexdigest()
            bundle["uia_transcript"]["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            bundle["uia_transcript"]["sha256"] = digest
            bundle["hashes"]["uia_transcript_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any("GUI process is not the launched native child" in error for error in errors),
                errors,
            )

    def test_field_bundle_rejects_rehashed_recovery_identity_tampering(self) -> None:
        mutations = (
            (
                "same-folder-existing-window",
                9,
                lambda data: data["element"].__setitem__("runtime_id", [42, 999]),
                "did not preserve the existing GUI identity",
            ),
            (
                "different-folder-dialog-process",
                10,
                lambda data: data["element"].__setitem__("process_id", 31337),
                "busy dialog belongs to another process",
            ),
            (
                "closed-window-original-handle",
                11,
                lambda data: data.__setitem__("original_native_window_handle", 987654),
                "relaunch claims do not bind native evidence",
            ),
        )
        for label, entry_index, mutate, expected in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
                transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
                entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
                mutate(entries[entry_index]["data"])
                self._replace_transcript(bundle, entries)
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_field_bundle_requires_all_three_raw_recovery_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
            del entries[10]
            for sequence, entry in enumerate(entries, 1):
                entry["sequence"] = sequence
            self._replace_transcript(bundle, entries)
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("exactly 14 events" in error for error in errors), errors)
            self.assertTrue(any("recovery UIAutomation events" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_installed_config_byte_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            changed = b'{"synthetic":"config"}\n'
            digest = hashlib.sha256(changed).hexdigest()
            artifact = bundle["artifacts"]["config"]
            artifact["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            artifact["size"] = len(changed)
            artifact["sha256"] = digest
            bundle["hashes"]["config_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("config bytes differ" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_installed_appx_manifest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["appx_manifest"]
            changed = base64.b64decode(artifact["bytes_base64"]) + b" "
            digest = hashlib.sha256(changed).hexdigest()
            artifact["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            artifact["size"] = len(changed)
            artifact["sha256"] = digest
            bundle["hashes"]["appx_manifest_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("AppxManifest.xml bytes differ" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_non_pe_dll_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["command_dll"]
            changed = b"hand-written DLL claim"
            digest = hashlib.sha256(changed).hexdigest()
            artifact["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            artifact["size"] = len(changed)
            artifact["sha256"] = digest
            bundle["hashes"]["dll_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("not a substantive PE image" in error for error in errors), errors)

    def test_field_bundle_rejects_rehashed_installed_menu_command_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["menu_manifest"]
            changed = base64.b64decode(artifact["bytes_base64"]).replace(
                b" --launcher\n", b" --launcher --synthetic\n"
            )
            self.assertNotEqual(changed, base64.b64decode(artifact["bytes_base64"]))
            digest = hashlib.sha256(changed).hexdigest()
            artifact["bytes_base64"] = base64.b64encode(changed).decode("ascii")
            artifact["size"] = len(changed)
            artifact["sha256"] = digest
            bundle["hashes"]["menu_manifest_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("menu command contract mismatch" in error for error in errors), errors)

    def test_field_bundle_rejects_unsigned_handwritten_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=["field bundle detached CMS attestation is invalid: unit fixture"],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("detached CMS attestation is invalid" in error for error in errors), errors)

    def test_field_bundle_rejects_signer_not_pinned_by_release_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            ledger["windows_explorer_field_signer_thumbprint"] = "8" * 40
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn(
                "field bundle signer thumbprint does not match the release ledger pin",
                errors,
            )

    def test_field_bundle_rejects_evidence_replayed_for_another_release_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            bundle["release_code_sha"] = "8" * 40
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn(
                "field bundle release_code_sha does not match the release ledger",
                errors,
            )

    def test_field_bundle_rejects_old_installed_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            bundle["python_runtime"]["module_version"] = "0.5.0"
            bundle["python_runtime"]["payload_sha256"] = "5" * 64
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn("field bundle installed Python package version mismatch", errors)
            self.assertIn("field bundle installed Python runtime differs from release inputs", errors)

    def test_field_evidence_rejects_extra_invocation_even_when_hashes_are_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            extra = self._native_rows(
                "selected_item", "c" * 64, "6" * 32, "4" * 64, "5" * 64, 7,
                4747,
            )[0]
            changed_log = invoke_log.read_bytes() + (extra + "\r\n").encode("utf-16-le")
            invoke_log.write_bytes(changed_log)
            changed_digest = hashlib.sha256(changed_log).hexdigest()
            ledger["windows_explorer_field_invoke_log_sha256"] = changed_digest
            bundle["hashes"]["invoke_log_sha256"] = changed_digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("exactly 36 native records" in error for error in errors), errors)

    def test_field_evidence_rejects_invalid_utf16_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "invoke.log"
            evidence.write_bytes(b"odd")
            ledger = {
                "windows_explorer_field_invoke_log_sha256": hashlib.sha256(
                    evidence.read_bytes()
                ).hexdigest()
            }
            errors = validate_field_evidence(ledger, evidence)
            self.assertTrue(any("not valid UTF-16LE" in error for error in errors))

    def test_release_provenance_rejects_untracked_artifact_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "src").mkdir()
            (repository / "src" / "tracked.py").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "add", "src/tracked.py"], cwd=repository, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-m",
                    "tracked",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            release_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (repository / "native").mkdir()
            (repository / "native" / "untracked.cpp").write_text("// missing from release\n", encoding="utf-8")
            errors = validate_release_provenance(
                repository,
                {
                    "release_code_sha": release_sha,
                    "wheel_payload_sha256": "0" * 64,
                },
                None,
            )
            self.assertTrue(any("untracked artifact inputs" in error for error in errors))

    def test_release_provenance_includes_policy_and_current_product_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "policy").mkdir()
            (repository / "docs" / "images").mkdir(parents=True)
            (repository / "README.md").write_text("current\n", encoding="utf-8")
            (repository / "policy" / "product-policy.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (repository / "docs" / "mvp-redesign.md").write_text(
                "current\n", encoding="utf-8"
            )
            (repository / "docs" / "images" / "library-manager.png").write_bytes(
                b"current-image"
            )
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-m",
                    "release",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            release_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (repository / "README.md").write_text("stale\n", encoding="utf-8")
            (repository / "policy" / "product-policy.json").write_text(
                "{\"stale\": true}\n", encoding="utf-8"
            )
            (repository / "docs" / "images" / "library-manager.png").write_bytes(
                b"stale-image"
            )
            errors = validate_release_provenance(
                repository,
                {
                    "release_code_sha": release_sha,
                    "wheel_payload_sha256": "0" * 64,
                },
                None,
            )
            joined = "\n".join(errors)
            self.assertIn("README.md", joined)
            self.assertIn("policy/product-policy.json", joined)
            self.assertIn("docs/images/library-manager.png", joined)


if __name__ == "__main__":
    unittest.main()
