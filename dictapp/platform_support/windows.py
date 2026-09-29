"""Windows implementation: selected-text capture via simulated Ctrl+C with full
clipboard save/restore, start-with-Windows via the HKCU Run key, and
WS_EX_NOACTIVATE for the popup."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import time
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Formats whose handles are not HGLOBAL memory (GDI objects etc.) can't be copied byte-wise.
_SKIP_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
_MAX_SAVE_BYTES = 64 * 1024 * 1024

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE = "EnZhDict"


# ------------------------------------------------------------------ clipboard
class _Clipboard:
    def __enter__(self):
        for _ in range(20):  # another app may hold it briefly
            if user32.OpenClipboard(None):
                return self
            time.sleep(0.01)
        raise OSError("clipboard busy")

    def __exit__(self, *exc):
        user32.CloseClipboard()


def _global_bytes(handle) -> bytes | None:
    size = kernel32.GlobalSize(handle)
    if not size:
        return None
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def save_clipboard() -> list[tuple[int, bytes]]:
    saved: list[tuple[int, bytes]] = []
    total = 0
    with _Clipboard():
        fmt = user32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _SKIP_FORMATS and not (0x200 <= fmt <= 0x3FF):
                handle = user32.GetClipboardData(fmt)
                data = _global_bytes(handle) if handle else None
                if data is not None:
                    total += len(data)
                    if total > _MAX_SAVE_BYTES:
                        log.info("clipboard too large to preserve fully")
                        break
                    saved.append((fmt, data))
            fmt = user32.EnumClipboardFormats(fmt)
    return saved


def restore_clipboard(saved: list[tuple[int, bytes]]) -> None:
    with _Clipboard():
        user32.EmptyClipboard()
        for fmt, data in saved:
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                continue
            ptr = kernel32.GlobalLock(h)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(fmt, h):
                kernel32.GlobalFree(h)  # ownership only transfers on success


def read_clipboard_text() -> str:
    with _Clipboard():
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)


def _modifiers_down() -> bool:
    # VK_SHIFT, VK_CONTROL, VK_MENU (Alt), VK_LWIN, VK_RWIN
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def _send_ctrl_c() -> None:
    from pynput.keyboard import Controller, Key
    kb = Controller()
    with kb.pressed(Key.ctrl):
        kb.press("c")
        kb.release("c")


def copy_selection(timeout: float = 0.5) -> str | None:
    """Copy the current selection in the foreground app and return it as text.

    The user's clipboard (all formats) is restored afterwards. Returns None if
    nothing was copied (no selection, or the app ignored Ctrl+C). Blocking —
    call from a worker thread.
    """
    # Wait (briefly) for the user to let go of modifiers so we send a clean Ctrl+C.
    t0 = time.monotonic()
    while _modifiers_down() and time.monotonic() - t0 < 0.3:
        time.sleep(0.01)
    try:
        saved = save_clipboard()
    except OSError:
        saved = None
    seq = user32.GetClipboardSequenceNumber()
    _send_ctrl_c()
    text = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.015)
        if user32.GetClipboardSequenceNumber() != seq:
            time.sleep(0.03)  # let the source app finish writing every format
            try:
                text = read_clipboard_text()
            except OSError:
                text = None
            break
    if user32.GetClipboardSequenceNumber() != seq and saved is not None:
        try:
            restore_clipboard(saved)
        except OSError:
            log.warning("could not restore clipboard")
    return text or None


# -------------------------------------------------------------- start-up entry
def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    main = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{pythonw if pythonw.exists() else exe}" "{main}" --tray'


def set_start_with_os(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_VALUE, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_VALUE)
            except FileNotFoundError:
                pass


def is_start_with_os() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_VALUE)
            return True
    except OSError:
        return False


# ------------------------------------------------------------- popup window
def make_noactivate(win_id: int) -> None:
    """Stop a window from taking focus when shown or clicked."""
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    hwnd = wt.HWND(int(win_id))
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
