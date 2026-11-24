import queue
import sys
from datetime import datetime, timedelta

import pytest

from nudge.notify import (
    Action,
    asset_path,
    build_spec,
    decode_action,
    emoji_for,
    snooze_choices,
)

from .conftest import NOW


def test_snooze_dropdown_minutes_come_from_inputs():
    assert decode_action("snooze:4", {"snooze": "30"}) == Action("snooze", 4, 30)
    assert decode_action("snooze:4:60", {"snooze": "30"}) == Action("snooze", 4, 60)
    assert decode_action("snooze:4", {"snooze": "junk"}) == Action("snooze", 4, None)


def test_emoji_for_titles():
    assert emoji_for("Water plants") == "💧"
    assert emoji_for("Take vitamins") == "💊"
    assert emoji_for("Pay rent") == "💳"
    assert emoji_for("Something else") == "⏰"


def test_spec_due_now(svc):
    r = svc.add("Water plants #home", "every day at 9am", now=NOW)
    spec = build_spec(r, nag=False, snooze_minutes=10, now=r.due_at)
    assert spec.title.endswith("Water plants")
    assert spec.status == "Due now · 9:00 AM"
    assert spec.context == "Every day at 9:00 AM"
    assert spec.attribution == "Nudge · #home"
    assert spec.logo == "nudge"
    assert not spec.persistent


def test_spec_nag_and_streak(svc):
    r = svc.add("Walk", "every day at 9am", now=NOW)
    for _ in range(3):
        r = svc.complete(r.id, now=r.due_at)
    spec = build_spec(r, nag=True, snooze_minutes=10, now=r.due_at + timedelta(minutes=25))
    assert spec.status == "Still waiting · overdue 25 min"
    assert "🔥 3 in a row" in spec.context
    assert spec.logo == "nudge-nag"
    assert spec.persistent and spec.loud


def test_spec_urgent_notes_and_snoozed(svc):
    r = svc.add("Pay rent !urgent", "every day at 9am", notes="Transfer to landlord", now=NOW)
    r = svc.snooze(r.id, 10, now=r.due_at)
    spec = build_spec(r, nag=False, snooze_minutes=10, now=r.snoozed_until)
    assert spec.status.startswith("Urgent · Snoozed reminder · was due 9:00 AM")
    assert spec.context == "Transfer to landlord"
    assert spec.logo == "nudge-urgent"


def test_snooze_choices_include_tomorrow_morning():
    choices = snooze_choices(datetime(2026, 11, 24, 21, 0))
    assert choices[0] == (10, "10 minutes")
    assert choices[-1] == (12 * 60, "Tomorrow at 9 AM")


def test_assets_are_packaged():
    for name in ("nudge.ico", "nudge.png", "nudge-nag.png", "nudge-urgent.png"):
        assert asset_path(name).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows toasts only")
def test_windows_toast_builds(svc):  # pragma: no cover - Windows CI
    from nudge.notify import WindowsToastNotifier

    r = svc.add("Water plants #home !high", "every day at 9am", now=NOW)
    notifier = WindowsToastNotifier.__new__(WindowsToastNotifier)
    notifier.actions = queue.Queue()
    notifier.app_wide = False
    toast = notifier.build_toast(r, nag=True, snooze_minutes=10, sound=True)
    assert toast.text_fields[0].endswith("Water plants")
    assert [a.content for a in toast.actions] == ["✓  Done", "Snooze", "Skip"]
    assert len(toast.inputs) == 1
