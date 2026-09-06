from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )


def guid(value: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def method(pointer: ctypes.c_void_p, index: int, restype: object, *argtypes: object):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def check_hresult(result: int, operation: str) -> None:
    if result < 0:
        raise RuntimeError(f"{operation} failed: 0x{result & 0xFFFFFFFF:08x}")


def assert_native_source_binding(library: ctypes.WinDLL, dll_path: Path) -> None:
    export = library.SkillMagnetNativeSourceSha256
    export.argtypes = ()
    export.restype = ctypes.c_wchar_p
    digest = export()
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError("DLL native source digest export is invalid")
    manifest_path = dll_path.with_name("SkillMagnetNativeSource.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("native source manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("contract") != "skill-magnet-native-source-v1"
        or manifest.get("source_tree_sha256") != digest
        or not isinstance(manifest.get("inputs"), list)
    ):
        raise RuntimeError("DLL and native source manifest are not bound")


def release(pointer: ctypes.c_void_p) -> None:
    method(pointer, 2, ctypes.c_ulong)(pointer)


def get_title(command: ctypes.c_void_p) -> str:
    raw = ctypes.c_void_p()
    check_hresult(
        method(command, 3, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            command, None, ctypes.byref(raw)
        ),
        "GetTitle",
    )
    try:
        return ctypes.wstring_at(raw)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(raw)


def create_command(factory: ctypes.c_void_p, iid_command: GUID) -> ctypes.c_void_p:
    command = ctypes.c_void_p()
    check_hresult(
        method(
            factory,
            3,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(factory, None, ctypes.byref(iid_command), ctypes.byref(command)),
        "CreateInstance",
    )
    return command


class InterfacePointer(ctypes.Structure):
    _fields_ = (("lpVtbl", ctypes.POINTER(ctypes.c_void_p)),)


class FolderViewSite:
    """Minimal COM site matching Explorer's IServiceProvider folder-view chain."""

    _iid_unknown = bytes(guid("00000000-0000-0000-c000-000000000046"))
    _iid_service_provider = bytes(guid("6d5140c1-7436-11ce-8034-00aa006009fa"))
    _iid_folder_view = bytes(guid("cde725b0-ccc9-4519-917e-325d72fab4ce"))
    _iid_persist = bytes(guid("0000010c-0000-0000-c000-000000000046"))
    _iid_persist_folder = bytes(guid("000214ea-0000-0000-c000-000000000046"))
    _iid_persist_folder2 = bytes(guid("1ac3d9f0-175c-11d1-95be-00609797ea4f"))

    def __init__(self, folder: Path) -> None:
        self.references = 1
        self._callbacks: list[object] = []
        self._pidl = self._parse_pidl(folder)

        query_type = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        ref_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        service_type = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        folder_type = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        current_folder_type = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        )
        not_implemented_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)

        @query_type
        def query_interface(_this, iid, output):
            if not output:
                return -2147467261
            output[0] = None
            requested = bytes(iid.contents)
            if requested in {self._iid_unknown, self._iid_service_provider}:
                output[0] = self.service_pointer.value
            elif requested == self._iid_folder_view:
                output[0] = self.folder_pointer.value
            elif requested in {
                self._iid_persist,
                self._iid_persist_folder,
                self._iid_persist_folder2,
            }:
                output[0] = self.persist_pointer.value
            else:
                return -2147467262
            self.references += 1
            return 0

        @ref_type
        def add_ref(_this):
            self.references += 1
            return self.references

        @ref_type
        def release_ref(_this):
            self.references = max(0, self.references - 1)
            return self.references

        @service_type
        def query_service(_this, service, iid, output):
            if bytes(service.contents) != self._iid_folder_view:
                return -2147467262
            return query_interface(None, iid, output)

        @folder_type
        def get_folder(_this, iid, output):
            return query_interface(None, iid, output)

        @current_folder_type
        def get_current_folder(_this, output):
            if not output:
                return -2147467261
            ole32 = ctypes.windll.ole32
            ole32.CoTaskMemAlloc.argtypes = (ctypes.c_size_t,)
            ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
            copy = ole32.CoTaskMemAlloc(len(self._pidl))
            if not copy:
                return -2147024882
            ctypes.memmove(copy, self._pidl, len(self._pidl))
            output[0] = copy
            return 0

        @not_implemented_type
        def not_implemented(_this):
            return -2147467263

        self._callbacks.extend(
            [
                query_interface,
                add_ref,
                release_ref,
                query_service,
                get_folder,
                get_current_folder,
                not_implemented,
            ]
        )
        query = ctypes.cast(query_interface, ctypes.c_void_p).value
        add = ctypes.cast(add_ref, ctypes.c_void_p).value
        release_callback = ctypes.cast(release_ref, ctypes.c_void_p).value
        not_impl = ctypes.cast(not_implemented, ctypes.c_void_p).value
        self._service_vtable = (ctypes.c_void_p * 4)(
            query, add, release_callback, ctypes.cast(query_service, ctypes.c_void_p).value
        )
        self._folder_vtable = (ctypes.c_void_p * 17)(
            query,
            add,
            release_callback,
            not_impl,
            not_impl,
            ctypes.cast(get_folder, ctypes.c_void_p).value,
            *([not_impl] * 11),
        )
        self._persist_vtable = (ctypes.c_void_p * 6)(
            query,
            add,
            release_callback,
            not_impl,
            not_impl,
            ctypes.cast(get_current_folder, ctypes.c_void_p).value,
        )
        self._service = InterfacePointer(self._service_vtable)
        self._folder = InterfacePointer(self._folder_vtable)
        self._persist = InterfacePointer(self._persist_vtable)
        self.service_pointer = ctypes.cast(ctypes.pointer(self._service), ctypes.c_void_p)
        self.folder_pointer = ctypes.cast(ctypes.pointer(self._folder), ctypes.c_void_p)
        self.persist_pointer = ctypes.cast(ctypes.pointer(self._persist), ctypes.c_void_p)

    @staticmethod
    def _parse_pidl(folder: Path) -> bytes:
        shell32 = ctypes.windll.shell32
        shell32.SHParseDisplayName.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        shell32.SHParseDisplayName.restype = ctypes.c_long
        raw = ctypes.c_void_p()
        attributes = ctypes.c_uint32()
        check_hresult(
            shell32.SHParseDisplayName(
                str(folder), None, ctypes.byref(raw), 0, ctypes.byref(attributes)
            ),
            "SHParseDisplayName",
        )
        try:
            size = 0
            while True:
                component = ctypes.c_uint16.from_address(raw.value + size).value
                if component == 0:
                    size += ctypes.sizeof(ctypes.c_uint16)
                    break
                size += component
            return ctypes.string_at(raw, size)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(raw)


def assert_manifest_rejected(
    menu_path: Path, manifest: bytes, factory: ctypes.c_void_p, iid_command: GUID
) -> None:
    menu_path.write_bytes(manifest)
    command = create_command(factory, iid_command)
    try:
        state = ctypes.c_int()
        check_hresult(
            method(
                command,
                7,
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            )(command, None, 0, ctypes.byref(state)),
            "invalid manifest GetState",
        )
        flags = ctypes.c_int()
        check_hresult(
            method(command, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                command, ctypes.byref(flags)
            ),
            "invalid manifest GetFlags",
        )
        enumerator = ctypes.c_void_p()
        enum_result = method(
            command, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
        )(command, ctypes.byref(enumerator))
        invoke_result = method(
            command, 8, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p
        )(command, None, None)
        if state.value != 1 or flags.value != 0 or enum_result >= 0 or enumerator.value:
            if enumerator.value:
                release(enumerator)
            raise RuntimeError("invalid menu manifest remained enabled")
        if invoke_result >= 0:
            raise RuntimeError("invalid menu manifest remained invokable")
    finally:
        release(command)


def selection_digest(log: str, source: str) -> str:
    for line in log.splitlines():
        if (
            "event=selection_succeeded" in line
            and f"selection_source={source}" in line
        ):
            match = re.search(r"(?:^|\t)project_sha256=([0-9a-f]{64})(?:\t|$)", line)
            if match:
                return match.group(1)
    raise RuntimeError(f"selection evidence is missing for {source}")


def probe_manifest(probe_prefix: list[str], output_path: Path) -> bytes:
    command = (
        subprocess.list2cmdline(
            [*probe_prefix, "--argv-probe", str(output_path)]
        )
        + " __SKILL_MAGNET_PROJECT__"
    )
    return (
        "skill-magnet-menu-v4\r\n"
        "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
        f"Argument quoting contract probe.\t{command}\r\n"
    ).encode("utf-8")


def create_shell_item_array(paths: list[Path]) -> ctypes.c_void_p:
    shell32 = ctypes.windll.shell32
    shell32.SHParseDisplayName.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHCreateShellItemArrayFromIDLists.argtypes = (
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    shell32.SHCreateShellItemArrayFromIDLists.restype = ctypes.c_long
    pidls: list[ctypes.c_void_p] = []
    try:
        for path in paths:
            pidl = ctypes.c_void_p()
            attributes = ctypes.c_uint32()
            check_hresult(
                shell32.SHParseDisplayName(
                    str(path), None, ctypes.byref(pidl), 0, ctypes.byref(attributes)
                ),
                "SHParseDisplayName",
            )
            pidls.append(pidl)
        raw_pidls = (ctypes.c_void_p * len(pidls))(*(pidl.value for pidl in pidls))
        items = ctypes.c_void_p()
        check_hresult(
            shell32.SHCreateShellItemArrayFromIDLists(
                len(pidls), raw_pidls, ctypes.byref(items)
            ),
            "SHCreateShellItemArrayFromIDLists",
        )
        return items
    finally:
        for pidl in pidls:
            ctypes.windll.ole32.CoTaskMemFree(pidl)


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--argv-probe":
        Path(sys.argv[2]).write_text(sys.argv[3], encoding="utf-8")
        return 0
    if len(sys.argv) not in {2, 4} or (len(sys.argv) == 4 and sys.argv[2] != "--invoke"):
        return 2
    dll_path = Path(sys.argv[1]).resolve()
    invoke_path = Path(sys.argv[3]).resolve() if len(sys.argv) == 4 else None
    identity_path = dll_path.with_name("SkillMagnetIdentity.exe")
    if not identity_path.is_file():
        return 5
    menu_path = dll_path.with_name("SkillMagnetMenu.tsv")
    lines = menu_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "skill-magnet-menu-v4":
        return 3
    records = [line.split("\t") for line in lines[1:] if line]
    if (
        len(records) != 1
        or len(records[0]) != 7
        or records[0][0] != "__launcher__"
        or records[0][1] != "Skill Magnet"
        or records[0][2] != "launcher"
        or records[0][3] != "root"
        or records[0][4] != "Skill Magnet"
        or not records[0][5]
        or records[0][6].count("__SKILL_MAGNET_PROJECT__") != 1
    ):
        return 4

    with tempfile.TemporaryDirectory(prefix="SkillMagnetContract-") as local_app_data:
        os.environ["LOCALAPPDATA"] = local_app_data
        os.environ["SKILL_MAGNET_NATIVE_CONTRACT_TEST"] = "1"
        ctypes.windll.ole32.CoInitializeEx(None, 2)
        library = ctypes.WinDLL(str(dll_path))
        assert_native_source_binding(library, dll_path)
        get_class = library.DllGetClassObject
        get_class.argtypes = (
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        get_class.restype = ctypes.c_long
        clsid = guid("13e2a9dd-4378-4f9d-a385-973c61b19e63")
        iid_factory = guid("00000001-0000-0000-c000-000000000046")
        iid_command = guid("a08ce4d0-fa25-44ab-b57c-c7b1c323e0b9")
        factory = ctypes.c_void_p()
        check_hresult(
            get_class(ctypes.byref(clsid), ctypes.byref(iid_factory), ctypes.byref(factory)),
            "DllGetClassObject",
        )
        command = create_command(factory, iid_command)

        try:
            if get_title(command) != "Skill Magnet":
                raise RuntimeError("unexpected root title")
            state = ctypes.c_int()
            check_hresult(
                method(
                    command,
                    7,
                    ctypes.c_long,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                )(command, None, 0, ctypes.byref(state)),
                "GetState",
            )
            flags = ctypes.c_int()
            check_hresult(
                method(command, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                    command, ctypes.byref(flags)
                ),
                "GetFlags",
            )
            canonical = GUID()
            check_hresult(
                method(command, 6, ctypes.c_long, ctypes.POINTER(GUID))(
                    command, ctypes.byref(canonical)
                ),
                "GetCanonicalName",
            )
            if state.value != 0 or flags.value != 0 or bytes(canonical) != bytes(clsid):
                raise RuntimeError("direct root state, flags, or canonical identity mismatch")

            enumerator = ctypes.c_void_p()
            enum_result = method(
                command, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
            )(command, ctypes.byref(enumerator))
            if enum_result >= 0 or enumerator.value:
                if enumerator.value:
                    release(enumerator)
                raise RuntimeError("direct root unexpectedly exposes subcommands")

            log_path = Path(local_app_data) / "SkillMagnet" / "ContextMenu" / "invoke.log"
            if log_path.exists():
                raise RuntimeError("menu inspection produced an invoke event")

            original_manifest = menu_path.read_bytes()
            invalid_manifests = (
                (
                    "skill-magnet-menu-v4\r\n"
                    "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
                    "Valid launcher followed by invalid data.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 0 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                    "invalid-extra-line\r\n"
                ).encode("utf-8"),
                (
                    "skill-magnet-menu-v4\r\n"
                    "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tFirst.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 0 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                    "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tSecond.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 0 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                ).encode("utf-8"),
                (
                    "skill-magnet-menu-v4\r\n"
                    "__launcher__\tSkill Magnet\tpackage\troot\tSkill Magnet\tUnknown kind.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 0 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                ).encode("utf-8"),
                (
                    "\ufeffskill-magnet-menu-v4\r\n"
                    "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\tBOM header.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 0 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                ).encode("utf-8"),
            )
            try:
                for invalid_manifest in invalid_manifests:
                    assert_manifest_rejected(
                        menu_path, invalid_manifest, factory, iid_command
                    )
            finally:
                menu_path.write_bytes(original_manifest)

            invalid_log = log_path.read_text(encoding="utf-16-le")
            if any(
                f"event={event}" in invalid_log
                for event in ("child_exited", "child_running", "child_wait_failed")
            ):
                raise RuntimeError("invalid menu manifest launched a child process")

            selected_item = ctypes.c_void_p()
            selected_items = ctypes.c_void_p()
            if invoke_path is not None:
                selected_path = Path(local_app_data) / "selected folder \u65e5\u672c\u8a9e"
                background_path = Path(local_app_data) / "background folder \u65e5\u672c\u8a9e"
                selected_path.mkdir()
                background_path.mkdir()
                multiple_items = create_shell_item_array(
                    [selected_path, background_path]
                )
                try:
                    multiple_result = method(
                        command,
                        8,
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    )(command, multiple_items, None)
                    if multiple_result >= 0:
                        raise RuntimeError("multiple selected items were accepted")
                finally:
                    release(multiple_items)
                selected_probe_output = Path(local_app_data) / "selected-argv.txt"
                background_probe_output = Path(local_app_data) / "background-argv.txt"
                probe_prefix = [sys.executable, str(Path(__file__).resolve())]
                selected_probe_command = ctypes.c_void_p()
                background_probe_command = ctypes.c_void_p()
                try:
                    menu_path.write_bytes(
                        probe_manifest(probe_prefix, selected_probe_output)
                    )
                    selected_probe_command = create_command(factory, iid_command)
                    menu_path.write_bytes(
                        probe_manifest(probe_prefix, background_probe_output)
                    )
                    background_probe_command = create_command(factory, iid_command)
                finally:
                    menu_path.write_bytes(original_manifest)

                shell32 = ctypes.windll.shell32
                shell32.SHCreateItemFromParsingName.argtypes = (
                    ctypes.c_wchar_p,
                    ctypes.c_void_p,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
                iid_shell_item = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
                check_hresult(
                    shell32.SHCreateItemFromParsingName(
                        str(selected_path),
                        None,
                        ctypes.byref(iid_shell_item),
                        ctypes.byref(selected_item),
                    ),
                    "SHCreateItemFromParsingName",
                )
                shell32.SHCreateShellItemArrayFromShellItem.argtypes = (
                    ctypes.c_void_p,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                shell32.SHCreateShellItemArrayFromShellItem.restype = ctypes.c_long
                iid_shell_item_array = guid("b63ea76d-1f85-456f-a19c-48159efa858b")
                check_hresult(
                    shell32.SHCreateShellItemArrayFromShellItem(
                        selected_item,
                        ctypes.byref(iid_shell_item_array),
                        ctypes.byref(selected_items),
                    ),
                    "SHCreateShellItemArrayFromShellItem",
                )
                check_hresult(
                    method(
                        selected_probe_command,
                        8,
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    )(selected_probe_command, selected_items, None),
                    "direct root IExplorerCommand.Invoke",
                )
                selected_argument = selected_probe_output.read_text(encoding="utf-8")
                if selected_argument != str(selected_path):
                    raise RuntimeError("selected project argument was not quoted exactly")

                iid_object_with_site = guid("fc4801a3-2ba9-11cf-a229-00aa003d7352")
                site_aware = ctypes.c_void_p()
                check_hresult(
                    method(
                        background_probe_command,
                        0,
                        ctypes.c_long,
                        ctypes.POINTER(GUID),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(
                        background_probe_command,
                        ctypes.byref(iid_object_with_site),
                        ctypes.byref(site_aware),
                    ),
                    "QueryInterface(IObjectWithSite)",
                )
                background_site = FolderViewSite(background_path)
                try:
                    check_hresult(
                        method(
                            site_aware,
                            3,
                            ctypes.c_long,
                            ctypes.c_void_p,
                        )(site_aware, background_site.service_pointer),
                        "IObjectWithSite.SetSite",
                    )
                    check_hresult(
                        method(
                            background_probe_command,
                            8,
                            ctypes.c_long,
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                        )(background_probe_command, None, None),
                        "background direct root IExplorerCommand.Invoke",
                    )
                finally:
                    method(
                        site_aware, 3, ctypes.c_long, ctypes.c_void_p
                    )(site_aware, None)
                    release(site_aware)
                background_argument = background_probe_output.read_text(
                    encoding="utf-8"
                )
                if background_argument != str(background_path):
                    raise RuntimeError("background project argument was not quoted exactly")
                release(selected_probe_command)
                selected_probe_command = ctypes.c_void_p()
                release(background_probe_command)
                background_probe_command = ctypes.c_void_p()

                failure_manifest = (
                    "skill-magnet-menu-v4\r\n"
                    "__launcher__\tSkill Magnet\tlauncher\troot\tSkill Magnet\t"
                    "Immediate failure contract probe.\t"
                    '"C:\\Windows\\System32\\cmd.exe" /d /c exit 7 '
                    "__SKILL_MAGNET_PROJECT__\r\n"
                ).encode("utf-8")
                failure_command = ctypes.c_void_p()
                try:
                    menu_path.write_bytes(failure_manifest)
                    failure_command = create_command(factory, iid_command)
                finally:
                    menu_path.write_bytes(original_manifest)
                try:
                    failure_result = method(
                        failure_command,
                        8,
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    )(failure_command, selected_items, None)
                    if failure_result >= 0:
                        raise RuntimeError("immediate nonzero child exit was accepted")
                finally:
                    release(failure_command)
                    release(selected_items)
                    release(selected_item)

                log = log_path.read_text(encoding="utf-16-le")
                if "event=child_process_failed" not in log or "detail=7" not in log:
                    raise RuntimeError("immediate child failure evidence is missing")
                selected_project_digest = selection_digest(log, "selected_item")
                background_project_digest = selection_digest(log, "background_site")
                if selected_project_digest == background_project_digest:
                    raise RuntimeError("selected and background projects have the same digest")
                expected_selected_digest = hashlib.sha256(
                    str(selected_path).encode("utf-16-le")
                ).hexdigest()
                expected_background_digest = hashlib.sha256(
                    str(background_path).encode("utf-16-le")
                ).hexdigest()
                if selected_project_digest != expected_selected_digest:
                    raise RuntimeError("selected project digest is not stable SHA-256")
                if background_project_digest != expected_background_digest:
                    raise RuntimeError("background project digest is not stable SHA-256")
                if (
                    str(selected_path) in log
                    or str(background_path) in log
                    or local_app_data in log
                ):
                    raise RuntimeError("invoke log contains a plaintext project path")
                if selected_probe_command.value:
                    release(selected_probe_command)
                if background_probe_command.value:
                    release(background_probe_command)
        finally:
            release(command)
            release(factory)
        if library.DllCanUnloadNow() != 0:
            raise RuntimeError("DLL cannot unload after contract test")

        if any("SkillMagnetLauncher.exe" in line for line in lines):
            raise RuntimeError("policy-incompatible launcher remains in menu contract")
        ctypes.windll.ole32.CoUninitialize()
    print("SkillMagnet direct-root IExplorerCommand contract PASS (Python host)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
