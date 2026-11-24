from datetime import datetime

import pytest

from nudge.service import ReminderService

# Tuesday 24 Nov 2026, 12:00 local
NOW = datetime(2026, 11, 24, 12, 0)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NUDGE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def svc(isolated_home):
    return ReminderService(isolated_home / "test.db")
