@echo off
rem Double-click this after downloading a new zip of Nudge and replacing the files.
rem It reinstalls the `nudge` command from this folder so you get the new version.
cd /d "%~dp0"
echo.
echo  Updating Nudge from %CD%
echo  (If Nudge is open, quit it from the tray icon first.)
echo.
where uv >nul 2>&1
if errorlevel 1 (
  echo  uv isn't installed. Get it from https://docs.astral.sh/uv/ and run this again.
  pause
  exit /b 1
)
uv tool install --reinstall .
if errorlevel 1 (
  echo.
  echo  The update didn't finish. Quit Nudge from the tray and try again.
  pause
  exit /b 1
)
nudge shortcut >nul 2>&1
echo.
nudge --version
echo  Updated. Open Nudge from the Start menu or by typing: nudge
echo.
pause
