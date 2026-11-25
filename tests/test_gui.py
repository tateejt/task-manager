from datetime import datetime, timedelta

import pytest

from nudge.gui.presenter import (
    card_for,
    greeting,
    matches,
    next_up,
    parse_input,
    sections,
    short_time,
    today,
    undo_message,
)

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
    assert (due.id, due.state, due.when, due.right, due.tone) == (
        overdue.id,
        "overdue",
        "Overdue 25m",
        "25m late",
        "red",
    )
    assert groups[1][1][0].id == later.id and groups[1][1][0].right == "6:00 PM"
    assert groups[2][1][0].id == tomorrow.id and groups[2][1][0].right == "Tmrw 7:00 AM"
    assert groups[3][1][0].right == "paused"
    assert [c.id for c in today(svc.list(include_inactive=True), now)] == [overdue.id, later.id]
    assert [c.id for c in next_up(svc.list(), now)] == [tomorrow.id]


def test_short_time():
    assert short_time(NOW + timedelta(minutes=25), NOW) == "in 25m"
    assert short_time(NOW + timedelta(days=3), NOW) == "Fri 12:00 PM"
    assert short_time(NOW + timedelta(days=20), NOW) == "Dec 14"
    assert short_time(NOW - timedelta(hours=2), NOW) == "2h late"
    assert short_time(NOW, NOW) == "now"


def test_parse_input():
    p = parse_input("Water plants #home !high every mon,thu at 9am", NOW)
    assert p.ok and p.title == "Water plants" and p.tags == ["home"] and p.priority == 2
    assert p.label == "Every Mon, Thu at 9:00 AM" and p.detail == "next Thu 9:00 AM"
    assert parse_input("Call mom tomorrow at 6pm", NOW).detail == "next tomorrow 6:00 PM"
    assert parse_input("Water plants", NOW).mode == "incomplete"
    assert parse_input("Plants every blursday", NOW).mode == "error"
    assert parse_input("", NOW).mode == "empty"
    s = parse_input("/plant", NOW)
    assert s.mode == "search" and s.query == "plant"
    assert parse_input("#Home", NOW).query == "#home"


def test_search_and_tags(svc):
    a = svc.add("Water plants #home", "at 6pm", now=NOW)
    svc.add("Gym", "at 7pm", now=NOW)
    assert matches(a, "#home") and matches(a, "plants") and not matches(a, "#work")
    assert [c.id for _, cards in sections(svc.list(), NOW, "#home") for c in cards] == [a.id]


def test_snoozed_card(svc):
    r = svc.add("Stretch", "in 5 minutes", now=NOW)
    r = svc.snooze(r.id, 30, now=NOW + timedelta(minutes=5))
    c = card_for(r, NOW + timedelta(minutes=6))
    assert c.state == "snoozed" and c.when.startswith("Snoozed") and c.tone == "sky"


def test_undo_message():
    assert undo_message("done", "Walk") == "Done: Walk"


def test_theme_pick():
    from nudge.gui.theme import DARK, LIGHT, pick

    assert pick("system", None) is DARK and pick("system", False) is LIGHT and pick("light", True) is LIGHT


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
        self.platform_brightness = None
        self.controls = []
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
    w.build()
    return w


def test_every_view_builds(window):
    for tab in ("today", "upcoming", "all", "history"):
        window.set_tab(tab)
        assert window.body.controls
    window.toggle_settings()
    assert window.settings_open and window.body.controls
    window.toggle_settings()
    window.page.platform_brightness = ft.Brightness.LIGHT
    window.build()
    assert window.p.name == "light"


def test_smart_input_adds(window, svc):
    window.input.value = "Stretch every 2 hours #health"
    window._on_input()
    assert window.result.value == "Every 2 hours" and "#health" in window.detail.value
    window.submit()
    r = next(r for r in svc.list() if r.title == "Stretch")
    assert r.tag_list == ["health"] and window.input.value == ""
    assert window.last_flash.startswith("Added Stretch")

    window.input.value = "Plants every blursday"
    window._on_input()
    window.submit()
    assert window.result.value == "?" and not any(r.title == "Plants" for r in svc.list())

    window.input.value = "#home"
    window._on_input()
    assert window.query == "#home"
    window.set_tab("all")
    titles = [c for c in window.body.controls]
    assert len(titles) == 2  # one section label + one row


def test_actions_and_undo(window, svc):
    rid = svc.list()[0].id
    window.act(rid, "done")
    assert svc.get(rid).streak == 1 and window.last_flash.startswith("Done")
    window.act(rid, "done")  # second press is refused, not stacked
    assert svc.get(rid).streak == 1
    window.undo()
    assert svc.get(rid).streak == 0
    window.act(rid, "snooze", "tomorrow")
    assert svc.get(rid).snoozed_until is not None


def test_inline_editor(window, svc):
    r = next(r for r in svc.list() if r.title == "Water plants")
    window.set_tab("all")
    window.toggle_editor(r.id)
    ed = window.editor
    assert ed is not None and ed.control in window.body.controls
    ed.when.value = "every blursday"
    assert not ed.check() and ed.save_btn.disabled
    ed.when.value = "weekdays at 7"
    ed.notes.value = "Big ones only"
    ed.set_priority(3)
    assert ed.save()
    r = svc.get(r.id)
    assert (r.schedule_label, r.notes, r.priority) == ("Weekdays at 7:00 AM", "Big ones only", 3)
    assert window.editor is None

    window.toggle_editor(r.id)
    window.editor.delete()  # first click asks
    assert svc.get(r.id)
    window.editor.delete()
    assert all(x.id != r.id for x in svc.list(include_inactive=True))


def test_notes_tab(window, svc):
    window.set_tab("notes")
    assert window.input.hint_text == "Jot a value…" and window.body.controls
    window.input.value = "case 48213"
    window._on_input()
    assert window.result.value == "case 48213"
    window.submit()
    assert window.case_ref == "48213"

    window.input.value = "INB exported 1,240"
    window._on_input()
    assert window.result.value == "INB exported = 1,240" and "case 48213" in window.detail.value
    window.submit()
    window.input.value = "INB returned 42"
    window._on_input()
    window.submit()
    assert [e.value for e in window.pad.entries(case="48213")] == ["42", "1,240"]

    window.copy_current()
    assert window.last_copied.splitlines()[1:] == ["INB exported: 1,240", "INB returned: 42"]

    e = window.pad.entries(case="48213")[0]
    window.delete_entry(e)
    assert len(window.pad.entries(case="48213")) == 1

    window.close_case()
    window.input.value = "/482"
    window._on_input()
    assert window.query == "482" and window.body.controls

    window.open_pad()  # no real window in tests: just switches to the Notes tab
    assert window.pad_mode and window.tab == "notes"
    window.set_tab("today")
    assert not window.pad_mode and window.input.hint_text == "Remind me to…"


def test_notes_blank_page_and_templates(window):
    window.set_tab("notes")
    assert window.case_ref is None  # starts blank
    for line in ["INB exported 1,240", "INB imported 1,198", "INB returned 42"]:
        window.input.value = line
        window._on_input()
        window.submit()
    page = window.case_ref
    assert page.startswith("note-")
    window.input.value = "save template INB"
    window._on_input()
    assert window.result.value == "save template INB" and "3 fields" in window.detail.value
    window.submit()
    assert [t.name for t in window.pad.templates()] == ["INB"]

    window.new_page()
    assert window.case_ref is None
    window.use_template("INB")
    assert window.case_ref != page
    window.input.value = "980"
    window._on_input()
    assert window.result.value == "INB exported = 980"
    window.submit()
    window.copy_current()
    assert window.last_copied.splitlines()[1:] == ["INB exported: 980"]
