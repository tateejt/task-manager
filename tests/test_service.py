from datetime import datetime, timedelta

import pytest

from nudge.config import Settings
from nudge.schedule import ScheduleError
from nudge.service import NotFound

from .conftest import NOW

QUIET_OFF = Settings(quiet_start="", quiet_end="", nag_minutes=15, max_nags=3)


def test_add_with_inline_schedule_and_meta(svc):
    r = svc.add("Take meds #health !urgent every day at 8am", now=NOW)
    assert (r.title, r.tag_list, r.priority) == ("Take meds", ["health"], 3)
    assert r.due_at == datetime(2026, 11, 25, 8)
    assert r.when_text == "every day at 8am"


def test_add_requires_schedule_and_title(svc):
    with pytest.raises(ScheduleError):
        svc.add("No schedule here", now=NOW)
    with pytest.raises(ScheduleError):
        svc.add("#onlytag", "every day", now=NOW)


def test_complete_recurring_advances_and_counts_streak(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    t = r.due_at
    for i in range(3):
        r = svc.complete(r.id, now=t + timedelta(minutes=5))
        assert r.streak == i + 1
        assert r.due_at == t + timedelta(days=1)
        t = r.due_at
    assert r.best_streak == 3
    assert [c.status for c, _ in svc.history(r.id)] == ["done"] * 3


def test_completing_early_counts_for_upcoming_occurrence(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)  # due tomorrow 9am
    r = svc.complete(r.id, now=NOW)
    assert r.due_at == datetime(2026, 11, 26, 9)


def test_complete_one_off_finishes_it(svc):
    r = svc.add("Call mom", "in 1 hour", now=NOW)
    r = svc.complete(r.id, now=NOW + timedelta(hours=1))
    assert r.completed_at is not None
    assert svc.list() == []
    assert len(svc.list(include_done=True)) == 1


def test_missed_occurrence_resets_streak_and_rolls_forward(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.complete(r.id, now=datetime(2026, 11, 25, 9, 1))  # streak 1, next is 26th 9am
    rolled = svc.roll_missed(now=datetime(2026, 11, 28, 10))  # three days ignored
    assert [x.id for x in rolled] == [r.id]
    r = svc.get(r.id)
    assert r.streak == 0 and r.best_streak == 1
    assert r.due_at == datetime(2026, 11, 28, 9)  # latest started occurrence, alerts once
    assert [c.status for c, _ in svc.history(r.id)] == ["missed", "done"]


def test_roll_missed_ignores_current_occurrence(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    assert svc.roll_missed(now=r.due_at + timedelta(hours=3)) == []


def test_skip_keeps_streak(svc):
    r = svc.add("Gym", "every day at 6pm", now=NOW)
    r = svc.complete(r.id, now=r.due_at)
    r = svc.skip(r.id, now=r.due_at)
    assert r.streak == 1
    assert r.due_at == datetime(2026, 11, 26, 18)


def test_alerting_nagging_and_limits(svc):
    r = svc.add("Pills", "every day at 9am", now=NOW)
    due = r.due_at
    assert svc.due_for_alert(due - timedelta(minutes=1), QUIET_OFF) == []
    assert [x.id for x in svc.due_for_alert(due, QUIET_OFF)] == [r.id]
    svc.mark_notified(r.id, now=due)
    assert svc.due_for_alert(due + timedelta(minutes=5), QUIET_OFF) == []
    t = due
    for _ in range(2):
        t += timedelta(minutes=15)
        assert len(svc.due_for_alert(t, QUIET_OFF)) == 1
        svc.mark_notified(r.id, now=t)
    # max_nags=3 reached
    assert svc.due_for_alert(t + timedelta(hours=1), QUIET_OFF) == []


def test_nagging_disabled(svc):
    r = svc.add("Pills", "in 5 minutes", now=NOW)
    settings = Settings(quiet_start="", quiet_end="", nag_minutes=0)
    svc.mark_notified(r.id, now=r.due_at)
    assert svc.due_for_alert(r.due_at + timedelta(hours=2), settings) == []


def test_snooze_delays_alert(svc):
    r = svc.add("Stretch", "in 5 minutes", now=NOW)
    svc.mark_notified(r.id, now=r.due_at)
    r = svc.snooze(r.id, 30, now=r.due_at)
    assert r.snoozed_until == datetime(2026, 11, 24, 12, 35)
    assert svc.due_for_alert(datetime(2026, 11, 24, 12, 34), QUIET_OFF) == []
    assert len(svc.due_for_alert(datetime(2026, 11, 24, 12, 35), QUIET_OFF)) == 1


def test_snooze_before_due_pushes_from_due_time(svc):
    r = svc.add("Stretch", "in 60 minutes", now=NOW)
    r = svc.snooze(r.id, 30, now=NOW)
    assert r.snoozed_until == datetime(2026, 11, 24, 13, 30)


def test_quiet_hours_hold_back_all_but_urgent(svc):
    normal = svc.add("Normal", "at 11pm", now=NOW)
    urgent = svc.add("Urgent !urgent", "at 11pm", now=NOW)
    s = Settings(quiet_start="22:00", quiet_end="07:00")
    assert [r.id for r in svc.due_for_alert(datetime(2026, 11, 24, 23, 0), s)] == [urgent.id]
    assert {r.id for r in svc.due_for_alert(datetime(2026, 11, 25, 7, 0), s)} == {normal.id, urgent.id}


def test_quiet_hours_window():
    s = Settings(quiet_start="22:00", quiet_end="07:00")
    assert s.in_quiet_hours(datetime(2026, 1, 1, 23))
    assert s.in_quiet_hours(datetime(2026, 1, 1, 3))
    assert not s.in_quiet_hours(datetime(2026, 1, 1, 7))
    assert not s.in_quiet_hours(datetime(2026, 1, 1, 12))
    day = Settings(quiet_start="12:00", quiet_end="13:00")
    assert day.in_quiet_hours(datetime(2026, 1, 1, 12, 30))


def test_pause_and_resume_skips_old_occurrences(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.set_active(r.id, False)
    assert svc.list() == []
    assert svc.due_for_alert(datetime(2026, 11, 30, 10), QUIET_OFF) == []
    r = svc.set_active(r.id, True, now=datetime(2026, 11, 30, 10))
    assert r.due_at == datetime(2026, 12, 1, 9)


def test_edit_schedule_and_fields(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    r = svc.edit(r.id, when="every mon at 7am", tags=["Health"], priority="high", title="Run", now=NOW)
    assert (r.title, r.tag_list, r.priority) == ("Run", ["health"], 2)
    assert r.due_at == datetime(2026, 11, 30, 7)


def test_delete_and_not_found(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.complete(r.id, now=NOW)
    svc.delete(r.id)
    with pytest.raises(NotFound):
        svc.get(r.id)
    assert svc.history() == []


def test_list_sort_and_tag_filter(svc):
    a = svc.add("Later #home", "in 3 hours", now=NOW)
    b = svc.add("Sooner #work", "in 1 hour", now=NOW)
    assert [r.id for r in svc.list()] == [b.id, a.id]
    assert [r.id for r in svc.list(tag="#home")] == [a.id]


def test_agenda_expands_recurrences(svc):
    svc.add("Meds", "every day at 8am and 8pm", now=NOW)
    svc.add("Call", "tomorrow at 5pm", now=NOW)
    items = svc.agenda(NOW, NOW + timedelta(days=2))
    times = [t for t, _ in items]
    assert times == [
        datetime(2026, 11, 24, 20),
        datetime(2026, 11, 25, 8),
        datetime(2026, 11, 25, 17),
        datetime(2026, 11, 25, 20),
        datetime(2026, 11, 26, 8),
    ]


def test_stats(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    svc.complete(r.id, now=datetime(2026, 11, 25, 9))
    st = svc.stats(now=datetime(2026, 11, 25, 10))
    assert (st.active, st.done_7d, st.missed_7d) == (1, 1, 0)
    assert st.best == [("Walk", 1)]


def test_add_many_reports_errors(svc):
    added, errors = svc.add_many(
        [
            {"title": "A", "when": "every day at 9am", "tags": "x;y", "priority": "high"},
            {"title": "B", "when": "every blursday"},
            {"when": "daily"},
        ],
        now=NOW,
    )
    assert [r.title for r in added] == ["A"]
    assert added[0].tag_list == ["x", "y"]
    assert len(errors) == 2
