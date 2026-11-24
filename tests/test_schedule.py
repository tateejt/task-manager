from datetime import datetime, time

import pytest

from nudge.schedule import (
    ScheduleError,
    extract_inline_meta,
    next_occurrence,
    parse_schedule,
    parse_time,
    split_title_and_schedule,
)

from .conftest import NOW  # Tuesday 2026-11-24 12:00


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9", time(9)),
        ("9am", time(9)),
        ("9:30pm", time(21, 30)),
        ("12am", time(0)),
        ("12pm", time(12)),
        ("21:15", time(21, 15)),
        ("noon", time(12)),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "13pm", "nine", "9:75"])
def test_parse_time_rejects_garbage(text):
    with pytest.raises(ScheduleError):
        parse_time(text)


@pytest.mark.parametrize(
    ("expr", "first", "second"),
    [
        ("every day at 9am", datetime(2026, 11, 25, 9), datetime(2026, 11, 26, 9)),
        ("daily at 7:30 and 9pm", datetime(2026, 11, 24, 21), datetime(2026, 11, 25, 7, 30)),
        ("weekdays at 8", datetime(2026, 11, 25, 8), datetime(2026, 11, 26, 8)),
        ("weekends", datetime(2026, 11, 28, 9), datetime(2026, 11, 29, 9)),
        ("every mon,thu at 9am", datetime(2026, 11, 26, 9), datetime(2026, 11, 30, 9)),
        ("every monday and friday at 6pm", datetime(2026, 11, 27, 18), datetime(2026, 11, 30, 18)),
        ("every 2 hours", datetime(2026, 11, 24, 14), datetime(2026, 11, 24, 16)),
        ("every 90 minutes", datetime(2026, 11, 24, 13, 30), datetime(2026, 11, 24, 15)),
        ("every 3 days at noon", datetime(2026, 11, 27, 12), datetime(2026, 11, 30, 12)),
        ("weekly on fri at 5pm", datetime(2026, 11, 27, 17), datetime(2026, 12, 4, 17)),
        ("every other monday", datetime(2026, 12, 7, 9), datetime(2026, 12, 21, 9)),
        ("monthly on the 1st at 9am", datetime(2026, 12, 1, 9), datetime(2027, 1, 1, 9)),
        ("every month on the last day", datetime(2026, 11, 30, 9), datetime(2026, 12, 31, 9)),
        ("every 3 months on the 1st", datetime(2026, 12, 1, 9), datetime(2027, 3, 1, 9)),
        ("yearly on mar 14", datetime(2027, 3, 14, 9), datetime(2028, 3, 14, 9)),
        ("every day at 9am starting dec 1", datetime(2026, 12, 1, 9), datetime(2026, 12, 2, 9)),
    ],
)
def test_recurring(expr, first, second):
    s = parse_schedule(expr, NOW)
    assert s.recurring
    assert s.due == first
    assert next_occurrence(s.rule, s.due) == second


def test_today_slot_not_yet_passed_is_used():
    s = parse_schedule("every day at 6pm", NOW)
    assert s.due == datetime(2026, 11, 24, 18)


def test_dst_keeps_wall_clock_time():
    # US DST ends Nov 1 2026; Europe Oct 25. Naive wall-clock rules must stay at 9:00.
    s = parse_schedule("every day at 9am", datetime(2026, 10, 20, 12))
    t = s.due
    for _ in range(20):
        t = next_occurrence(s.rule, t)
        assert t.hour == 9


@pytest.mark.parametrize(
    ("expr", "due"),
    [
        ("in 20 minutes", datetime(2026, 11, 24, 12, 20)),
        ("in 2 hours", datetime(2026, 11, 24, 14)),
        ("tomorrow at 5pm", datetime(2026, 11, 25, 17)),
        ("at 6pm", datetime(2026, 11, 24, 18)),
        ("at 11am", datetime(2026, 11, 25, 11)),  # already passed today -> tomorrow
        ("friday at noon", datetime(2026, 11, 27, 12)),
        ("dec 3 at 2pm", datetime(2026, 12, 3, 14)),
    ],
)
def test_one_off(expr, due):
    s = parse_schedule(expr, NOW)
    assert not s.recurring
    assert s.due == due


@pytest.mark.parametrize(
    "expr", ["", "every blursday", "every 0 days", "every 2 hours at 9am", "gibberish xyz"]
)
def test_bad_schedules(expr):
    with pytest.raises(ScheduleError):
        parse_schedule(expr, NOW)


def test_labels_are_friendly():
    assert parse_schedule("every mon,thu at 9am", NOW).label == "Every Mon, Thu at 9:00 AM"
    assert parse_schedule("every 2 hours", NOW).label == "Every 2 hours"
    assert parse_schedule("monthly on the 1st", NOW).label == "Every month on the 1st at 9:00 AM"


@pytest.mark.parametrize(
    ("text", "title", "when"),
    [
        ("Water plants every mon,thu at 9am", "Water plants", "every mon,thu at 9am"),
        ("Call mom tomorrow at 6pm", "Call mom", "tomorrow at 6pm"),
        ("Stand up in 30 minutes", "Stand up", "in 30 minutes"),
        ("Take meds daily at 8am", "Take meds", "daily at 8am"),
        ("Turn on lights every day at 7pm", "Turn on lights", "every day at 7pm"),
        ("Look at budget in 2 days", "Look at budget", "in 2 days"),
        ("Just a title", "Just a title", ""),
    ],
)
def test_split_title(text, title, when):
    assert split_title_and_schedule(text, NOW) == (title, when)


def test_inline_meta():
    q = extract_inline_meta("Pay rent #Bills #home !high")
    assert q.title == "Pay rent"
    assert q.tags == ["bills", "home"]
    assert q.priority == 2
