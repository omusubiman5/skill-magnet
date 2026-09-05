from __future__ import annotations

import os
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
import time
from pathlib import Path
from unittest import mock

import integration.explorer_results_gate as results_gate
from integration.explorer_results_gate import (
    _configured_repository_url,
    _configured_selector_choices,
    _field_attestation_payload,
    _normalized_text_bytes,
    _normalized_windows_powershell_bytes,
    _native_source_manifest_from_repository,
    _ordered_selector_label_sha256,
    _release_runtime_payload_sha256,
    _selector_choice_map_sha256,
    _text_sha256,
    _validate_ui_owner_receipt_schema,
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

    def test_field_collector_records_root_dispatch_without_raw_content(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        menu = collector[
            collector.index("function Invoke-VisibleSkillMagnetRoot") :
            collector.index("function Test-ExactStringSequence")
        ]
        for event in (
            "uia_root_bound", "invoke_call_enter", "invoke_call_return",
            "invoke_call_error",
        ):
            self.assertIn(f'Write-FieldActionDiagnostic "{event}"', menu)
        helper = collector[
            collector.index("function Write-FieldActionDiagnostic") :
            collector.index("function New-FieldUiIdentityAnchor")
        ]
        self.assertIn("runtime_key_sha256", helper)
        self.assertIn("[IO.FileMode]::Append", helper)
        self.assertNotIn("SelectedName", helper)
        self.assertNotIn("request", helper.casefold())

    @staticmethod
    def _write_runtime_repository(root: Path) -> tuple[Path, Path]:
        package = root / "src" / "skill_magnet"
        native = root / "native" / "windows-modern-context-menu"
        package.mkdir(parents=True)
        native.mkdir(parents=True)
        (package / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
        (native / "build.ps1").write_text("source\n", encoding="utf-8")
        (root / "skill-magnet.json").write_text("{}\n", encoding="utf-8")
        return package, native

    @staticmethod
    def _field_runtime_walker_namespace() -> dict[str, object]:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        start_marker = "$runtimeTreeWalker = @'\n"
        end_marker = "\n'@\n\n$runtimeProbe = $runtimeTreeWalker + @'"
        start = collector.index(start_marker) + len(start_marker)
        end = collector.index(end_marker, start)
        namespace: dict[str, object] = {"__name__": "field_runtime_walker_test"}
        exec(compile(collector[start:end], "field-runtime-tree-walker", "exec"), namespace)
        return namespace

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
        configured_label_digest = _ordered_selector_label_sha256(configured_choices)
        selected_label_digest = _text_sha256(configured_labels[0])
        configured_remote = _configured_repository_url(config_payload)

        def ui_receipt_evidence(
            role: str,
            native_role: str,
            phase: str,
            widget_id: str,
            claim_field: str,
            claim_sha256: str,
            ordinal: int,
            invocation_id: str,
            project_sha256: str,
            target_sha256: str,
            process_id: int,
            native_sequence_sha256: str,
        ) -> dict[str, object]:
            generation = f"{ordinal:032x}"
            published = f"2026-09-05T00:00:{ordinal:02d}Z"
            state = (
                {
                    "language_sha256": "1" * 64,
                    "selection_mode_sha256": "2" * 64,
                    "processing": False,
                    "details_visible": False,
                }
                if phase == "context_selection"
                else {"processing": False, "register_selected": True}
            )
            widget: dict[str, object] = {
                "id": widget_id,
                "role": "button" if claim_field == "text_sha256" else "entry",
                "state": {"configured": "normal", "enabled": True},
                "viewable": True,
                "hwnd": 500 + ordinal,
                "client": {"x": 1, "y": 2, "width": 3, "height": 4},
                "screen": {"x": 1, "y": 2, "width": 3, "height": 4},
                claim_field: claim_sha256,
            }
            surface: dict[str, object] = {
                "schema_version": 1,
                "generation": generation,
                "pid": process_id,
                "phase": phase,
                "window": {
                    "hwnd": 400 + ordinal,
                    "title_sha256": "3" * 64,
                    "client": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "screen": {"x": 1, "y": 2, "width": 3, "height": 4},
                },
                "state": state,
                "widgets": [widget],
                "revision": ordinal,
                "published_at_utc": published,
            }
            receipt: dict[str, object] = {
                "schema_version": 2,
                "owner_kind": "context_launcher",
                "pid": process_id,
                "process_instance_id": f"{ordinal + 16:032x}",
                "process_started_at_unix_ns": ordinal,
                "target_sha256": target_sha256,
                "generation": generation,
                "phase": phase,
                "window_handle": 400 + ordinal,
                "revision": ordinal,
                "published_at_utc": published,
                "ui_surface": surface,
            }
            canonical = lambda value: json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            return {
                "role": role,
                "native_role": native_role,
                "invocation_id": invocation_id,
                "project_sha256": project_sha256,
                "target_sha256": target_sha256,
                "process_id": process_id,
                "native_sequence_sha256": native_sequence_sha256,
                "transcript_session_id": "f" * 32,
                "phase": phase,
                "process_instance_id": receipt["process_instance_id"],
                "generation": generation,
                "revision": ordinal,
                "claim_widget_id": widget_id,
                "claim_field": claim_field,
                "claim_sha256": claim_sha256,
                "receipt_sha256": hashlib.sha256(canonical(receipt)).hexdigest(),
                "surface_sha256": hashlib.sha256(canonical(surface)).hexdigest(),
                "receipt": receipt,
            }

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
        native_lines = [
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
        ]

        def native_digest(role: str) -> str:
            return hashlib.sha256(
                (("\r\n".join(native_by_role[role])) + "\r\n").encode("utf-16-le")
            ).hexdigest()

        receipt_native = {
            "selected_manager_click": ("selected_item", "1" * 32, "a" * 64, "1" * 64, 4242),
            "manager_remote": ("selected_item", "1" * 32, "a" * 64, "1" * 64, 4242),
            "background_selection": ("background_site", "4" * 32, "b" * 64, "2" * 64, 4343),
            "registration_click": (
                "missing_skill_registration", "8" * 32, "a" * 64, "1" * 64, 4747
            ),
            "registration_source": (
                "missing_skill_registration", "8" * 32, "a" * 64, "1" * 64, 4747
            ),
            "runtime_projectless": (
                "runtime_skill_projectless", "9" * 32, "e" * 64, "3" * 64, 4848
            ),
        }

        identity_anchors = (
            ("selected_item", "a" * 64, "1" * 64, "unavailable", "1" * 32),
            ("background_site", "b" * 64, "2" * 64, "unavailable", "4" * 32),
            (
                "missing_skill_registration", "a" * 64, "1" * 64,
                "5" * 64, "8" * 32,
            ),
            (
                "runtime_skill_projectless", "e" * 64, "3" * 64,
                "unavailable", "9" * 32,
            ),
        )
        identity_lines = [
            f"2026-09-05T00:00:10.{index:03d}Z\tevent=ui_identity_bound"
            f"\tnative_role={role}\tproject_sha256={project}"
            f"\ttarget_sha256={target}"
            f"\tregistration_source_sha256={registration_source}"
            f"\tinvocation_id={invocation}"
            for index, (role, project, target, registration_source, invocation)
            in enumerate(identity_anchors, 1)
        ]
        invoke_payload = ("\r\n".join(native_lines + identity_lines) + "\r\n").encode(
            "utf-16-le"
        )
        invoke_log = root / "invoke.log"
        invoke_log.write_bytes(invoke_payload)

        def bound_receipt(
            role: str,
            phase: str,
            widget_id: str,
            claim_field: str,
            claim_sha256: str,
            ordinal: int,
        ) -> dict[str, object]:
            native_role, invocation, project, target, process_id = receipt_native[role]
            return ui_receipt_evidence(
                role,
                native_role,
                phase,
                widget_id,
                claim_field,
                claim_sha256,
                ordinal,
                invocation,
                project,
                target,
                process_id,
                native_digest(native_role),
            )

        ui_receipts = [
            bound_receipt("selected_manager_click", "context_selection", "library_manager", "text_sha256", _text_sha256("Library Manager"), 1),
            bound_receipt("manager_remote", "library_manager", "configured_remote", "value_sha256", _text_sha256(configured_remote), 2),
            bound_receipt("background_selection", "context_selection", "selection_choice", "values_sha256", configured_label_digest, 3),
            bound_receipt("registration_click", "context_selection", "register_selected", "text_sha256", _text_sha256("このフォルダーのスキルを登録"), 4),
            bound_receipt("registration_source", "library_manager", "registration_source", "value_sha256", "5" * 64, 5),
            bound_receipt("runtime_projectless", "context_selection", "project", "text_sha256", _text_sha256("作業対象フォルダー: 指定なし（デスクトップアプリが新規タスク用領域を自動作成）"), 6),
        ]

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
                    "selection_choice_values_sha256": configured_label_digest,
                    "selected_choice_value_sha256": selected_label_digest,
                    "selection_combo_exact_match_count": 1,
                    "library_manager_button_count": 1,
                    "library_manager_button_text_sha256": _text_sha256(
                        "Library Manager"
                    ),
                    "register_button_count": 1,
                    "register_button_text_sha256": _text_sha256(
                        "このフォルダーのスキルを登録"
                    ),
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
                            "selection_choice_values_sha256": configured_label_digest,
                            "selected_choice_value_sha256": selected_label_digest,
                            "selection_combo_exact_match_count": 1,
                            "library_manager_button_count": 1,
                            "library_manager_button_text_sha256": _text_sha256(
                                "Library Manager"
                            ),
                            "register_button_count": 1,
                            "register_button_text_sha256": _text_sha256(
                                "このフォルダーのスキルを登録"
                            ),
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
            "configured_remote_sha256": _text_sha256(configured_remote),
            "configured_remote_visible": True,
            "create_button_count": 1,
            "create_button_text_sha256": _text_sha256("新規登録"),
            "update_button_count": 1,
            "update_button_text_sha256": _text_sha256("選択項目を更新"),
            "delete_button_count": 1,
            "delete_button_text_sha256": _text_sha256("選択項目を削除"),
            "reload_button_count": 1,
            "reload_button_text_sha256": _text_sha256("再読込"),
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
                "configured_remote_sha256": _text_sha256(configured_remote),
                "configured_remote_visible": True,
                "create_button_count": 1,
                "create_button_text_sha256": _text_sha256("新規登録"),
                "update_button_count": 1,
                "update_button_text_sha256": _text_sha256("選択項目を更新"),
                "delete_button_count": 1,
                "delete_button_text_sha256": _text_sha256("選択項目を削除"),
                "reload_button_count": 1,
                "reload_button_text_sha256": _text_sha256("再読込"),
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
            "registration_source_sha256": "5" * 64,
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
                "registration_source_sha256": "5" * 64,
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
            }
            if name != "config":
                artifacts[name]["bytes_base64"] = base64.b64encode(payload).decode(
                    "ascii"
                )
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
            "schema_version": 5,
            "release_version": "0.5.9",
            "release_code_sha": "7" * 40,
            "field_status": "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_0_5_9",
            "observed_at_utc": "2026-09-05T00:00:09.900Z",
            "collector_sha256": hashlib.sha256(
                _normalized_windows_powershell_bytes(collector.read_bytes())
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
            "ui_receipts": ui_receipts,
            "selector_contract": {
                "choice_map_sha256": _selector_choice_map_sha256(configured_choices),
                "ordered_label_sha256": configured_label_digest,
                "choice_count": len(configured_choices),
                "selected_label_sha256": selected_label_digest,
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

    def test_release_runtime_digest_excludes_generated_native_output_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            _, native = self._write_runtime_repository(repository)
            expected = _release_runtime_payload_sha256(repository)

            generated = native / "out"
            generated.mkdir()
            (generated / "SkillMagnetCommand.dll").write_bytes(b"generated")
            self.assertEqual(_release_runtime_payload_sha256(repository), expected)

            (native / "build.ps1").write_text("changed\n", encoding="utf-8")
            self.assertNotEqual(_release_runtime_payload_sha256(repository), expected)

    def test_release_runtime_digest_rejects_reparse_even_for_excluded_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            _, native = self._write_runtime_repository(repository)
            generated = native / "out"
            generated.mkdir()
            original = results_gate._runtime_path_is_reparse

            def mark_output_as_reparse(path: Path, metadata: os.stat_result) -> bool:
                return path.name == "out" or original(path, metadata)

            with mock.patch.object(
                results_gate,
                "_runtime_path_is_reparse",
                side_effect=mark_output_as_reparse,
            ):
                with self.assertRaisesRegex(ValueError, "reparse points are forbidden"):
                    _release_runtime_payload_sha256(repository)

    def test_release_runtime_digest_enforces_entry_file_total_and_time_limits(self) -> None:
        cases = (
            ("_RUNTIME_TREE_MAX_ENTRIES", 0, "entry count exceeds"),
            ("_RUNTIME_TREE_MAX_FILE_BYTES", 1, "file size"),
            ("_RUNTIME_TREE_MAX_TOTAL_BYTES", 1, "total bytes exceed"),
            ("_RUNTIME_TREE_MAX_SECONDS", -1.0, "deadline expired"),
        )
        for setting, value, expected in cases:
            with self.subTest(setting=setting):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    self._write_runtime_repository(repository)
                    with mock.patch.object(results_gate, setting, value):
                        with self.assertRaisesRegex(ValueError, expected) as raised:
                            _release_runtime_payload_sha256(repository)
                    self.assertIn("rebuild/reinstall", str(raised.exception))

    def test_release_runtime_stable_read_detects_file_swap_before_content_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            payload = parent / "payload.py"
            payload.write_bytes(b"safe")
            metadata, identity = results_gate._runtime_checked_metadata(
                payload, label="payload", directory=False
            )
            _, parent_identity = results_gate._runtime_checked_metadata(
                parent, label="parent", directory=True
            )
            changed = mock.Mock()
            for attribute in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_mtime",
                "st_mtime_ns",
                "st_ctime",
                "st_ctime_ns",
                "st_size",
            ):
                setattr(changed, attribute, getattr(metadata, attribute))
            changed.st_ino = int(metadata.st_ino) + 1
            budget: dict[str, int | float] = {
                "deadline": results_gate.time.monotonic() + 5,
                "entries": 0,
                "bytes": 0,
            }
            real_read = os.read
            with mock.patch.object(results_gate.os, "fstat", return_value=changed), mock.patch.object(
                results_gate.os, "read", side_effect=real_read
            ) as read:
                with self.assertRaisesRegex(ValueError, "opened file identity differs"):
                    results_gate._runtime_stable_read(
                        payload,
                        label="payload",
                        metadata=metadata,
                        identity=identity,
                        parent=parent,
                        parent_identity=parent_identity,
                        budget=budget,
                    )
            read.assert_not_called()

    def test_release_runtime_tree_detects_directory_swap_before_content_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "payload.py").write_bytes(b"safe")
            root_metadata = os.lstat(root)
            original = results_gate._runtime_metadata_identity
            directory_calls = 0

            def changed_directory_identity(
                metadata: os.stat_result, *, directory: bool
            ) -> tuple[int, ...]:
                nonlocal directory_calls
                identity = original(metadata, directory=directory)
                if (
                    directory
                    and metadata.st_dev == root_metadata.st_dev
                    and metadata.st_ino == root_metadata.st_ino
                ):
                    directory_calls += 1
                    if directory_calls >= 3:
                        return (identity[0], identity[1] + 1, *identity[2:])
                return identity

            budget: dict[str, int | float] = {
                "deadline": results_gate.time.monotonic() + 5,
                "entries": 0,
                "bytes": 0,
            }
            real_read = os.read
            with mock.patch.object(
                results_gate,
                "_runtime_metadata_identity",
                side_effect=changed_directory_identity,
            ), mock.patch.object(results_gate.os, "read", side_effect=real_read) as read:
                with self.assertRaisesRegex(ValueError, "changed during verification"):
                    results_gate._runtime_collect_tree(
                        root,
                        prefix="runtime/",
                        label="runtime",
                        budget=budget,
                        include_file=lambda relative: True,
                        skip_directory=lambda relative: False,
                    )
            read.assert_not_called()

    def test_field_runtime_walker_rejects_unknown_out_reparse_and_all_limits(self) -> None:
        cases = ("unknown_out", "reparse", "entries", "size", "total", "timeout")
        for case in cases:
            with self.subTest(case=case):
                namespace = self._field_runtime_walker_namespace()
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    payload = root / "payload.bin"
                    payload.write_bytes(b"safe")
                    if case == "unknown_out":
                        (root / "_native" / "windows-modern-context-menu" / "out").mkdir(
                            parents=True
                        )
                    elif case == "reparse":
                        junction = root / "junction"
                        junction.mkdir()
                        original = namespace["runtime_is_reparse"]
                        namespace["runtime_is_reparse"] = (
                            lambda path, metadata: path.name == "junction"
                            or original(path, metadata)
                        )
                    elif case == "size":
                        namespace["RUNTIME_MAX_FILE_BYTES"] = 1
                    elif case == "entries":
                        namespace["RUNTIME_MAX_ENTRIES"] = 0
                    elif case == "total":
                        namespace["RUNTIME_MAX_TOTAL_BYTES"] = 1
                    budget = {
                        "deadline": namespace["time"].monotonic()
                        + (-1 if case == "timeout" else 5),
                        "entries": 0,
                        "bytes": 0,
                    }
                    expected = {
                        "unknown_out": "residue directory",
                        "reparse": "reparse points are forbidden",
                        "entries": "entry count exceeds",
                        "size": "file size",
                        "total": "total bytes exceed",
                        "timeout": "deadline expired",
                    }[case]
                    with self.assertRaisesRegex(RuntimeError, expected):
                        namespace["runtime_collect_tree"](
                            root,
                            "skill_magnet/",
                            "installed runtime",
                            budget,
                            lambda relative: relative.suffix.lower() != ".pyc",
                            lambda relative: relative.name == "__pycache__",
                            lambda relative: relative.as_posix().casefold()
                            == "_native/windows-modern-context-menu/out",
                        )

    def test_field_runtime_walker_detects_file_and_directory_swaps_without_read(self) -> None:
        for case in ("file", "directory"):
            with self.subTest(case=case):
                namespace = self._field_runtime_walker_namespace()
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    payload = root / "payload.bin"
                    payload.write_bytes(b"safe")
                    real_read = os.read
                    if case == "file":
                        metadata, identity = namespace["runtime_checked_metadata"](
                            payload, "payload", False
                        )
                        _, parent_identity = namespace["runtime_checked_metadata"](
                            root, "parent", True
                        )
                        changed = mock.Mock()
                        for attribute in (
                            "st_dev",
                            "st_ino",
                            "st_mode",
                            "st_mtime",
                            "st_mtime_ns",
                            "st_ctime",
                            "st_ctime_ns",
                            "st_size",
                        ):
                            setattr(changed, attribute, getattr(metadata, attribute))
                        changed.st_ino = int(metadata.st_ino) + 1
                        budget = {
                            "deadline": namespace["time"].monotonic() + 5,
                            "entries": 0,
                            "bytes": 0,
                        }
                        with mock.patch.object(
                            namespace["os"], "fstat", return_value=changed
                        ), mock.patch.object(
                            namespace["os"], "read", side_effect=real_read
                        ) as read:
                            with self.assertRaisesRegex(RuntimeError, "opened file identity differs"):
                                namespace["runtime_stable_read"](
                                    payload,
                                    "payload",
                                    metadata,
                                    identity,
                                    root,
                                    parent_identity,
                                    budget,
                                )
                    else:
                        root_metadata = os.lstat(root)
                        original = namespace["runtime_identity"]
                        calls = 0

                        def shift(metadata: os.stat_result, directory: bool) -> tuple[int, ...]:
                            nonlocal calls
                            identity = original(metadata, directory)
                            if (
                                directory
                                and metadata.st_dev == root_metadata.st_dev
                                and metadata.st_ino == root_metadata.st_ino
                            ):
                                calls += 1
                                if calls >= 3:
                                    return (identity[0], identity[1] + 1, *identity[2:])
                            return identity

                        namespace["runtime_identity"] = shift
                        budget = {
                            "deadline": namespace["time"].monotonic() + 5,
                            "entries": 0,
                            "bytes": 0,
                        }
                        with mock.patch.object(
                            namespace["os"], "read", side_effect=real_read
                        ) as read:
                            with self.assertRaisesRegex(RuntimeError, "changed during verification"):
                                namespace["runtime_collect_tree"](
                                    root,
                                    "runtime/",
                                    "runtime",
                                    budget,
                                    lambda relative: True,
                                    lambda relative: False,
                                )
                    read.assert_not_called()

    def test_field_release_runtime_probe_matches_independent_gate_digest(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        start_marker = "$releaseRuntimeProbe = $runtimeTreeWalker + @'\n"
        end_marker = "\n'@\n$releaseRuntimeOutput = @($releaseRuntimeProbe |"
        start = collector.index(start_marker) + len(start_marker)
        end = collector.index(end_marker, start)
        namespace = self._field_runtime_walker_namespace()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["-", str(ROOT)]), mock.patch.object(
            sys, "stdout", output
        ):
            exec(
                compile(collector[start:end], "field-release-runtime-probe", "exec"),
                namespace,
            )
        self.assertEqual(
            output.getvalue().strip(),
            _release_runtime_payload_sha256(ROOT),
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
                + "2026-09-05T00:00:10.001Z\tevent=ui_identity_bound\tnative_role=selected_item"
                + f"\tproject_sha256={'a' * 64}\ttarget_sha256={'1' * 64}"
                + f"\tregistration_source_sha256=unavailable\tinvocation_id={'1' * 32}\r\n"
                + "2026-09-05T00:00:10.002Z\tevent=ui_identity_bound\tnative_role=background_site"
                + f"\tproject_sha256={'b' * 64}\ttarget_sha256={'2' * 64}"
                + f"\tregistration_source_sha256=unavailable\tinvocation_id={'4' * 32}\r\n"
                + "2026-09-05T00:00:10.003Z\tevent=ui_identity_bound\tnative_role=missing_skill_registration"
                + f"\tproject_sha256={'a' * 64}\ttarget_sha256={'1' * 64}"
                + f"\tregistration_source_sha256={'5' * 64}\tinvocation_id={'8' * 32}\r\n"
                + "2026-09-05T00:00:10.004Z\tevent=ui_identity_bound\tnative_role=runtime_skill_projectless"
                + f"\tproject_sha256={'e' * 64}\ttarget_sha256={'3' * 64}"
                + f"\tregistration_source_sha256=unavailable\tinvocation_id={'9' * 32}\r\n"
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
                lambda bundle: bundle["selector_contract"].__setitem__(
                    "choice_map_sha256", "0" * 64
                ),
            ),
            (
                "visible-label",
                lambda bundle: bundle["selector_contract"].__setitem__(
                    "ordered_label_sha256", "1" * 64
                ),
            ),
            (
                "selected-label",
                lambda bundle: bundle["selector_contract"].__setitem__(
                    "selected_label_sha256", "2" * 64
                ),
            ),
            (
                "choice-count",
                lambda bundle: bundle["selector_contract"].__setitem__(
                    "choice_count", 999
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

    def test_field_bundle_rejects_selector_label_digest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            entries = [json.loads(line) for line in transcript.decode("utf-8").splitlines()]
            entries[2]["data"]["selection_choice_values_sha256"] = "0" * 64
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

    def test_field_bundle_rejects_legacy_schema_v4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            bundle["schema_version"] = 4
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn("field bundle schema_version must be 5", errors)

    def test_field_bundle_v5_contains_no_raw_remote_labels_or_config_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, bundle_path, _, bundle, _ = self._field_fixture(Path(temporary))
            config_payload = (ROOT / "skill-magnet.json").read_bytes()
            remote = _configured_repository_url(config_payload)
            labels = [
                str(choice["label"])
                for choice in _configured_selector_choices(config_payload)
            ]
            serialized = bundle_path.read_text(encoding="utf-8")
            self.assertEqual(bundle["schema_version"], 5)
            self.assertNotIn("bytes_base64", bundle["artifacts"]["config"])
            self.assertNotIn(remote, serialized)
            for label in labels:
                self.assertNotIn(label, serialized)

    def test_field_bundle_rejects_duplicate_keys_size_and_reparse_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            payload = bundle_path.read_bytes().replace(
                b'"schema_version": 5,',
                b'"schema_version": 5, "schema_version": 5,',
                1,
            )
            bundle_path.write_bytes(payload)
            ledger["windows_explorer_field_bundle_sha256"] = hashlib.sha256(
                payload
            ).hexdigest()
            errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("duplicate JSON key" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            with mock.patch(
                "integration.explorer_results_gate._FIELD_BUNDLE_MAX_BYTES", 1
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn(
                "Windows Explorer field bundle size is outside the accepted range",
                errors,
            )
            with mock.patch(
                "integration.explorer_results_gate._is_reparse_or_link",
                side_effect=lambda path: path == bundle_path,
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn(
                "Windows Explorer field bundle must not be a link or reparse point",
                errors,
            )

    def test_field_bundle_rejects_duplicate_transcript_keys_and_plaintext_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            transcript = base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
            changed = transcript.replace(
                b'{"observed_at_utc":',
                b'{"sequence":1,"sequence":1,"observed_at_utc":',
                1,
            )
            digest = hashlib.sha256(changed).hexdigest()
            bundle["uia_transcript"]["bytes_base64"] = base64.b64encode(changed).decode()
            bundle["uia_transcript"]["sha256"] = digest
            bundle["hashes"]["uia_transcript_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(any("duplicate JSON key" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            bundle["package"]["package_full_name"] = r"C:\Users\example\secret"
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertIn("field bundle contains a plaintext local path", errors)

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

    def test_field_collector_is_windows_powershell_utf8_compatible(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_bytes()
        self.assertTrue(
            collector.startswith(b"\xef\xbb\xbf"),
            "Windows PowerShell 5.1 requires a UTF-8 BOM for the Japanese field script",
        )
        self.assertEqual(
            _normalized_windows_powershell_bytes(collector),
            _normalized_text_bytes(collector[3:]),
        )
        text = collector.decode("utf-8-sig")
        self.assertNotRegex(text, r"-I\s+-c\s+\$\w+Probe")
        for probe in ("runtimeProbe", "selectionProbe", "nativeProbe"):
            self.assertRegex(text, rf"\${probe}\s*\|\s*\n?\s*& .*? -I -")
        selection_probe = text[
            text.index("$selectionProbe = @'") : text.index("$selectionJson =")
        ]
        self.assertIn("ensure_ascii=True", selection_probe)

    def test_field_collector_powershell5_materializes_all_six_ui_receipts(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        receipt_function = collector[
            collector.index("function New-UiReceiptEvidence(") :
            collector.index("function Wait-MissingSkillRecoveryDialog")
        ]
        for variable in ("selectedManagerClick", "registrationClick"):
            assignment = collector[collector.index(f"${variable} =") :]
            assignment = assignment[: assignment.index("\n    $")]
            self.assertNotIn("Out-Null", assignment)
        probe = r'''
$ErrorActionPreference = "Stop"
$script:FieldSessionId = "f" * 32
function Assert-Field($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-NoRawReceiptDisplayValues($Receipt) {}
function Get-FieldUiSurfaceWidget($Surface, [string]$Id) {
    @($Surface.widgets | Where-Object { $_.id -ceq $Id })[0]
}
function Get-NativeSequenceSha256($Sequence) {
    $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Sequence.invocation_id)
    ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
}
function Get-CanonicalJsonSha256($Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Compress -Depth 20))
    ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
}
function New-ProbeObservation([int]$Ordinal, [string]$Phase, [string]$WidgetId, [string]$ClaimField, [string]$Claim, [int]$ProcessId, [string]$Target) {
    $widget = [ordered]@{ id=$WidgetId; role="button"; state=@{enabled=$true}; viewable=$true; hwnd=(500+$Ordinal); client=@{}; screen=@{} }
    $widget[$ClaimField] = $Claim
    $surface = [ordered]@{ widgets=@([pscustomobject]$widget) }
    $receipt = [ordered]@{ pid=$ProcessId; target_sha256=$Target; phase=$Phase; process_instance_id=("{0:x32}" -f (16+$Ordinal)); generation=("{0:x32}" -f $Ordinal); revision=$Ordinal; ui_surface=[pscustomobject]$surface }
    [pscustomobject]@{ ui_owner_receipt=[pscustomobject]$receipt }
}
'''
        probe += receipt_function
        probe += r'''
$roles = @(
    @("selected_manager_click", "context_selection", "library_manager", "text_sha256", 4242, "a"),
    @("manager_remote", "library_manager", "configured_remote", "value_sha256", 4242, "a"),
    @("background_selection", "context_selection", "selection_choice", "values_sha256", 4343, "b"),
    @("registration_click", "context_selection", "register_selected", "text_sha256", 4747, "a"),
    @("registration_source", "library_manager", "registration_source", "value_sha256", 4747, "a"),
    @("runtime_projectless", "context_selection", "project", "text_sha256", 4848, "e")
)
$uiReceipts = @()
for ($index = 0; $index -lt $roles.Count; $index++) {
    $item = $roles[$index]
    $project = ([string]$item[5]) * 64
    $target = "7" * 64
    $nativeRole = @("selected_item", "selected_item", "background_site", "missing_skill_registration", "missing_skill_registration", "runtime_skill_projectless")[$index]
    $native = [pscustomobject]@{ process_id=[int]$item[4]; project_sha256=$project; invocation_id=("{0:x32}" -f (1+$index)) }
    $observation = New-ProbeObservation (1+$index) ([string]$item[1]) ([string]$item[2]) ([string]$item[3]) ("9"*64) ([int]$item[4]) $target
    $uiReceipts += New-UiReceiptEvidence ([string]$item[0]) $observation ([string]$item[2]) ([string]$item[3]) $nativeRole $native $project $target
}
if ($uiReceipts.Count -ne 6 -or @($uiReceipts | Where-Object { $null -eq $_ }).Count -ne 0) { throw "receipt materialization failed" }
if (@($uiReceipts | Where-Object { $_.project_sha256 -ceq $_.target_sha256 }).Count -ne 0) { throw "project and target identities were conflated" }
[ordered]@{ ui_receipts=$uiReceipts } | ConvertTo-Json -Compress -Depth 30
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "receipt-probe.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(probe_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        bundle = json.loads(completed.stdout.strip())
        self.assertEqual(len(bundle["ui_receipts"]), 6)
        self.assertTrue(all(bundle["ui_receipts"]))

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
            collector.index("function Get-VisibleDescendantText")
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

    def test_field_collector_native_wait_rejects_ambiguous_and_failed_dispatch(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        sequence = collector[
            collector.index("function Wait-NativeSequence") :
            collector.index("function Assert-BusyMessageAndClose")
        ]
        self.assertIn("$enterRecords.Count -gt 1", sequence)
        self.assertIn("multiple native invoke_enter records", sequence)
        for event in (
            "selection_failed",
            "marker_missing",
            "create_process_failed",
            "child_wait_failed",
            "child_exit_read_failed",
            "child_process_failed",
        ):
            self.assertIn(f'"{event}"', sequence)
        self.assertIn("Native invocation failed: event=", sequence)
        self.assertIn("exit_code=", sequence)
        self.assertIn("Register-FieldOwnedProcess $processId $id", sequence)
        self.assertIn("observed_events=$lastObserved", sequence)

    def test_field_collector_checks_both_original_and_duplicate_process_ui(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        manager_flow = collector[
            collector.index("$managerSameSequence = Wait-NativeSequence") :
            collector.index("$managerDifferentSequence = Wait-NativeSequence")
        ]
        same_folder_flow = collector[
            collector.index("$sameSequence = Wait-NativeSequence") :
            collector.index("$differentSequence = Wait-NativeSequence")
        ]
        self.assertIn("$managerFieldProcessIds", manager_flow)
        self.assertIn("[int]$selectedSequence.process_id", manager_flow)
        self.assertIn("[int]$managerSameSequence.process_id", manager_flow)
        self.assertIn("$managerFieldProcessIds -contains", manager_flow)
        self.assertIn("$sameFieldProcessIds", same_folder_flow)
        self.assertIn("[int]$backgroundSequence.process_id", same_folder_flow)
        self.assertIn("[int]$sameSequence.process_id", same_folder_flow)
        self.assertIn("$sameFieldProcessIds -contains", same_folder_flow)

    def test_field_collector_scopes_root_to_the_context_menu_it_opened(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        context = collector[
            collector.index("function Open-ExplorerContextMenu") :
            collector.index("function Test-ExactStringSequence")
        ]
        self.assertIn("$preExistingRootKeys", context)
        self.assertIn("Get-UiaRuntimeKey", context)
        self.assertIn("$rootByRuntime.ContainsKey", context)
        self.assertIn("$openedMenu.pre_existing_root_keys.ContainsKey", context)
        self.assertIn("$rootDeadline = [DateTime]::UtcNow.AddSeconds(5)", context)
        self.assertIn("Start-Sleep -Milliseconds 100", context)
        self.assertIn("exactly one newly visible Skill Magnet root", context)

    def test_field_collector_scopes_every_product_window_to_native_process(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        named_elements = collector[
            collector.index("function Get-VisibleNamedElements") :
            collector.index("function Get-UiaRuntimeKey")
        ]
        unified_gui = collector[
            collector.index("function Inspect-UnifiedGui") :
            collector.index("function Close-UiaWindow")
        ]
        manager = collector[
            collector.index("function Inspect-LibraryManager") :
            collector.index("function Wait-MissingSkillRecoveryDialog")
        ]
        busy = collector[
            collector.index("function Assert-BusyMessageAndClose") :
            collector.index("$configPath =")
        ]
        self.assertIn("[int]$ProcessId = 0", named_elements)
        self.assertIn("[int]$_.Current.ProcessId -eq $ProcessId", named_elements)
        self.assertIn("[int]$ExpectedProcessId", unified_gui)
        self.assertIn(
            'Wait-VisibleWindowByPrefix "Skill Magnet — 実行確認" $ExpectedProcessId',
            unified_gui,
        )
        self.assertIn(
            'Wait-VisibleWindowByPrefix "Library Manager" $ExpectedProcessId',
            manager,
        )
        self.assertIn(
            'Wait-VisibleWindowByPrefix "Skill Magnet エラー" $ExpectedProcessId',
            busy,
        )
        self.assertRegex(
            collector,
            r"Inspect-UnifiedGui `\s*\n\s*\$selectedFolder "
            r"\$expectedChoices \$selectedSequence\.process_id",
        )
        self.assertRegex(
            collector,
            r"Assert-BusyMessageAndClose \$differentSequence\.process_id",
        )

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_field_widget_gate_waits_for_later_viewable_revision(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        assert_source = collector[
            collector.index("function Assert-Field") :
            collector.index("function Get-BytesSha256")
        ]
        gate_source = collector[
            collector.index("function Test-RequiredFieldWidgetRevision") :
            collector.index("function Wait-FieldUiSurface")
        ]
        encoded = base64.b64encode(
            (assert_source + "\n" + gate_source).encode("utf-8")
        ).decode("ascii")
        probe = rf'''
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded}"))
. ([ScriptBlock]::Create($source))
function Surface([int64]$Revision, [bool]$Viewable, [string]$Role = "combobox") {{
    [pscustomobject]@{{
        revision = $Revision
        widgets = @([pscustomobject]@{{
            id = "selection_choice"; role = $Role; viewable = $Viewable
        }})
    }}
}}
$rejected = @{{}}
$first = Test-RequiredFieldWidgetRevision (Surface 7 $false) `
    "selection_choice" "combobox" 0 "generation-a" $rejected
$rollback = Test-RequiredFieldWidgetRevision (Surface 5 $false) `
    "selection_choice" "combobox" 0 "generation-a" $rejected
$forgedEarlier = Test-RequiredFieldWidgetRevision (Surface 6 $true) `
    "selection_choice" "combobox" 0 "generation-a" $rejected
$later = Test-RequiredFieldWidgetRevision (Surface 8 $true) `
    "selection_choice" "combobox" 0 "generation-a" $rejected
$wrongRoleRejected = $false
try {{
    Test-RequiredFieldWidgetRevision (Surface 9 $true "label") `
        "selection_choice" "combobox" 0 "generation-a" $rejected | Out-Null
}} catch {{ $wrongRoleRejected = $true }}
$ownerRevisions = @{{}}
$generation = "a" * 32
Update-FieldOwnerRevision $generation 7 $ownerRevisions
$ownerRollbackRejected = $false
try {{ Update-FieldOwnerRevision $generation 6 $ownerRevisions }}
catch {{ $ownerRollbackRejected = $true }}
Update-FieldOwnerRevision $generation 8 $ownerRevisions
[pscustomobject]@{{
    first_false = -not $first
    rollback_false = -not $rollback
    max_preserved = [int64]$rejected["generation-a"] -eq 7
    forged_earlier_false = -not $forgedEarlier
    later_true = $later
    wrong_role_rejected = $wrongRoleRejected
    owner_rollback_rejected = $ownerRollbackRejected
    owner_later_accepted = [int64]$ownerRevisions[$generation] -eq 8
}} | ConvertTo-Json -Compress
'''
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    def test_field_collector_rejects_untrusted_ui_receipts_before_mouse_input(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        strict_read = collector[
            collector.index("function Read-ValidatedFieldContextOwner") :
            collector.index("function Get-FieldUiSurfaceWidget")
        ]
        surface = collector[
            collector.index("function Test-RequiredFieldWidgetRevision") :
            collector.index("function Invoke-FieldUiSurfaceWidget")
        ]
        click = collector[
            collector.index("function Invoke-FieldUiSurfaceWidget") :
            collector.index("function Register-FieldOwnedProcess")
        ]
        self.assertIn("FileAttributes]::ReparsePoint", strict_read)
        self.assertIn("262144", strict_read)
        self.assertIn("[SkillMagnetStableBytes]::Read", strict_read)
        self.assertIn("[byte[]]$stable.Bytes", strict_read)
        self.assertIn("return $null", strict_read)
        self.assertIn('"changed_during_read", "path_identity_changed"', strict_read)
        self.assertIn('"sharing_violation", "not_found"', strict_read)
        self.assertIn("cannot be safely read", strict_read)
        self.assertIn("FileShare.ReadWrite | FileShare.Delete", collector)
        self.assertIn("GetFileInformationByHandle", collector)
        self.assertIn("path_identity_changed", collector)
        self.assertIn("duplicate JSON key", strict_read)
        self.assertIn("object_pairs_hook=unique_object", strict_read)
        self.assertIn("Get-BytesSha256 $beforeBytes", strict_read)
        self.assertIn('"__FIELD_OWNER_BASE64__"', strict_read)
        self.assertIn('$validator.Replace("__FIELD_OWNER_BASE64__", $encoded)', strict_read)
        self.assertIn("$script:FieldExpectedExecutablePath -I -", strict_read)
        self.assertNotIn("-I -c", strict_read)
        self.assertNotIn("sys.argv[1]", strict_read)
        for required in (
            "$owner.schema_version",
            "$owner.process_instance_id",
            "$owner.process_started_at_unix_ns",
            "$owner.target_sha256",
            "$surface.schema_version",
            "$surface.revision",
            "$surface.published_at_utc",
            "$surface.generation",
            "$surface.pid",
            "$surface.phase",
            "$script:FieldPreExistingOwnerGeneration",
            "GetWindowThreadProcessId",
            "GetAncestor",
            "GetWindowRect",
            "GetSystemMetrics(76)",
            "Test-FieldRectangleWithin",
            "$expectedTitle = switch ($ExpectedPhase)",
            "$RequiredWidgetId",
            "$requiredWidgets[0].viewable",
            "$RejectedWidgetRevision[$Generation]",
            "canonical UTC Z format",
            'ClassName -ceq "TkChild"',
            "IsOffscreen",
        ):
            self.assertIn(required, surface)
        for forbidden_request_field in (
            '"text"',
            '"value"',
            '"values"',
            '"text_sha256"',
            '"value_sha256"',
            '"values_sha256"',
        ):
            self.assertIn(forbidden_request_field, surface)
        self.assertIn(
            "$requestFields -notcontains $forbiddenRequestField", surface
        )
        self.assertIn("Assert-NoRawReceiptDisplayValues $owner", surface)
        for required in (
            "$allowedIds",
            "Test-FieldProcessIdentity",
            "FocusWindow",
            "GetForegroundWindow",
            "$firstHit -eq $widgetHandle",
            "SetCursorPos",
            "$fresh.surface.revision",
            "$fresh.owner_sha256",
            "$ExpectedTargetSha256",
            "$secondHit -eq $widgetHandle",
            "AutomationElement]::FromPoint",
            "GetWindowThreadProcessId",
            "GetAncestor",
            "CheckedClickCurrent",
            "[int64]$nextReceipt.surface.revision -gt [int64]$fresh.surface.revision",
        ):
            self.assertIn(required, click)
        self.assertGreaterEqual(click.count("Test-FieldProcessIdentity $identity"), 3)
        self.assertGreaterEqual(click.count("Wait-FieldUiSurface"), 3)
        self.assertIn("$finalReceipt.surface.revision", click)
        self.assertIn("$finalWidget.text_sha256", click)
        self.assertIn("$clickHit = [SkillMagnetFieldInput]::WindowFromPoint($point)", click)
        self.assertIn("$clickUia = [System.Windows.Automation.AutomationElement]::FromPoint", click)
        self.assertNotIn("LeftClick", click)
        self.assertLess(
            click.index("$secondHit -eq $widgetHandle"),
            click.index("CheckedClickCurrent"),
        )
        self.assertLess(
            click.index("AutomationElement]::FromPoint"),
            click.index("CheckedClickCurrent"),
        )
        self.assertLess(click.index("$finalReceipt"), click.index("$clickHit"))
        self.assertLess(click.index("$clickUia"), click.index("CheckedClickCurrent"))

    def test_field_collector_uses_foreground_thread_attachment(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        native_input = collector[
            collector.index("public static class SkillMagnetFieldInput") :
            collector.index("function Get-VisibleNamedElements")
        ]
        for required in (
            "AttachThreadInput",
            "GetCurrentThreadId",
            "ShowWindow(hWnd, 9)",
            "BringWindowToTop(hWnd)",
            "GetForegroundWindow() == hWnd",
        ):
            self.assertIn(required, native_input)
        self.assertGreaterEqual(collector.count("FocusWindow($handle)"), 1)
        self.assertGreaterEqual(collector.count("FocusWindow($windowHandle)"), 1)

    def test_every_physical_click_uses_atomic_native_identity_gate(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        native_input = collector[
            collector.index("public static class SkillMagnetFieldInput") :
            collector.index("function Get-VisibleNamedElements")
        ]
        checked = native_input[
            native_input.index("public static bool CheckedClickCurrent") :
        ]
        for required in (
            "GetForegroundWindow()",
            "WindowFromPoint(point)",
            "GetAncestor(finalHit, 2)",
            "GetWindowThreadProcessId(finalHit",
            "ProcessMatches(expectedProcessId",
            "AutomationElement.FromPoint",
            "finalUia.Current.IsEnabled",
            "finalUia.Current.Name",
            "ReadPinnedReceipt(receiptPath",
            "expectedProcessInstanceId",
            "expectedGeneration",
            "expectedRevision",
            "expectedSemanticId",
            "expectedSemanticNameSha256",
            "requireImmutableChild",
            "expectedChildRuntimeKey",
            "expectedChildControlType",
            "expectedChildClassName",
            "expectedChildWidth",
            "expectedChildEnabled",
            "expectedChildOffscreen",
            "TestAfterInitialValidation",
        ):
            self.assertIn(required, checked)
        self.assertEqual(collector.count("mouse_event("), 3)
        self.assertEqual(collector.count("CheckedClickCurrent("), 3)
        final_check = checked.index("// This is the final fail-closed boundary")
        first_send = checked.index("mouse_event(down", final_check)
        between = checked[final_check:first_send]
        for forbidden in ("Start-Sleep", "TestAfterInitialValidation", "FocusWindow"):
            self.assertNotIn(forbidden, between)
        self.assertIn("ReceiptMatches(", between)
        self.assertIn("ProcessMatches(", between)
        self.assertIn("finalUia.Current.Name", between)

    def test_ui_receipt_consumers_reject_unknown_nested_keys_and_wrong_types(self) -> None:
        digest = "a" * 64
        generation = "b" * 32
        published = "2026-09-05T00:00:00Z"
        receipt: dict[str, object] = {
            "schema_version": 2,
            "owner_kind": "context_launcher",
            "pid": 123,
            "process_instance_id": "c" * 32,
            "process_started_at_unix_ns": 1,
            "target_sha256": digest,
            "generation": generation,
            "phase": "context_selection",
            "window_handle": 100,
            "revision": 2,
            "published_at_utc": published,
            "ui_surface": {
                "schema_version": 1,
                "generation": generation,
                "pid": 123,
                "phase": "context_selection",
                "window": {
                    "hwnd": 100,
                    "title_sha256": digest,
                    "client": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "screen": {"x": 1, "y": 2, "width": 3, "height": 4},
                },
                "state": {
                    "language_sha256": digest,
                    "selection_mode_sha256": digest,
                    "processing": False,
                    "details_visible": False,
                },
                "widgets": [
                    {
                        "id": "request",
                        "role": "entry",
                        "state": {"configured": "normal", "enabled": True},
                        "viewable": True,
                        "hwnd": 101,
                        "client": {"x": 1, "y": 2, "width": 3, "height": 4},
                        "screen": {"x": 1, "y": 2, "width": 3, "height": 4},
                    }
                ],
                "revision": 2,
                "published_at_utc": published,
            },
        }

        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        marker = "$validator = @'\n"
        start = collector.index(marker) + len(marker)
        validator = collector[start : collector.index("\n'@", start)]

        def clone() -> dict[str, object]:
            return json.loads(json.dumps(receipt))

        def gate_read(payload: bytes) -> None:
            value = results_gate._strict_json_loads(payload, label="test UI receipt")
            _validate_ui_owner_receipt_schema(value)

        def field_read(payload: bytes) -> None:
            encoded = base64.b64encode(payload).decode("ascii")
            program = validator.replace("__FIELD_OWNER_BASE64__", encoded)
            with mock.patch("sys.stdout", io.StringIO()):
                exec(compile(program, "field-ui-owner-validator", "exec"), {})

        valid_payload = json.dumps(receipt, separators=(",", ":")).encode()
        gate_read(valid_payload)
        field_read(valid_payload)
        manager = clone()
        manager["phase"] = "library_manager"
        manager["ui_surface"]["phase"] = "library_manager"  # type: ignore[index]
        manager["ui_surface"]["state"] = {  # type: ignore[index]
            "processing": False,
            "register_selected": True,
        }
        for state in (
            manager["ui_surface"]["state"],  # type: ignore[index]
            {
                "processing": True,
                "register_selected": False,
                "stage_sha256": digest,
            },
        ):
            manager["ui_surface"]["state"] = state  # type: ignore[index]
            manager_payload = json.dumps(manager, separators=(",", ":")).encode()
            gate_read(manager_payload)
            field_read(manager_payload)
        for starting_phase in ("context_starting", "library_manager_starting"):
            starting = {
                key: value for key, value in clone().items() if key != "ui_surface"
            }
            starting["phase"] = starting_phase
            starting["window_handle"] = 0
            starting_payload = json.dumps(starting, separators=(",", ":")).encode()
            gate_read(starting_payload)
            field_read(starting_payload)

        invalid: list[dict[str, object]] = []
        owner_extra = clone()
        owner_extra["private_token"] = "secret"
        invalid.append(owner_extra)
        surface_extra = clone()
        surface_extra["ui_surface"]["request_digest"] = digest  # type: ignore[index]
        invalid.append(surface_extra)
        window_extra = clone()
        window_extra["ui_surface"]["window"]["private_token"] = "secret"  # type: ignore[index]
        invalid.append(window_extra)
        state_extra = clone()
        state_extra["ui_surface"]["state"]["request_digest"] = digest  # type: ignore[index]
        invalid.append(state_extra)
        widget_extra = clone()
        widget_extra["ui_surface"]["widgets"][0]["private_token"] = "secret"  # type: ignore[index]
        invalid.append(widget_extra)
        widget_state_extra = clone()
        widget_state_extra["ui_surface"]["widgets"][0]["state"]["request_digest"] = digest  # type: ignore[index]
        invalid.append(widget_state_extra)
        rect_extra = clone()
        rect_extra["ui_surface"]["window"]["client"]["private_token"] = 1  # type: ignore[index]
        invalid.append(rect_extra)
        wrong_type = clone()
        wrong_type["ui_surface"]["widgets"][0]["viewable"] = 1  # type: ignore[index]
        invalid.append(wrong_type)
        request_digest = clone()
        request_digest["ui_surface"]["widgets"][0]["text_sha256"] = digest  # type: ignore[index]
        invalid.append(request_digest)
        missing_required = clone()
        del missing_required["ui_surface"]["window"]["screen"]  # type: ignore[index]
        invalid.append(missing_required)
        missing_selection_surface = clone()
        del missing_selection_surface["ui_surface"]
        invalid.append(missing_selection_surface)
        missing_manager_surface = {
            key: value for key, value in manager.items() if key != "ui_surface"
        }
        invalid.append(missing_manager_surface)
        unknown_phase = {
            key: value for key, value in clone().items() if key != "ui_surface"
        }
        unknown_phase["phase"] = "recovery_starting"
        invalid.append(unknown_phase)
        for starting_phase in ("context_starting", "library_manager_starting"):
            starting_with_surface = clone()
            starting_with_surface["phase"] = starting_phase
            invalid.append(starting_with_surface)

        for candidate in invalid:
            payload = json.dumps(candidate, separators=(",", ":")).encode()
            with self.subTest(keys=list(candidate)):
                with self.assertRaises(ValueError):
                    gate_read(payload)
                with self.assertRaises(ValueError):
                    field_read(payload)

        duplicate = valid_payload.replace(
            b'"schema_version":2', b'"schema_version":2,"schema_version":2', 1
        )
        with self.assertRaises(ValueError):
            gate_read(duplicate)
        with self.assertRaises(ValueError):
            field_read(duplicate)

    @unittest.skipUnless(os.name == "nt", "requires real Windows HWND behavior")
    def test_native_click_guard_sends_no_mouse_after_post_validation_swaps(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        encoded_csharp = base64.b64encode(csharp.encode("utf-8")).decode("ascii")
        probe = rf'''
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_csharp}"))
Add-Type -ReferencedAssemblies @(
    "UIAutomationClient", "UIAutomationTypes", "WindowsBase"
) -TypeDefinition $source
function Get-Sha256([byte[]]$Bytes) {{
    [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    ).Replace("-", "").ToLowerInvariant()
}}
$root = [Windows.Forms.Form]::new()
$root.Text = "expected-root"
$root.StartPosition = "Manual"
$root.SetDesktopBounds(80, 80, 320, 180)
$button = [Windows.Forms.Button]::new()
$button.Text = "guarded-action"
$button.SetBounds(30, 40, 160, 50)
$root.Controls.Add($button)
$competitor = [Windows.Forms.Form]::new()
$competitor.Text = "competing-root"
$competitor.StartPosition = "Manual"
$competitor.SetDesktopBounds(500, 80, 260, 160)
$script:clickCount = 0
$button.Add_Click({{ $script:clickCount += 1 }})
try {{
    $root.Show()
    $competitor.Show()
    [Windows.Forms.Application]::DoEvents()
    $point = $button.PointToScreen([Drawing.Point]::new(20, 20))
    $process = [Diagnostics.Process]::GetCurrentProcess()
    $exe = [IO.Path]::GetFullPath($process.MainModule.FileName)
    $ticks = $process.StartTime.ToUniversalTime().Ticks
    $nameSha = Get-Sha256 ([Text.UTF8Encoding]::new($false).GetBytes($button.Text))
    [SkillMagnetFieldInput]::SetCursorPos($point.X, $point.Y) | Out-Null
    [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
    [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
        [SkillMagnetFieldInput]::FocusWindow($competitor.Handle) | Out-Null
    }}
    $hwndResult = [SkillMagnetFieldInput]::CheckedClickCurrent(
        $point.X, $point.Y, $button.Handle, $root.Handle, [uint32]$process.Id,
        $exe, [long]$ticks, $true, $nameSha,
        "", "", "", "", [long]0, "", "", $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $true, $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $false
    )
    [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
    [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
    $receiptPath = Join-Path ([IO.Path]::GetTempPath()) (
        "skill-magnet-click-guard-" + [guid]::NewGuid().ToString("N") + ".json"
    )
    $processInstance = "a" * 32
    $generation = "b" * 32
    $receipt = @{{
        process_instance_id = $processInstance
        generation = $generation
        revision = 1
        ui_surface = @{{ widgets = @(@{{ id = "guarded"; text_sha256 = $nameSha }}) }}
    }} | ConvertTo-Json -Depth 5 -Compress
    $receiptBytes = [Text.UTF8Encoding]::new($false).GetBytes($receipt)
    [IO.File]::WriteAllBytes($receiptPath, $receiptBytes)
    $receiptSha = Get-Sha256 $receiptBytes
    [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
        $changed = $receipt.Replace('"revision":1', '"revision":2')
        [IO.File]::WriteAllText($receiptPath, $changed, [Text.UTF8Encoding]::new($false))
    }}
    $receiptResult = [SkillMagnetFieldInput]::CheckedClickCurrent(
        $point.X, $point.Y, $button.Handle, $root.Handle, [uint32]$process.Id,
        $exe, [long]$ticks, $true, $nameSha, $receiptPath, $receiptSha,
        $processInstance, $generation, [long]1, "guarded", $nameSha,
        $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $true, $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $false
    )
    [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
    [IO.File]::WriteAllBytes($receiptPath, $receiptBytes)
    [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
    [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
        $button.Text = "substituted-action"
        [Windows.Forms.Application]::DoEvents()
    }}
    $uiaResult = [SkillMagnetFieldInput]::CheckedClickCurrent(
        $point.X, $point.Y, $button.Handle, $root.Handle, [uint32]$process.Id,
        $exe, [long]$ticks, $true, $nameSha, $receiptPath, $receiptSha,
        $processInstance, $generation, [long]1, "guarded", $nameSha,
        $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $true, $false, "", 0, "",
        [double]0, [double]0, [double]0, [double]0, $false
    )
    [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
    [SkillMagnetFieldInput]::FocusWindow($competitor.Handle) | Out-Null
    [Windows.Forms.Application]::DoEvents()
    [pscustomobject]@{{
        hwnd_result = $hwndResult
        receipt_result = $receiptResult
        uia_result = $uiaResult
        click_count = $script:clickCount
    }} |
        ConvertTo-Json -Compress
}}
finally {{
    [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
    if ($receiptPath) {{ Remove-Item -LiteralPath $receiptPath -Force -ErrorAction SilentlyContinue }}
    $competitor.Close()
    $root.Close()
}}
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "click-guard-probe.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-STA",
                    "-File",
                    str(probe_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        observation = json.loads(completed.stdout.strip())
        self.assertFalse(observation["hwnd_result"])
        self.assertFalse(observation["receipt_result"])
        self.assertFalse(observation["uia_result"])
        self.assertEqual(observation["click_count"], 0)

    @unittest.skipUnless(os.name == "nt", "requires real Windows UIAutomation")
    def test_native_click_guard_rejects_row_and_child_identity_faults(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        encoded_csharp = base64.b64encode(csharp.encode("utf-8")).decode("ascii")
        probe = rf'''
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_csharp}"))
Add-Type -ReferencedAssemblies @(
    "UIAutomationClient", "UIAutomationTypes", "WindowsBase"
) -TypeDefinition $source
function Sha([string]$Text) {{
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
    [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
}}
function Run-Fault([string]$Kind, [int]$Offset) {{
    $root = [Windows.Forms.Form]::new()
    $root.Text = "root-$Kind"
    $root.StartPosition = "Manual"
    $root.SetDesktopBounds(80, 80 + $Offset, 360, 180)
    $row = [Windows.Forms.Panel]::new()
    $row.Text = "expected-row"
    $row.SetBounds(20, 30, 260, 80)
    $button = [Windows.Forms.Button]::new()
    $button.Text = "guarded-child"
    $button.SetBounds(30, 20, 170, 40)
    $row.Controls.Add($button)
    $root.Controls.Add($row)
    $competitor = [Windows.Forms.Form]::new()
    $competitor.Text = "competitor-$Kind"
    $competitor.StartPosition = "Manual"
    $competitor.SetDesktopBounds(500, 80 + $Offset, 300, 180)
    $script:clickCount += 0
    $button.Add_Click({{ $script:clickCount += 1 }})
    try {{
        $root.Show(); $competitor.Show(); [Windows.Forms.Application]::DoEvents()
        $point = $button.PointToScreen([Drawing.Point]::new(60, 20))
        $process = [Diagnostics.Process]::GetCurrentProcess()
        $childUia = [Windows.Automation.AutomationElement]::FromHandle($button.Handle)
        $rowUia = [Windows.Automation.AutomationElement]::FromHandle($row.Handle)
        $rowRect = $rowUia.Current.BoundingRectangle
        $rowRuntime = [string]::Join(".", $rowUia.GetRuntimeId())
        [SkillMagnetFieldInput]::SetCursorPos($point.X, $point.Y) | Out-Null
        [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
        [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
            switch ($Kind) {{
                "reparent" {{ $competitor.Controls.Add($button) }}
                "same_name_row_swap" {{
                    $root.Controls.Remove($row)
                    $newRow = [Windows.Forms.Panel]::new()
                    $newRow.Text = "expected-row"
                    $newRow.SetBounds(20, 30, 260, 80)
                    $newButton = [Windows.Forms.Button]::new()
                    $newButton.Text = "guarded-child"
                    $newButton.SetBounds(30, 20, 170, 40)
                    $newRow.Controls.Add($newButton); $root.Controls.Add($newRow)
                }}
                "empty_name" {{ $button.Text = "" }}
                "bounds_change" {{ $row.SetBounds(21, 30, 260, 80) }}
                "runtime_swap" {{
                    $row.Controls.Remove($button)
                    $newButton = [Windows.Forms.Button]::new()
                    $newButton.Text = "guarded-child"
                    $newButton.SetBounds(30, 20, 170, 40)
                    $row.Controls.Add($newButton)
                }}
            }}
            [Windows.Forms.Application]::DoEvents()
        }}
        $result = [SkillMagnetFieldInput]::CheckedClickCurrent(
            $point.X, $point.Y, $button.Handle, $root.Handle, [uint32]$process.Id,
            [IO.Path]::GetFullPath($process.MainModule.FileName),
            [long]$process.StartTime.ToUniversalTime().Ticks,
            $true, (Sha "guarded-child"), "", "", "", "", [long]0, "", "",
            $false, "", 0, "", [double]0, [double]0,
            [double]0, [double]0, $true, $false,
            $rowRuntime, [int]$rowUia.Current.ControlType.Id, (Sha $rowUia.Current.Name),
            [double]$rowRect.X, [double]$rowRect.Y,
            [double]$rowRect.Width, [double]$rowRect.Height, $false
        )
        return -not $result
    }}
    finally {{
        [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
        $competitor.Close(); $root.Close()
    }}
}}
$script:clickCount = 0
$observations = [ordered]@{{}}
$index = 0
foreach ($kind in @("reparent", "same_name_row_swap", "empty_name", "bounds_change", "runtime_swap")) {{
    $observations[$kind] = Run-Fault $kind ($index * 4)
    $index += 1
}}
$observations["mouse_zero"] = $script:clickCount -eq 0
$observations | ConvertTo-Json -Compress
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "row-child-guard-probe.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-File", str(probe_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip(), repr((completed.stdout, completed.stderr)))
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    @unittest.skipUnless(os.name == "nt", "requires real Windows Tk/UIAutomation")
    def test_native_click_guard_splits_tk_uia_name_from_receipt_semantics(self) -> None:
        collector = (ROOT / "tests" / "powershell" /
                     "windows-explorer-direct-root-field-test.ps1").read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        encoded_csharp = base64.b64encode(csharp.encode()).decode()
        server_source = r'''
import json, os, pathlib, sys, tkinter as tk
from tkinter import ttk
state, command, ack, clicked = map(pathlib.Path, sys.argv[1:5])
root=tk.Tk(); root.title("Skill Magnet Tk probe"); root.geometry("500x410+700+120"); root.attributes("-topmost", True)
button=ttk.Button(root,text="Library Manager",command=lambda: clicked.write_text("1")); button.place(x=40,y=50,width=180,height=42)
combo=ttk.Combobox(root,values=("one","two")); combo.place(x=40,y=110,width=180,height=30)
entry=ttk.Entry(root); entry.place(x=40,y=155,width=180,height=30)
label=ttk.Label(root,text="status"); label.place(x=40,y=200,width=180,height=30)
tree=ttk.Treeview(root); tree.place(x=40,y=245,width=180,height=70)
text=tk.Text(root); text.place(x=260,y=245,width=180,height=70)
root.update_idletasks(); root.update()
widgets=[]
for widget in (button,combo,entry,label,tree,text):
 widgets.append({"hwnd":widget.winfo_id(),"left":widget.winfo_rootx(),"top":widget.winfo_rooty(),"width":widget.winfo_width(),"height":widget.winfo_height(),"x":widget.winfo_rootx()+widget.winfo_width()//2,"y":widget.winfo_rooty()+widget.winfo_height()//2})
state.write_text(json.dumps({"pid":os.getpid(),"root":root.winfo_id(),"button":button.winfo_id(),"x":button.winfo_rootx()+90,"y":button.winfo_rooty()+21,"widgets":widgets}))
def poll():
 global button
 if command.exists():
  mode=command.read_text()
  if mode=="move": button.place_configure(x=45)
  elif mode=="disable": button.state(["disabled"])
  elif mode=="name": button.configure(text="Changed semantic name")
  elif mode=="swap":
   button.destroy(); ttk.Label(root,text="spacer").place(x=0,y=0)
   button=ttk.Button(root,text="Library Manager"); button.place(x=40,y=50,width=180,height=42)
  root.update_idletasks(); ack.write_text("ok"); command.unlink(missing_ok=True)
 root.after(10,poll)
root.after(10,poll); root.mainloop()
'''
        probe_source = rf'''
param($StatePath,$ReceiptPath,$CommandPath,$AckPath,$Mode)
$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_csharp}"))
Add-Type -ReferencedAssemblies @("UIAutomationClient","UIAutomationTypes","WindowsBase") -TypeDefinition $source
function Sha([string]$Text){{$b=[Text.UTF8Encoding]::new($false).GetBytes($Text);[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b)).Replace("-","").ToLowerInvariant()}}
$s=Get-Content $StatePath -Raw|ConvertFrom-Json; $p=Get-Process -Id ([int]$s.pid
); $button=[IntPtr]([int64]$s.button); $root=[SkillMagnetFieldInput]::GetAncestor($button,2)
[SkillMagnetFieldInput]::FocusWindow($root)|Out-Null; [SkillMagnetFieldInput]::SetCursorPos([int]$s.x,[int]$s.y)|Out-Null; Start-Sleep -Milliseconds 100
$uia=[Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new([double]$s.x,[double]$s.y)); $r=$uia.Current.BoundingRectangle
$rolesOk=$true; foreach($w in $s.widgets){{$e=[Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new([double]$w.x,[double]$w.y));$q=$e.Current.BoundingRectangle;$rolesOk=$rolesOk-and([int64]$e.Current.NativeWindowHandle-eq[int64]$w.hwnd)-and([int]$e.Current.ProcessId-eq[int]$s.pid)-and([int]$e.Current.ControlType.Id-eq[Windows.Automation.ControlType]::Pane.Id)-and([string]$e.Current.ClassName-ceq"TkChild")-and([string]$e.Current.Name-ceq"")-and([string]::Join(".",$e.GetRuntimeId()).Length-gt 0)-and([double]$q.X-eq[double]$w.left)-and([double]$q.Y-eq[double]$w.top)-and([double]$q.Width-eq[double]$w.width)-and([double]$q.Height-eq[double]$w.height)-and([bool]$e.Current.IsEnabled)-and(-not[bool]$e.Current.IsOffscreen)}}
$runtime=[string]::Join(".",$uia.GetRuntimeId()); $actual=Sha ([string]$uia.Current.Name); $semantic=Sha "Library Manager"; $pi="a"*32; $gen="b"*32
$receipt=@{{process_instance_id=$pi;generation=$gen;revision=1;ui_surface=@{{widgets=@(@{{id="library_manager";text_sha256=$semantic;viewable=$true;state=@{{enabled=$true}}}})}}}}|ConvertTo-Json -Depth 5 -Compress
$bytes=[Text.UTF8Encoding]::new($false).GetBytes($receipt); [IO.File]::WriteAllBytes($ReceiptPath,$bytes)
$receiptSha=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace("-","").ToLowerInvariant()
if($Mode-ne"success"){{[SkillMagnetFieldInput]::TestAfterInitialValidation=[Action]{{if($Mode-eq"name"){{[IO.File]::WriteAllText($ReceiptPath,$receipt.Replace($semantic,(Sha "Changed semantic name")),[Text.UTF8Encoding]::new($false))}}elseif($Mode-eq"disable"){{[IO.File]::WriteAllText($ReceiptPath,$receipt.Replace('"enabled":true','"enabled":false'),[Text.UTF8Encoding]::new($false))}};[IO.File]::WriteAllText($CommandPath,$Mode);$d=[DateTime]::UtcNow.AddSeconds(3);while(!(Test-Path $AckPath)-and[DateTime]::UtcNow-lt$d){{Start-Sleep -Milliseconds 10}}}}}}
try{{$result=[SkillMagnetFieldInput]::CheckedClickCurrent([int]$s.x,[int]$s.y,$button,$root,[uint32]$s.pid,[IO.Path]::GetFullPath($p.MainModule.FileName),[long]$p.StartTime.ToUniversalTime().Ticks,$true,$actual,$ReceiptPath,$receiptSha,$pi,$gen,[long]1,"library_manager",$semantic,$true,$runtime,[int]$uia.Current.ControlType.Id,[string]$uia.Current.ClassName,[double]$r.X,[double]$r.Y,[double]$r.Width,[double]$r.Height,[bool]$uia.Current.IsEnabled,[bool]$uia.Current.IsOffscreen,"",0,"",0,0,0,0,$false);[pscustomobject]@{{result=$result;roles_ok=$rolesOk;actual_name=[string]$uia.Current.Name;actual_sha=$actual;semantic_sha=$semantic;class_name=[string]$uia.Current.ClassName}}|ConvertTo-Json -Compress}}finally{{[SkillMagnetFieldInput]::TestAfterInitialValidation=$null}}
'''
        observations = {}
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary); server_file=temp/"server.py"; probe_file=temp/"probe.ps1"
            server_file.write_text(server_source, encoding="utf-8"); probe_file.write_text(probe_source, encoding="utf-8-sig")
            for index, mode in enumerate(("success", "move", "disable", "name", "swap")):
                case=temp/f"case-{index}"; case.mkdir(); state=case/"state"; command=case/"command"; ack=case/"ack"; clicked=case/"clicked"
                server=subprocess.Popen([sys.executable,str(server_file),str(state),str(command),str(ack),str(clicked)],cwd=ROOT)
                try:
                    for _ in range(100):
                        if state.exists(): break
                        time.sleep(.05)
                    self.assertTrue(state.exists())
                    done=subprocess.run(["powershell.exe","-NoProfile","-STA","-File",str(probe_file),str(state),str(case/"receipt"),str(command),str(ack),mode],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=20)
                    self.assertEqual(done.returncode,0,done.stderr); observations[mode]=json.loads(done.stdout.strip()); time.sleep(.1)
                    self.assertEqual(clicked.exists(),mode=="success",observations[mode])
                finally:
                    server.terminate(); server.wait(timeout=5)
        empty_sha=hashlib.sha256(b"").hexdigest(); self.assertTrue(observations["success"]["result"])
        self.assertEqual(observations["success"]["actual_name"],""); self.assertEqual(observations["success"]["actual_sha"],empty_sha)
        self.assertNotEqual(empty_sha,observations["success"]["semantic_sha"]); self.assertEqual(observations["success"]["class_name"],"TkChild")
        self.assertTrue(observations["success"]["roles_ok"], observations)
        for mode in ("move","disable","name","swap"): self.assertFalse(observations[mode]["result"],observations)

    @unittest.skipUnless(os.name == "nt", "requires real Windows UIAutomation")
    def test_selected_row_lineage_is_kept_between_left_and_right_clicks(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        encoded_csharp = base64.b64encode(csharp.encode("utf-8")).decode("ascii")
        def function_source(name: str) -> str:
            start = collector.index(f"function {name}")
            end = collector.find("\nfunction ", start + 1)
            return collector[start:] if end < 0 else collector[start:end]

        powershell_functions = "\n".join(
            function_source(name)
            for name in (
                "Assert-Field", "Get-BytesSha256", "Get-Utf8Sha256",
                "Get-UiaRuntimeKey", "New-ExplorerRowSnapshot",
                "Test-UiaSelfOrDescendantOfSnapshot", "Get-FieldProcessIdentity",
                "Test-FieldProcessIdentity", "Invoke-CheckedExplorerPhysicalClick",
            )
        )
        encoded_functions = base64.b64encode(
            powershell_functions.encode("utf-8")
        ).decode("ascii")
        probe = rf'''
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_csharp}"))
Add-Type -ReferencedAssemblies @(
    "UIAutomationClient", "UIAutomationTypes", "WindowsBase"
) -TypeDefinition $source
$functionSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_functions}"))
. ([ScriptBlock]::Create($functionSource))
function Sha([string]$Text) {{
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
    [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
}}
function Run-Sequence([string]$Kind, [int]$Offset) {{
    $root = [Windows.Forms.Form]::new(); $root.Text = "root-$Kind"
    $root.StartPosition = "Manual"; $root.SetDesktopBounds(80, 80 + $Offset, 360, 180)
    $row = [Windows.Forms.Panel]::new(); $row.Text = "selected-row"
    $row.SetBounds(20, 30, 260, 80)
    $button = [Windows.Forms.Button]::new(); $button.Text = "selected-child"
    $button.SetBounds(30, 20, 170, 40); $row.Controls.Add($button); $root.Controls.Add($row)
    $competitor = [Windows.Forms.Form]::new(); $competitor.Text = "other-$Kind"
    $competitor.StartPosition = "Manual"; $competitor.SetDesktopBounds(500, 80 + $Offset, 300, 180)
    $button.Add_MouseDown({{ param($sender, $event); if($event.Button -eq [Windows.Forms.MouseButtons]::Right){{$script:rightCount++}} }})
    try {{
        $root.Show(); $competitor.Show(); [Windows.Forms.Application]::DoEvents()
        $point = $button.PointToScreen([Drawing.Point]::new(60, 20))
        $process = [Diagnostics.Process]::GetCurrentProcess()
        $rowUia = [Windows.Automation.AutomationElement]::FromHandle($row.Handle)
        $rowRect = $rowUia.Current.BoundingRectangle
        $window = [pscustomobject]@{{ HWND = [int64]$root.Handle }}
        [SkillMagnetFieldInput]::SetCursorPos($point.X, $point.Y) | Out-Null
        [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
        [Windows.Forms.Application]::DoEvents()
        $rowSnapshot = New-ExplorerRowSnapshot $rowUia $point.X $point.Y
        Invoke-CheckedExplorerPhysicalClick `
            $window $point.X $point.Y $false $rowSnapshot
        $left = $true
        [Windows.Forms.Application]::DoEvents()
        if ($Kind -eq "reparent") {{ $competitor.Controls.Add($button) }}
        elseif ($Kind -eq "move") {{ $row.SetBounds(21, 30, 260, 80) }}
        elseif ($Kind -notlike "boundary_*") {{
            $root.Controls.Remove($row)
            $replacement = [Windows.Forms.Panel]::new(); $replacement.Text = "selected-row"
            $replacement.SetBounds(20, 30, 260, 80)
            $replacementButton = [Windows.Forms.Button]::new(); $replacementButton.Text = "selected-child"
            $replacementButton.SetBounds(30, 20, 170, 40)
            $replacementButton.Add_MouseDown({{ param($sender, $event); if($event.Button -eq [Windows.Forms.MouseButtons]::Right){{$script:rightCount++}} }})
            $replacement.Controls.Add($replacementButton); $root.Controls.Add($replacement)
        }}
        [Windows.Forms.Application]::DoEvents()
        [SkillMagnetFieldInput]::SetCursorPos($point.X, $point.Y) | Out-Null
        [SkillMagnetFieldInput]::FocusWindow($root.Handle) | Out-Null
        if ($Kind -eq "boundary_child_move") {{
            [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
                $button.SetBounds(31, 20, 170, 40)
                [Windows.Forms.Application]::DoEvents()
            }}
        }}
        elseif ($Kind -eq "boundary_child_disable") {{
            [SkillMagnetFieldInput]::TestAfterInitialValidation = [Action]{{
                $button.Enabled = $false
                [Windows.Forms.Application]::DoEvents()
            }}
        }}
        $rightRejected = $false
        try {{
            Invoke-CheckedExplorerPhysicalClick `
                $window $point.X $point.Y $true $rowSnapshot
        }} catch {{ $rightRejected = $true }}
        [Windows.Forms.Application]::DoEvents()
        return $left -and $rightRejected
    }}
    finally {{
        [SkillMagnetFieldInput]::TestAfterInitialValidation = $null
        $competitor.Close(); $root.Close()
    }}
}}
$script:rightCount = 0
[pscustomobject]@{{
    reparent = Run-Sequence "reparent" 0
    move = Run-Sequence "move" 4
    same_name_swap = Run-Sequence "same_name_swap" 8
    boundary_child_move = Run-Sequence "boundary_child_move" 12
    boundary_child_disable = Run-Sequence "boundary_child_disable" 16
    right_mouse_zero = $script:rightCount -eq 0
}} | ConvertTo-Json -Compress
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "between-click-row-guard.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-File", str(probe_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    def test_explorer_physical_clicks_share_the_final_identity_gate(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        gate = collector[
            collector.index("function Invoke-CheckedExplorerPhysicalClick") :
            collector.index("function Open-ExplorerContextMenu")
        ]
        for required in (
            "Get-FieldProcessIdentity",
            "Test-FieldProcessIdentity",
            "GetForegroundWindow() -eq $rootHandle",
            "GetAncestor($finalHwnd, 2) -eq $rootHandle",
            "WindowFromPoint($point)",
            "AutomationElement]::FromPoint",
            "Get-UiaRuntimeKey $finalUia",
            "Get-Utf8Sha256 ([string]$finalUia.Current.Name)",
            "CheckedClickCurrent",
            "Test-UiaSelfOrDescendantOfSnapshot $firstUia $ExpectedRowSnapshot",
            "Test-UiaSelfOrDescendantOfSnapshot $finalUia $ExpectedRowSnapshot",
            "no mouse input was sent",
        ):
            self.assertIn(required, gate)
        self.assertNotIn("mouse_event", gate)
        menu = collector[
            collector.index("function Open-ExplorerContextMenu") :
            collector.index("function Invoke-VisibleSkillMagnetRoot")
        ]
        self.assertEqual(menu.count("Invoke-CheckedExplorerPhysicalClick"), 3)
        self.assertIn(
            "Invoke-CheckedExplorerPhysicalClick $Window $x $y $true $selectedRowSnapshot",
            menu,
        )
        self.assertIn(
            "Invoke-CheckedExplorerPhysicalClick $Window $x $y $true $null",
            menu,
        )
        self.assertNotIn("LeftClick", menu)
        self.assertNotIn("RightClick", menu)

    def test_invoke_log_reader_does_not_block_native_append_writes(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        reader = collector[
            collector.index("function Read-InvokeLines") :
            collector.index("function Parse-InvokeLine")
        ]
        native_reader = collector[
            collector.index("public static class SkillMagnetStableLog") :
            collector.index('"@', collector.index("public static class SkillMagnetStableLog"))
        ]
        self.assertIn("FileShare.ReadWrite", native_reader)
        self.assertNotIn("FileShare.Delete", native_reader)
        self.assertIn("GetFileInformationByHandle", native_reader)
        self.assertIn("MemoryMappedFile.CreateFromFile", native_reader)
        self.assertIn("mappings.Add(mapping)", native_reader)
        self.assertIn("guard.Lock(offset, count)", native_reader)
        self.assertIn("guard.Unlock(range.Item1, range.Item2)", native_reader)
        self.assertIn("locks.Add(Tuple.Create(offset, count))", native_reader)
        self.assertIn("$snapshot = $script:InvokeLogReaders[$full].Read()", reader)
        self.assertNotIn("ReadAllText", reader)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell 5.1")
    def test_field_tree_hash_uses_ps5_root_bound_relative_paths(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        common = collector[
            collector.index("function Assert-Field") :
            collector.index("function Get-NativeSourceManifest")
        ]
        tree = collector[
            collector.index("function Get-RootBoundRelativePath") :
            collector.index("function Get-PersistentMutationSnapshot")
        ]
        encoded = base64.b64encode((common + "\n" + tree).encode("utf-8")).decode(
            "ascii"
        )
        probe = rf'''
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded}"))
. ([ScriptBlock]::Create($source))
$base = Join-Path $env:TEMP ("skill-magnet-ps5-relative-" + [guid]::NewGuid().ToString("N"))
$root = Join-Path $base "Root"
$child = Join-Path $root "Child"
$sibling = Join-Path $base "Root-prefix-sibling"
[IO.Directory]::CreateDirectory($child) | Out-Null
[IO.Directory]::CreateDirectory($sibling) | Out-Null
$file = Join-Path $child "File.txt"
[IO.File]::WriteAllText($file, "content", [Text.UTF8Encoding]::new($false))
try {{
    $selfAllowed = (Get-RootBoundRelativePath ($root + "\") $root) -ceq "."
    $childRelative = (Get-RootBoundRelativePath ($root.ToUpperInvariant()) $file) -ceq `
        "Child/File.txt"
    $treeDigest = (Get-TreeContentSha256 $root) -match "^[0-9a-f]{{64}}$"
    $siblingRejected = $false
    try {{ Get-RootBoundRelativePath $root $sibling | Out-Null }}
    catch {{ $siblingRejected = $true }}
    $driveRejected = $false
    try {{ Get-RootBoundRelativePath $root "Z:\not-under-root" | Out-Null }}
    catch {{ $driveRejected = $true }}
    $relativeRejected = $false
    try {{ Get-RootBoundRelativePath $root "Child\File.txt" | Out-Null }}
    catch {{ $relativeRejected = $true }}
    $dotSegmentRejected = $false
    try {{ Get-RootBoundRelativePath $root ($root + "\Child\..\Child\File.txt") | Out-Null }}
    catch {{ $dotSegmentRejected = $true }}
    $junction = Join-Path $root "junction"
    New-Item -ItemType Junction -Path $junction -Target $child | Out-Null
    $reparseRejected = $false
    try {{ Get-TreeContentSha256 $root | Out-Null }}
    catch {{ $reparseRejected = $true }}
    [pscustomobject]@{{
        powershell_major = $PSVersionTable.PSVersion.Major -eq 5
        root_self_allowed = $selfAllowed
        child_case_separator = $childRelative
        tree_digest = $treeDigest
        sibling_rejected = $siblingRejected
        drive_rejected = $driveRejected
        relative_rejected = $relativeRejected
        dot_segment_rejected = $dotSegmentRejected
        reparse_rejected = $reparseRejected
        no_core_api = -not $source.Contains("GetRelativePath")
    }} | ConvertTo-Json -Compress
}}
finally {{ Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue }}
'''
        completed = subprocess.run(
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-Command",
                probe,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    @unittest.skipUnless(os.name == "nt", "requires Windows file identities")
    def test_invoke_log_reader_rejects_partial_truncate_rotation_and_read_growth(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        reader_function = collector[
            collector.index("function Read-InvokeLines") :
            collector.index("function Parse-InvokeLine")
        ]
        encoded_csharp = base64.b64encode(csharp.encode("utf-8")).decode("ascii")
        encoded_reader = base64.b64encode(reader_function.encode("utf-8")).decode("ascii")
        probe = rf'''
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_csharp}"))
Add-Type -ReferencedAssemblies @(
    "UIAutomationClient", "UIAutomationTypes", "WindowsBase"
) -TypeDefinition $source
$readerSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded_reader}"))
. ([ScriptBlock]::Create($readerSource))
function Write-Utf16([string]$Path, [string]$Text) {{
    [IO.File]::WriteAllBytes($Path, [Text.Encoding]::Unicode.GetBytes($Text))
}}
function Append-Utf16([string]$Path, [string]$Text) {{
    $bytes = [Text.Encoding]::Unicode.GetBytes($Text)
    $stream = [IO.FileStream]::new(
        $Path, [IO.FileMode]::Append, [IO.FileAccess]::Write,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    )
    try {{ $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }}
    finally {{ $stream.Dispose() }}
}}
$root = Join-Path ([IO.Path]::GetTempPath()) ("skill-magnet-log-test-" + [guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {{
    $script:InvokeLogSnapshots = @{{}}
    $path = Join-Path $root "invoke.log"
    Write-Utf16 $path "one`r`n"
    $initial = @(Read-InvokeLines $path).Count -eq 1
    $sameLengthWriteRejected = $false
    try {{
        $attack = [IO.FileStream]::new($path, [IO.FileMode]::Open,
            [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        try {{
            $same = [Text.Encoding]::Unicode.GetBytes("two`r`n")
            $attack.Position = 0
            $attack.Write($same, 0, $same.Length)
            $attack.Flush($true)
        }} finally {{ $attack.Dispose() }}
    }} catch {{ $sameLengthWriteRejected = $true }}
    Append-Utf16 $path "append-ok`r`n"
    $normalLines = @(Read-InvokeLines $path)
    $normalEofAppend = $normalLines.Count -eq 2
    Append-Utf16 $path "part"
    $partialHeld = @(Read-InvokeLines $path).Count -eq 2
    Append-Utf16 $path "ial`r`n"
    $partialCompleted = @(Read-InvokeLines $path).Count -eq 3
    Append-Utf16 $path "terminal"
    $partialTerminalHeld = @(Read-InvokeLines $path).Count -eq 3
    Append-Utf16 $path "`r`n"
    $partialTerminalCompleted = @(Read-InvokeLines $path).Count -eq 4

    $truncateRejected = $false
    try {{ Write-Utf16 $path "x`r`n" }} catch {{ $truncateRejected = $true }}

    $script:InvokeLogSnapshots = @{{}}
    foreach ($reader in @($script:InvokeLogReaders.Values)) {{ $reader.Dispose() }}
    $script:InvokeLogReaders = @{{}}
    Write-Utf16 $path "one`r`n"
    Read-InvokeLines $path | Out-Null
    $incompleteRewriteRejected = $false
    try {{
        $attack = [IO.FileStream]::new($path, [IO.FileMode]::Open,
            [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        try {{
            $attack.SetLength(0)
            $forged = [Text.Encoding]::Unicode.GetBytes("one`r`nforged`r`n")
            $attack.Write($forged, 0, $forged.Length)
            $attack.Flush($true)
        }} finally {{ $attack.Dispose() }}
    }} catch {{ $incompleteRewriteRejected = $true }}
    $forgedRegrowthRejected = $incompleteRewriteRejected -and
        (@(Read-InvokeLines $path).Count -eq 1)

    $script:InvokeLogSnapshots = @{{}}
    foreach ($reader in @($script:InvokeLogReaders.Values)) {{ $reader.Dispose() }}
    $script:InvokeLogReaders = @{{}}
    Write-Utf16 $path "rotation-base`r`n"
    Read-InvokeLines $path | Out-Null
    $rotationRejected = $false
    try {{
        Move-Item -LiteralPath $path -Destination ($path + ".old")
        Write-Utf16 $path "rotation-new`r`n"
        Read-InvokeLines $path | Out-Null
    }} catch {{ $rotationRejected = $true }}

    $growthPath = Join-Path $root "growth.log"
    Write-Utf16 $growthPath "growth-base`r`n"
    [SkillMagnetStableLog]::TestAfterReadBeforeFinalIdentity = [Action]{{
        Append-Utf16 $growthPath "growth`r`n"
    }}
    $growth = [SkillMagnetStableLog]::Read($growthPath)
    [SkillMagnetStableLog]::TestAfterReadBeforeFinalIdentity = $null
    $growthRejected = -not $growth.Stable -and $growth.Error -eq "changed_during_read"

    $swapPath = Join-Path $root "swap.log"
    Write-Utf16 $swapPath "swap-base`r`n"
    [SkillMagnetStableLog]::TestAfterReadBeforeFinalIdentity = [Action]{{
        Move-Item -LiteralPath $swapPath -Destination ($swapPath + ".old")
        Write-Utf16 $swapPath "swap-new`r`n"
    }}
    $swap = [SkillMagnetStableLog]::Read($swapPath)
    [SkillMagnetStableLog]::TestAfterReadBeforeFinalIdentity = $null
    $swapRejected = -not $swap.Stable

    [pscustomobject]@{{
        initial = $initial
        same_length_write_rejected = $sameLengthWriteRejected
        normal_eof_append = $normalEofAppend
        partial_held = $partialHeld
        partial_completed = $partialCompleted
        partial_terminal_held = $partialTerminalHeld
        partial_terminal_completed = $partialTerminalCompleted
        truncate_rejected = $truncateRejected
        incomplete_rewrite_rejected = $incompleteRewriteRejected
        forged_regrowth_rejected = $forgedRegrowthRejected
        rotation_rejected = $rotationRejected
        growth_rejected = $growthRejected
        path_swap_rejected = $swapRejected
    }} | ConvertTo-Json -Compress
}}
finally {{
    [SkillMagnetStableLog]::TestAfterReadBeforeFinalIdentity = $null
    if ($null -ne $script:InvokeLogReaders) {{
        foreach ($reader in @($script:InvokeLogReaders.Values)) {{ $reader.Dispose() }}
        $script:InvokeLogReaders = @{{}}
    }}
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}}
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "stable-log-probe.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(probe_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip(), repr((completed.stdout, completed.stderr)))
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    @unittest.skipUnless(os.name == "nt", "requires Windows file identities")
    def test_owner_receipt_reader_detects_in_place_and_path_swap_races(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        csharp = collector.split(') -TypeDefinition @"', 1)[1].split('"@', 1)[0]
        encoded = base64.b64encode(csharp.encode("utf-8")).decode("ascii")
        probe = rf'''
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{encoded}"))
Add-Type -ReferencedAssemblies @("UIAutomationClient","UIAutomationTypes","WindowsBase") -TypeDefinition $source
$root = Join-Path ([IO.Path]::GetTempPath()) ("skill-magnet-owner-read-" + [guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {{
    $path = Join-Path $root "owner.json"
    [IO.File]::WriteAllText($path, '{{"revision":1}}' + "`n", [Text.UTF8Encoding]::new($false))
    $stable = [SkillMagnetStableBytes]::Read($path, 262144)
    [SkillMagnetStableBytes]::TestAfterFirstRead = [Action]{{
        [IO.File]::WriteAllText($path, '{{"revision":2}}' + "`n", [Text.UTF8Encoding]::new($false))
    }}
    $rewrite = [SkillMagnetStableBytes]::Read($path, 262144)
    [SkillMagnetStableBytes]::TestAfterFirstRead = $null
    [IO.File]::WriteAllText($path, '{{"revision":3}}' + "`n", [Text.UTF8Encoding]::new($false))
    [SkillMagnetStableBytes]::TestBeforePathReopen = [Action]{{
        Move-Item -LiteralPath $path -Destination ($path + ".old")
        [IO.File]::WriteAllText($path, '{{"revision":4}}' + "`n", [Text.UTF8Encoding]::new($false))
    }}
    $swap = [SkillMagnetStableBytes]::Read($path, 262144)
    [SkillMagnetStableBytes]::TestBeforePathReopen = $null
    $recovered = [SkillMagnetStableBytes]::Read($path, 262144)
    [pscustomobject]@{{
        initial_stable = $stable.Stable
        rewrite_rejected = -not $rewrite.Stable
        path_swap_rejected = -not $swap.Stable -and $swap.Error -eq "path_identity_changed"
        recovered_stable = $recovered.Stable
        file_share_delete_present = $source.Contains("FileShare.ReadWrite | FileShare.Delete")
    }} | ConvertTo-Json -Compress
}}
finally {{
    [SkillMagnetStableBytes]::TestAfterFirstRead = $null
    [SkillMagnetStableBytes]::TestBeforePathReopen = $null
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}}
'''
        with tempfile.TemporaryDirectory() as temporary:
            probe_path = Path(temporary) / "stable-owner-probe.ps1"
            probe_path.write_text(probe, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(probe_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observation = json.loads(completed.stdout.strip())
        self.assertTrue(all(observation.values()), observation)

    def test_field_collector_clicks_only_fixed_semantic_ids_from_live_receipt(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        click = collector[
            collector.index("function Invoke-FieldUiSurfaceWidget") :
            collector.index("function Register-FieldOwnedProcess")
        ]
        for semantic_id in ("library_manager", "register_selected"):
            self.assertIn(f"{semantic_id} =", click)
        for forbidden_crud_id in ("new_registration", "update", "delete", "reload"):
            self.assertNotIn(f'"{forbidden_crud_id}"', click)
        self.assertIn('Get-FieldUiSurfaceWidget $receipt.surface $Id "button"', click)
        self.assertIn("$expectedTextById", click)
        self.assertIn("$widget.text_sha256 -ceq $expectedWidgetTextSha256", click)
        self.assertIn("$freshWidget.text_sha256 -ceq $expectedWidgetTextSha256", click)
        self.assertIn("Test-UiaPointSnapshot $uiaHit $uiaSnapshot", click)
        self.assertIn("$expectedWidgetTextSha256", click)
        self.assertIn("[string]$uiaSnapshot.name_sha256", click)
        self.assertIn("$widget.screen", click)
        self.assertNotIn("fallback", click.casefold())

    def test_field_collector_binds_installed_runtime_before_explorer_input(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        pre_input = collector[: collector.index("$selectedWindow = Open-ExplorerFolder")]
        self.assertIn("$runtimeTreeWalker = @'", pre_input)
        self.assertIn("$releaseRuntimeProbe = $runtimeTreeWalker + @'", pre_input)
        self.assertIn("$releaseRuntimeDigest", pre_input)
        self.assertIn("[string]$runtime.payload_sha256 -ceq $releaseRuntimeDigest", pre_input)
        self.assertIn("no Explorer input was sent", pre_input)
        self.assertLess(
            pre_input.index("$releaseRuntimeDigest"),
            pre_input.index("$selectionProbe = @'"),
        )

    def test_field_collector_hashes_unowned_physical_runtime_files_fail_closed(self) -> None:
        collector = (
            ROOT / "tests" / "powershell" / "windows-explorer-direct-root-field-test.ps1"
        ).read_text(encoding="utf-8-sig")
        runtime_probe = collector[
            collector.index("$runtimeTreeWalker = @'") : collector.index("$selectionProbe = @'")
        ]
        self.assertIn("scanner = os.scandir(directory)", runtime_probe)
        self.assertIn("entry.stat(follow_symlinks=False)", runtime_probe)
        self.assertIn("runtime_identity(os.fstat(descriptor), False)", runtime_probe)
        self.assertIn("RUNTIME_MAX_ENTRIES = 4096", runtime_probe)
        self.assertIn("RUNTIME_MAX_FILE_BYTES", runtime_probe)
        self.assertIn("RUNTIME_MAX_TOTAL_BYTES", runtime_probe)
        self.assertIn("RUNTIME_MAX_SECONDS", runtime_probe)
        self.assertIn('== "_native/windows-modern-context-menu/out"', runtime_probe)
        self.assertNotIn(".rglob(", runtime_probe)
        self.assertNotIn(".read_bytes(", runtime_probe)
        self.assertEqual(runtime_probe.count("distribution.files"), 1)

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
            collector.index("function Get-VisibleDescendantText")
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

    def test_field_bundle_main_path_validates_every_ui_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ), mock.patch(
                "integration.explorer_results_gate._validate_ui_owner_receipt_schema",
                wraps=_validate_ui_owner_receipt_schema,
            ) as validator:
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertEqual(errors, [])
            self.assertEqual(validator.call_count, 6)

    def test_field_bundle_rejects_missing_ui_receipts_and_missing_surface(self) -> None:
        for label, mutation, expected in (
            (
                "missing receipts",
                lambda bundle: bundle.pop("ui_receipts"),
                "ui_receipts must contain exactly six",
            ),
            (
                "selection receipt missing surface",
                lambda bundle: bundle["ui_receipts"][0]["receipt"].pop("ui_surface"),
                "owner must contain ui_surface",
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
                mutation(bundle)
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_field_bundle_rejects_ui_receipt_extra_and_digest_tampering(self) -> None:
        mutations = (
            (
                "extra nested key",
                lambda entry: entry["receipt"].__setitem__("private_token", "secret"),
                "receipt schema mismatch",
            ),
            (
                "receipt digest",
                lambda entry: entry.__setitem__("receipt_sha256", "0" * 64),
                "receipt_sha256 mismatch",
            ),
            (
                "claim",
                lambda entry: entry.__setitem__("claim_sha256", "0" * 64),
                "claim does not match",
            ),
            (
                "role phase",
                lambda entry: entry.__setitem__("phase", "library_manager"),
                "phase does not match role",
            ),
        )
        for label, mutate, expected in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
                mutate(bundle["ui_receipts"][0])
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_field_bundle_rejects_registration_source_digest_not_anchored_in_invoke_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            replacement = "6" * 64
            entries = [
                json.loads(line)
                for line in base64.b64decode(bundle["uia_transcript"]["bytes_base64"])
                .decode("utf-8")
                .splitlines()
            ]
            entries[12]["data"]["registration_source_sha256"] = replacement
            self._replace_transcript(bundle, entries)
            bundle["registration_recovery_observation"][
                "registration_source_sha256"
            ] = replacement
            receipt_entry = next(
                item for item in bundle["ui_receipts"] if item["role"] == "registration_source"
            )
            receipt_entry["claim_sha256"] = replacement
            surface = receipt_entry["receipt"]["ui_surface"]
            surface["widgets"][0]["value_sha256"] = replacement
            canonical = lambda value: json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            receipt_entry["surface_sha256"] = hashlib.sha256(canonical(surface)).hexdigest()
            receipt_entry["receipt_sha256"] = hashlib.sha256(
                canonical(receipt_entry["receipt"])
            ).hexdigest()
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any("registration source digest does not bind" in error for error in errors),
                errors,
            )

    def test_field_bundle_rejects_native_project_digest_used_as_receipt_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            entry = next(
                item
                for item in bundle["ui_receipts"]
                if item["role"] == "selected_manager_click"
            )
            self.assertNotEqual(entry["project_sha256"], entry["target_sha256"])
            entry["target_sha256"] = entry["project_sha256"]
            entry["receipt"]["target_sha256"] = entry["project_sha256"]
            canonical = lambda value: json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            entry["receipt_sha256"] = hashlib.sha256(
                canonical(entry["receipt"])
            ).hexdigest()
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any("does not bind verified native workflow" in error for error in errors),
                errors,
            )

    def test_field_bundle_rejects_cross_role_binding_swap_and_receipt_reuse(self) -> None:
        for case in ("native-binding-swap", "receipt-reuse"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(
                    Path(temporary)
                )
                selected = next(
                    item
                    for item in bundle["ui_receipts"]
                    if item["role"] == "selected_manager_click"
                )
                background = next(
                    item
                    for item in bundle["ui_receipts"]
                    if item["role"] == "background_selection"
                )
                if case == "native-binding-swap":
                    binding_keys = (
                        "native_role",
                        "invocation_id",
                        "project_sha256",
                        "target_sha256",
                        "process_id",
                        "native_sequence_sha256",
                    )
                    selected_binding = {key: selected[key] for key in binding_keys}
                    for key in binding_keys:
                        selected[key] = background[key]
                        background[key] = selected_binding[key]
                else:
                    background["receipt"] = selected["receipt"]
                    background["receipt_sha256"] = selected["receipt_sha256"]
                    background["surface_sha256"] = selected["surface_sha256"]
                self._rewrite_bundle(bundle_path, bundle, ledger)
                with mock.patch(
                    "integration.explorer_results_gate._verify_windows_field_attestation",
                    return_value=[],
                ):
                    errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
                expected = (
                    "does not match role"
                    if case == "native-binding-swap"
                    else "reuses a receipt"
                )
                self.assertTrue(any(expected in error for error in errors), errors)

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

    def test_field_bundle_rejects_ambiguous_duplicate_msix_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, bundle, _ = self._field_fixture(Path(temporary))
            artifact = bundle["artifacts"]["signed_msix"]
            original = base64.b64decode(artifact["bytes_base64"])
            changed_buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(
                changed_buffer, "w", compression=zipfile.ZIP_STORED
            ) as target:
                for info in source.infolist():
                    target.writestr(info, source.read(info))
                target.writestr("SkillMagnetCommand.dll", b"ambiguous-second-entry")
            changed = changed_buffer.getvalue()
            digest = hashlib.sha256(changed).hexdigest()
            artifact.update(
                bytes_base64=base64.b64encode(changed).decode("ascii"),
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
            artifact["size"] = len(changed)
            artifact["sha256"] = digest
            bundle["hashes"]["config_sha256"] = digest
            self._rewrite_bundle(bundle_path, bundle, ledger)
            with mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any("config artifact size/hash" in error for error in errors), errors
            )

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

    def test_field_bundle_reports_release_runtime_safety_failure_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger, bundle_path, invoke_log, _, _ = self._field_fixture(Path(temporary))
            with mock.patch(
                "integration.explorer_results_gate._release_runtime_payload_sha256",
                side_effect=ValueError(
                    "release runtime safety scan rejected native source: reparse point; "
                    "restore and rerun"
                ),
            ), mock.patch(
                "integration.explorer_results_gate._verify_windows_field_attestation",
                return_value=[],
            ):
                errors = validate_field_bundle(ledger, bundle_path, invoke_log, ROOT)
            self.assertTrue(
                any(
                    "could not be verified safely before acceptance" in error
                    and "restore and rerun" in error
                    for error in errors
                ),
                errors,
            )

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
            self.assertTrue(
                any("exactly 40 native/identity records" in error for error in errors),
                errors,
            )

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
