import queue
from datetime import datetime, timedelta

from nudge.config import Settings
from nudge.notify import Action, decode_action, encode_action
from nudge.runner import SingleInstance, tick
from nudge.transfer import dump, load

from .conftest import NOW


class FakeNotifier:
    def __init__(self):
        self.shown = []

    def show(self, reminder, *, nag, snooze_minutes, sound):
        self.shown.append((reminder.title, nag))


SETTINGS = Settings(quiet_start="", quiet_end="", nag_minutes=15)


def test_action_round_trip():
    assert decode_action(encode_action("done", 7)) == Action("done", 7)
    assert decode_action(encode_action("snooze", 3, 60)) == Action("snooze", 3, 60)
    assert decode_action("snooze:3") == Action("snooze", 3, None)
    for bad in (None, "", "nope:1", "done:x", "done"):
        assert decode_action(bad) is None


def test_tick_notifies_then_nags_then_stops_after_done(svc):
    r = svc.add("Water plants", "every day at 9am", now=NOW)
    n = FakeNotifier()
    actions: queue.Queue = queue.Queue()
    assert tick(svc, n, SETTINGS, actions, now=r.due_at) == 1
    assert tick(svc, n, SETTINGS, actions, now=r.due_at + timedelta(minutes=1)) == 0
    assert tick(svc, n, SETTINGS, actions, now=r.due_at + timedelta(minutes=15)) == 1
    assert n.shown == [("Water plants", False), ("Water plants", True)]

    # Pressing "Done" on the toast
    actions.put(Action("done", r.id))
    assert tick(svc, n, SETTINGS, actions, now=r.due_at + timedelta(minutes=40)) == 0
    assert svc.get(r.id).due_at == r.due_at + timedelta(days=1)


def test_tick_snooze_action(svc):
    r = svc.add("Stretch", "in 5 minutes", now=NOW)
    n = FakeNotifier()
    actions: queue.Queue = queue.Queue()
    tick(svc, n, SETTINGS, actions, now=r.due_at)
    actions.put(Action("snooze", r.id, 10))
    tick(svc, n, SETTINGS, actions, now=r.due_at + timedelta(minutes=1))
    assert len(n.shown) == 1
    tick(svc, n, SETTINGS, actions, now=r.due_at + timedelta(minutes=11))
    assert len(n.shown) == 2


def test_tick_action_for_deleted_reminder_is_harmless(svc):
    actions: queue.Queue = queue.Queue()
    actions.put(Action("done", 999))
    logs = []
    tick(svc, FakeNotifier(), SETTINGS, actions, now=NOW, log=logs.append)
    assert "no longer exists" in logs[0]


def test_tick_survives_notifier_errors(svc):
    class Broken:
        def show(self, *a, **k):
            raise RuntimeError("boom")

    svc.add("X", "in 1 minute", now=NOW)
    logs = []
    assert tick(svc, Broken(), SETTINGS, now=NOW + timedelta(minutes=1), log=logs.append) == 0
    assert "boom" in logs[0]


def test_single_instance(tmp_path):
    a, b = SingleInstance(tmp_path / "l"), SingleInstance(tmp_path / "l")
    assert a.acquire()
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_export_import_round_trip(svc, tmp_path):
    svc.add("Meds #health !high", "every day at 8am and 8pm", notes="with food", now=NOW)
    svc.add("Call mom", "in 2 hours", now=NOW)
    reminders = svc.list()
    for fmt in ("yaml", "csv"):
        items = load(dump(reminders, fmt), fmt)
        assert items[0]["title"] == "Call mom"
        assert items[0]["when"] == "2026-11-24 14:00"
        assert items[1]["when"] == "every day at 8am and 8pm"
        assert items[1]["priority"] == "high"
        assert items[1]["notes"] == "with food"
    from nudge.service import ReminderService

    other = ReminderService(tmp_path / "other.db")
    added, errors = other.add_many(load(dump(reminders, "yaml"), "yaml"), now=NOW)
    assert errors == []
    assert [(r.title, r.due_at, r.tag_list) for r in added] == [
        ("Call mom", datetime(2026, 11, 24, 14), []),
        ("Meds", datetime(2026, 11, 24, 20), ["health"]),
    ]
