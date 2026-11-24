"""Start the runner when you sign in to Windows.

Uses the per-user ``Run`` registry key, which needs no admin rights and launches
``pythonw.exe`` so no console window appears.
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Nudge"


def _pythonw() -> Path:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else exe


def launch_command(app: bool = False) -> str:
    """The command run at sign-in: the tray app (``app=True``) or the headless notifier."""
    if app:
        return f'"{_pythonw()}" -m nudge.gui --minimized'
    return f'"{_pythonw()}" -m nudge run --quiet'


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Starting at login is only supported on Windows.")


def install(app: bool = False) -> str:
    _require_windows()
    import winreg

    cmd = launch_command(app)
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


def create_shortcuts(desktop: bool = True) -> list[Path]:
    """Add "Nudge" to the Start menu (and optionally the desktop) so the app opens with a click."""
    _require_windows()
    import os
    import subprocess

    from .notify import asset_path

    targets = [
        Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Nudge.lnk"
    ]
    if desktop:
        targets.append(Path(os.environ["USERPROFILE"]) / "Desktop" / "Nudge.lnk")
    for lnk in targets:
        script = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:NUDGE_LNK);"
            "$s.TargetPath=$env:NUDGE_TARGET;$s.Arguments='-m nudge.gui';"
            "$s.IconLocation=$env:NUDGE_ICON;$s.Description='Nudge reminders';$s.Save()"
        )
        env = {**os.environ, "NUDGE_LNK": str(lnk), "NUDGE_TARGET": str(_pythonw()),
               "NUDGE_ICON": str(asset_path("nudge.ico"))}  # fmt: skip
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, env=env,
                       capture_output=True)  # fmt: skip
    return targets
