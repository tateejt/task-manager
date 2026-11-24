import subprocess
import sys

from nudge import startup


def test_shortcuts_ask_windows_for_folders_and_report_failures(monkeypatch):
    calls = []

    def fake_run(cmd, env, **kw):
        calls.append(env["NUDGE_FOLDER"])
        if env["NUDGE_FOLDER"] == "Desktop":
            return subprocess.CompletedProcess(cmd, 1, "", "Exception calling Save: access denied\nmore")
        return subprocess.CompletedProcess(cmd, 0, "C:\\Users\\me\\Start Menu\\Programs\\Nudge.lnk\n", "")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)
    made, errors = startup.create_shortcuts(desktop=True)
    assert calls == ["Programs", "Desktop"]
    assert [str(p).endswith("Nudge.lnk") for p in made] == [True]
    assert errors == ["Desktop: Exception calling Save: access denied"]
    assert "GetFolderPath" in startup._SHORTCUT_PS
