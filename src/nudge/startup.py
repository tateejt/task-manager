"""Start the runner when you sign in to Windows.

Uses the per-user ``Run`` registry key, which needs no admin rights and launches
``pythonw.exe`` so no console window appears.
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Nudge"


def launch_command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    return f'"{interpreter}" -m nudge run --quiet'


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Starting at login is only supported on Windows.")


def install() -> str:
    _require_windows()
    import winreg

    cmd = launch_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def uninstall() -> bool:
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def status() -> str | None:
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except FileNotFoundError:
        return None
