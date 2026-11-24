from datetime import datetime, timedelta

import pytest

from nudge.gui.presenter import card_for, greeting, matches, sections, undo_message

from .conftest import NOW


def test_greeting():
    assert greeting(datetime(2026, 1, 1, 8)) == "Good morning"
    assert greeting(datetime(2026, 1, 1, 13)) == "Good afternoon"
    assert greeting(datetime(2026, 1, 1, 20)) == "Good evening"


def test_cards_and_sections(svc):
    overdue = svc.add("Pills !high", "in 5 minutes", now=NOW)
    later = svc.add("Water plants #home", "at 6pm", now=NOW)
    tomorrow = svc.add("Gym", "tomorrow at 7am", now=NOW)
    paused = svc.add("Old", "every day at 9am", now=NOW)
    svc.set_active(paused.id, False, now=NOW)
    now = NOW + timedelta(minutes=30)
    groups = sections(svc.list(include_inactive=True), now)
    assert [name for name, _ in groups] == ["Due now", "Later today", "Tomorrow", "Paused"]
    due = groups[0][1][0]
    assert (due.id, due.state, due.when, due.emoji) == (overdue.id, "overdue", "Overdue 25m", "💊")
    assert groups[1][1][0].id == later.id and groups[1][1][0].when == "In 5h 30m"
    assert groups[2][1][0].id == tomorrow.id
    assert groups[3][1][0].when == "Paused"


def test_search_and_tags(svc):
    a = svc.add("Water plants #home", "at 6pm", now=NOW)
    svc.add("Gym", "at 7pm", now=NOW)
    assert matches(a, "#home") and matches(a, "plants") and not matches(a, "#work")
    assert [c.id for _, cards in sections(svc.list(), NOW, "#home") for c in cards] == [a.id]


def test_snoozed_card(svc):
    r = svc.add("Stretch", "in 5 minutes", now=NOW)
    r = svc.snooze(r.id, 30, now=NOW + timedelta(minutes=5))
    c = card_for(r, NOW + timedelta(minutes=6))
    assert c.state == "snoozed" and c.when.startswith("Snoozed")


def test_undo_message():
    assert undo_message("done", "Walk") == "Marked done: Walk"


# ---------------------------------------------------------------- smoke test the real Flet controls

ft = pytest.importorskip("flet")


class StubWindow:
    def __getattr__(self, name):
        return None

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


class StubPage:
    """Just enough of flet.Page for the window to build its controls without a running app."""

    def __init__(self):
        self.window = None
        self.dialogs = []
        self.added = []

    def add(self, *controls):
        self.added += controls

    def update(self):
        pass

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn, *a):
        pass


@pytest.fixture
def window(svc):
    from nudge.gui.app import NudgeWindow

    svc.add("Water plants #home !high", "every mon,thu at 9am", notes="Ferns too")
    svc.add("Call mom", "in 5 minutes")
    w = NudgeWindow(StubPage(), svc)
    w.body = ft.Container()
    return w


def test_views_build(window):
    for view in (window.reminders_view, window.upcoming_view, window.history_view, window.settings_view):
        assert view() is not None


def test_actions_and_undo_snackbar(window, svc):
    rid = svc.list()[0].id
    window.act(rid, "done")
    assert svc.get(rid).streak == 1
    bar = window.page.dialogs[-1]
    assert isinstance(bar, ft.SnackBar) and bar.action is not None
    window.act(rid, "done")  # second press is refused, not stacked
    assert svc.get(rid).streak == 1
    window.undo()
    assert svc.get(rid).streak == 0
    window.act(rid, "snooze", "tomorrow")
    assert svc.get(rid).snoozed_until is not None


def test_dialog_builds_and_saves(window, svc):
    from nudge.gui.app import ReminderDialog

    d = ReminderDialog(window)
    d.title.value = "Stretch every 2 hours"
    d.update()
    assert d.when == "every 2 hours" and not d.save_btn.disabled
    d.save()
    assert any(r.title == "Stretch" and r.schedule_label == "Every 2 hours" for r in svc.list())

    d = ReminderDialog(window)
    d.title.value = "Plants"
    d.repeat.value = "days"
    d.days.selected = ["mon", "thu"]
    d.times.value = "9am"
    d.update()
    assert d.preview_title.value == "Every Mon, Thu at 9:00 AM"

    d.quick.value = "every blursday"
    d.update()
    assert d.save_btn.disabled

    existing = svc.list()[0]
    d = ReminderDialog(window, existing)
    d.quick.value = "weekdays at 7"
    d.update()
    d.save()
    assert svc.get(existing.id).schedule_label == "Weekdays at 7:00 AM"
