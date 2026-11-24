from datetime import datetime, timedelta

import pytest

from nudge.service import AlreadyDone, NothingToUndo

from .conftest import NOW  # Tue 2026-11-24 12:00


def test_double_done_does_not_skip_ahead(svc):
    r = svc.add("Walk", "every day at 9am", now=datetime(2026, 11, 24, 8))
    t = datetime(2026, 11, 24, 9, 5)
    r = svc.complete(r.id, now=t)
    assert r.due_at == datetime(2026, 11, 25, 9)
    with pytest.raises(AlreadyDone):
        svc.complete(r.id, now=t + timedelta(seconds=1))
    with pytest.raises(AlreadyDone):
        svc.skip(r.id, now=t + timedelta(hours=5))
    assert svc.get(r.id).due_at == datetime(2026, 11, 25, 9)
    assert svc.get(r.id).streak == 1
    # Once tomorrow's occurrence starts, done works again
    r = svc.complete(r.id, now=datetime(2026, 11, 25, 9, 1))
    assert r.due_at == datetime(2026, 11, 26, 9)


def test_early_done_allowed_once_then_force(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)  # first due tomorrow
    r = svc.complete(r.id, now=NOW)  # early, allowed once
    assert r.due_at == datetime(2026, 11, 26, 9)
    with pytest.raises(AlreadyDone):
        svc.complete(r.id, now=NOW)
    r = svc.complete(r.id, now=NOW, force=True)
    assert r.due_at == datetime(2026, 11, 27, 9)


def test_undo_done_restores_everything(svc):
    r = svc.add("Walk", "every day at 9am", now=datetime(2026, 11, 24, 8))
    svc.complete(r.id, now=datetime(2026, 11, 24, 9, 5))
    action, r2 = svc.undo()
    assert action == "done"
    assert r2.due_at == datetime(2026, 11, 24, 9)
    assert r2.streak == 0 and r2.best_streak == 0
    assert svc.history(r.id) == []
    with pytest.raises(NothingToUndo):
        svc.undo()


def test_undo_one_off_done_reopens_it(svc):
    r = svc.add("Call mom", "in 1 hour", now=NOW)
    svc.complete(r.id, now=NOW)
    assert svc.list() == []
    svc.undo(r.id)
    assert [x.id for x in svc.list()] == [r.id]


def test_undo_walks_back_through_actions(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.snooze(r.id, 30, now=NOW)
    svc.set_active(r.id, False, now=NOW)
    assert svc.undo()[0] == "pause"
    assert svc.get(r.id).active
    assert svc.undo()[0] == "snooze"
    assert svc.get(r.id).snoozed_until is None


def test_undo_edit(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.edit(r.id, title="Run", when="every mon at 7am", now=NOW)
    _, r = svc.undo()
    assert (r.title, r.schedule_label, r.due_at) == (
        "Walk",
        "Every day at 9:00 AM",
        datetime(2026, 11, 25, 9),
    )


def test_undo_scoped_to_reminder(svc):
    a = svc.add("A", "every day at 9am", now=NOW)
    b = svc.add("B", "every day at 9am", now=NOW)
    svc.complete(a.id, now=NOW)
    svc.complete(b.id, now=NOW)
    action, r = svc.undo(a.id)
    assert r.id == a.id
    assert svc.get(b.id).streak == 1


def test_reset_puts_it_back_on_schedule(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.complete(r.id, now=NOW)
    svc.complete(r.id, now=NOW, force=True)
    svc.snooze(r.id, 60, now=NOW)
    r = svc.reset(r.id, now=NOW)
    assert r.due_at == datetime(2026, 11, 25, 9)
    assert r.snoozed_until is None
    assert svc.undo()[0] == "reset"


def test_delete_clears_undo(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.complete(r.id, now=NOW)
    svc.delete(r.id)
    with pytest.raises(NothingToUndo):
        svc.undo()
