from __future__ import annotations

import ctypes
import os
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


def main() -> int:
    if len(sys.argv) not in {2, 4} or (len(sys.argv) == 4 and sys.argv[2] != "--invoke"):
        return 2
    dll_path = Path(sys.argv[1]).resolve()
    invoke_path = Path(sys.argv[3]).resolve() if len(sys.argv) == 4 else None
    identity_path = dll_path.with_name("SkillMagnetIdentity.exe")
    if not identity_path.is_file():
        return 5
    menu_path = dll_path.with_name("SkillMagnetMenu.tsv")
    lines = menu_path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0] != "skill-magnet-menu-v4":
        return 3
    expected_titles = [line.split("\t")[4] for line in lines[1:] if line]
    if not expected_titles:
        return 4

    with tempfile.TemporaryDirectory(prefix="SkillMagnetContract-") as local_app_data:
        os.environ["LOCALAPPDATA"] = local_app_data
        ctypes.windll.ole32.CoInitializeEx(None, 2)
        library = ctypes.WinDLL(str(dll_path))
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
        command = ctypes.c_void_p()
        try:
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
        finally:
            release(factory)

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
            if state.value != 0 or not (flags.value & 1) or bytes(canonical) != bytes(clsid):
                raise RuntimeError("root state, flags, or canonical identity mismatch")

            enumerator = ctypes.c_void_p()
            check_hresult(
                method(command, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(
                    command, ctypes.byref(enumerator)
                ),
                "EnumSubCommands",
            )
            titles: list[str] = []
            invoke_succeeded = invoke_path is None
            selected_item = ctypes.c_void_p()
            selected_items = ctypes.c_void_p()
            if invoke_path is not None:
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
                        str(invoke_path),
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
            try:
                while True:
                    child = ctypes.c_void_p()
                    fetched = ctypes.c_ulong()
                    result = method(
                        enumerator,
                        3,
                        ctypes.c_long,
                        ctypes.c_ulong,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(ctypes.c_ulong),
                    )(enumerator, 1, ctypes.byref(child), ctypes.byref(fetched))
                    if result != 0 or fetched.value != 1:
                        break
                    try:
                        titles.append(get_title(child))
                        if invoke_path is not None and not invoke_succeeded:
                            check_hresult(
                                method(
                                    child,
                                    8,
                                    ctypes.c_long,
                                    ctypes.c_void_p,
                                    ctypes.c_void_p,
                                )(child, selected_items, None),
                                "IExplorerCommand.Invoke",
                            )
                            invoke_succeeded = True
                    finally:
                        release(child)
            finally:
                release(enumerator)
                if selected_items:
                    release(selected_items)
                if selected_item:
                    release(selected_item)
            if titles != expected_titles:
                raise RuntimeError(f"unexpected child titles: {titles!r}")
            if not invoke_succeeded:
                raise RuntimeError("child command was not invoked")
            if (
                invoke_path is None
                and (Path(local_app_data) / "SkillMagnet" / "ContextMenu" / "invoke.log").exists()
            ):
                raise RuntimeError("menu enumeration produced an invoke event")
        finally:
            release(command)
        if library.DllCanUnloadNow() != 0:
            raise RuntimeError("DLL cannot unload after contract test")

        # The sparse package needs an executable identity anchor, but Explorer
        # commands must never run it. SkillMagnetCommand.dll launches the
        # Authenticode-valid Python command from SkillMagnetMenu.tsv directly.
        if any("SkillMagnetLauncher.exe" in line for line in lines):
            raise RuntimeError("policy-incompatible launcher remains in menu contract")
        ctypes.windll.ole32.CoUninitialize()
    print("SkillMagnet IExplorerCommand contract PASS (Python host)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
