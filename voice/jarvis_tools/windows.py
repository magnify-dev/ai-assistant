"""Jarvis tools - windows.py"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from pathlib import Path

from jarvis_tools.constants import (
    KEYEVENTF_KEYUP,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    PROCESS_QUERY_LIMITED_INFORMATION,
    SW_RESTORE,
    _context_window,
    kernel32,
    user32,
)

def _pid_to_exe(pid: int) -> str:
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""

def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd) + 1
    if length <= 1:
        return ""
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value.strip()

def _window_info(hwnd: int) -> tuple[str, str]:
    title = _window_title(hwnd)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return title, _pid_to_exe(pid.value)

def _enumerate_visible_windows() -> list[tuple[str, str, int]]:
    windows: list[tuple[str, str, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title, exe = _window_info(hwnd)
        if not title:
            return True
        windows.append((title, exe, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return windows

def capture_active_window_context() -> None:
    """Remember what the user was looking at when they said the wake word."""
    global _context_window
    hwnd = user32.GetForegroundWindow()
    title, exe = _window_info(hwnd)
    if title and not _is_assistant_window(exe, title):
        _context_window = {"title": title, "exe": exe, "captured_at": time.time()}
        return

    for title, exe, _hwnd in _enumerate_visible_windows():
        if "cursor" in exe.lower() or " - cursor" in title.lower():
            _context_window = {"title": title, "exe": exe, "captured_at": time.time()}
            return

    _context_window = {"title": title, "exe": exe, "captured_at": time.time()}

def get_active_window_title() -> str:
    global _context_window

    if _context_window:
        age = time.time() - float(_context_window.get("captured_at", 0))
        if age <= 120:
            title = str(_context_window.get("title", ""))
            exe = str(_context_window.get("exe", ""))
            if title and not _is_assistant_window(exe, title):
                return _format_window(title, exe)

    title, exe = _find_cursor_window()
    if title:
        return _format_window(title, exe)

    hwnd = user32.GetForegroundWindow()
    title, exe = _window_info(hwnd)
    if title and not _is_assistant_window(exe, title):
        return _format_window(title, exe)

    return "No active window title found"

def _find_cursor_window() -> tuple[str, str]:
    for title, exe, _hwnd in _enumerate_visible_windows():
        if exe.lower().endswith("cursor.exe"):
            return title, exe
    for title, exe, _hwnd in _enumerate_visible_windows():
        if " - cursor" in title.lower():
            return title, exe
    return "", ""

def _is_assistant_window(exe: str, title: str) -> bool:
    exe_lower = exe.lower().replace("/", "\\")
    if "ai-assistant" in exe_lower and exe_lower.endswith("python.exe"):
        return True
    if title.lower().endswith("python.exe") and "ai-assistant" in exe_lower:
        return True
    return False

def _format_window(title: str, exe: str) -> str:
    app = Path(exe).name if exe else "unknown"
    if title:
        return f"{title} ({app})"
    return app or "No active window title found"

def _find_firefox_hwnd(title_hint: str = "") -> int:
    hint = title_hint.lower().strip()
    fallback = 0
    for title, exe, hwnd in _enumerate_visible_windows():
        if "firefox" not in exe.lower():
            continue
        if hint and hint in title.lower():
            return hwnd
        if not fallback:
            fallback = hwnd
    return fallback

def _focus_hwnd(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    # SW_RESTORE un-maximizes visible windows; only use it to un-minimize.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))
    except Exception:
        pass
    foreground = user32.GetForegroundWindow()
    fg_pid = wintypes.DWORD()
    fg_thread = user32.GetWindowThreadProcessId(foreground, ctypes.byref(fg_pid))
    current_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, fg_thread, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(current_thread, fg_thread, False)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == hwnd

def _restore_foreground(previous_hwnd: int, firefox_hwnd: int = 0) -> None:
    if not previous_hwnd or not user32.IsWindow(previous_hwnd):
        return
    if firefox_hwnd and previous_hwnd == firefox_hwnd:
        return
    if user32.GetForegroundWindow() == previous_hwnd:
        return
    if _focus_hwnd(previous_hwnd):
        logging.info("Restored focus after browser play (hwnd=%s)", previous_hwnd)
    else:
        logging.info("Could not restore previous focus (hwnd=%s)", previous_hwnd)

def _os_click_screen(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

def _os_press_key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
