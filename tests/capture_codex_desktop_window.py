from __future__ import annotations

import argparse
import ctypes
import json
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetDIBits.argtypes = (
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.c_void_p,
    wintypes.UINT,
)
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.DeleteDC.restype = wintypes.BOOL
user32.GetWindowDC.argtypes = (wintypes.HWND,)
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
user32.PrintWindow.restype = wintypes.BOOL


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    _fields_ = (("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3))


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    value = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, value, len(value))
    return value.value


def largest_chatgpt_window(target_pid: int | None = None) -> tuple[int, int, str, RECT]:
    candidates: list[tuple[int, int, str, RECT]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not process:
            return True
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                process, 0, path, ctypes.byref(size)
            ):
                return True
            name = Path(path.value).name.casefold()
        finally:
            kernel32.CloseHandle(process)
        if target_pid is not None:
            if pid.value != target_pid:
                return True
        elif name not in {"chatgpt.exe", "codex.exe"}:
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if area:
            candidates.append((int(hwnd), int(pid.value), window_text(hwnd), rect))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if not candidates:
        raise RuntimeError("No visible Codex Desktop window found")
    return max(
        candidates,
        key=lambda item: (item[3].right - item[3].left)
        * (item[3].bottom - item[3].top),
    )


def capture(hwnd: int, rect: RECT, output: Path) -> None:
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise RuntimeError("PrintWindow failed")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(info),
            0,
        ):
            raise RuntimeError("GetDIBits failed")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.frombuffer(
            "RGB", (width, height), pixels, "raw", "BGRX", 0, 1
        ).save(output)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    hwnd, pid, title, rect = largest_chatgpt_window(args.pid)
    capture(hwnd, rect, args.output)
    metadata = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_handle": hwnd,
        "window_pid": pid,
        "window_title": title,
        "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
        "capture_method": "Win32 PrintWindow(PW_RENDERFULLCONTENT), window rectangle only",
    }
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
