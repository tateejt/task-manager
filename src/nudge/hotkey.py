"""A system-wide keyboard shortcut (Windows), used to pop open the scratchpad from any app.

Uses the Win32 ``RegisterHotKey`` API on a background thread, so it needs no extra packages and
no admin rights. Elsewhere it does nothing.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable

MODIFIERS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, ";": 0xBA, "/": 0xBF, "`": 0xC0, ".": 0xBE, ",": 0xBC,
}  # fmt: skip


def parse(combo: str) -> tuple[int, int]:
    """``"win+shift+n"`` -> (modifier flags, virtual-key code). Raises ValueError if it isn't valid."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("A shortcut needs at least one modifier and a key, e.g. win+shift+n")
    *mods, key = parts
    flags = 0
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"Unknown modifier '{m}'. Use ctrl, alt, shift or win.")
        flags |= MODIFIERS[m]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"Unknown key '{key}'")
    return flags, vk


def pretty(combo: str) -> str:
    """``"win+shift+n"`` -> ``"Win+Shift+N"``."""
    return "+".join(p.upper() if len(p) == 1 else p.capitalize() for p in combo.lower().split("+"))


class GlobalHotkey:
    """Calls ``callback`` (on a background thread) whenever the shortcut is pressed anywhere."""

    def __init__(self, combo: str, callback: Callable[[], None]):
        self.combo = combo
        self.callback = callback
        self.ok = False
        self.error = ""
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def start(self, timeout: float = 2.0) -> bool:
        if sys.platform != "win32":
            self.error = "Only available on Windows"
            return False
        try:
            mods, vk = parse(self.combo)
        except ValueError as e:
            self.error = str(e)
            return False
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(mods, vk, ready), daemon=True, name="nudge-hotkey"
        )
        self._thread.start()
        ready.wait(timeout)
        return self.ok

    def _run(self, mods: int, vk: int, ready: threading.Event) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            self.error = "Another app is already using this shortcut"
            ready.set()
            return
        self.ok = True
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    with contextlib.suppress(Exception):
                        self.callback()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if sys.platform == "win32" and self._thread_id:  # pragma: no cover - Windows only
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.ok = False
