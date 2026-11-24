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


#: Creates one .lnk. Windows itself says where the Start menu and desktop are, so a desktop that
#: OneDrive (or a company policy) has moved still works. Paths come in through environment variables
#: so spaces, apostrophes or non-English characters in them can't break the script.
_SHORTCUT_PS = r"""
$ErrorActionPreference = 'Stop'
$dir = [Environment]::GetFolderPath($env:NUDGE_FOLDER)
if (-not $dir) { throw "Windows has no $($env:NUDGE_FOLDER) folder for this user" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lnk = Join-Path $dir 'Nudge.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $env:NUDGE_TARGET
$s.Arguments = '-m nudge.gui'
$s.WorkingDirectory = $env:USERPROFILE
$s.IconLocation = $env:NUDGE_ICON
$s.Description = 'Nudge reminders'
$s.Save()
Write-Output $lnk
"""


def shortcut_folders(desktop: bool = True) -> list[str]:
    """Windows "special folder" names for where shortcuts go (Programs = the Start menu)."""
    return ["Programs", "Desktop"] if desktop else ["Programs"]


def create_shortcuts(desktop: bool = True) -> tuple[list[Path], list[str]]:
    """Add "Nudge" to the Start menu (and optionally the desktop) so the app opens with a click.

    Returns the shortcuts made and a readable error for each one that couldn't be made.
    """
    _require_windows()
    import os
    import subprocess

    from .notify import asset_path

    made: list[Path] = []
    errors: list[str] = []
    for folder in shortcut_folders(desktop):
        env = {**os.environ, "NUDGE_FOLDER": folder, "NUDGE_TARGET": str(_pythonw()),
               "NUDGE_ICON": str(asset_path("nudge.ico"))}  # fmt: skip
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _SHORTCUT_PS],
                env=env, capture_output=True, text=True, timeout=60,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"{folder}: couldn't run PowerShell ({e})")
            continue
        out = (proc.stdout or "").strip().splitlines()
        if proc.returncode == 0 and out:
            made.append(Path(out[-1]))
        else:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            errors.append(f"{folder}: {detail[0] if detail else f'PowerShell exited with {proc.returncode}'}")
    return made, errors
