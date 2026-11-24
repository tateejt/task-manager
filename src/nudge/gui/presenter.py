"""Pure helpers that turn reminders into what the desktop window shows (no Flet imports, easy to test)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Reminder
from ..notify import emoji_for
from ..schedule import (
    PRIORITY_LABEL,
    ScheduleError,
    explain,
    extract_inline_meta,
    split_title_and_schedule,
)
from ..ui import relative

#: Which palette colour each state uses (see ``gui.theme``).
STATE_TONE = {
    "overdue": "red",
    "soon": "amber",
    "later": "accent",
    "snoozed": "sky",
    "paused": "faint",
    "done": "green",
}


@dataclass
class Card:
    id: int
    emoji: str
    title: str
    schedule: str
    when: str
    when_exact: str
    right: str  # the short, right-aligned time ("6:00 PM", "in 25m", "12m late")
    state: str
    tone: str
    priority: int
    priority_label: str
    tags: list[str]
    streak: int
    notes: str
    recurring: bool
    active: bool


def clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def _span(delta: timedelta) -> str:
    mins = max(1, int(delta.total_seconds() // 60))
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h {mins % 60}m" if mins % 60 and hours < 3 else f"{hours}h"
    return f"{hours // 24}d"


def short_time(due: datetime, now: datetime) -> str:
    """Compact, scannable time for the right-hand column."""
    if due <= now:
        return "now" if now - due < timedelta(minutes=1) else f"{_span(now - due)} late"
    if due - now <= timedelta(hours=1):
        return f"in {_span(due - now)}"
    days = (due.date() - now.date()).days
    if days == 0:
        return clock(due)
    if days == 1:
        return f"Tmrw {clock(due)}"
    if days < 7:
        return f"{due:%a} {clock(due)}"
    return f"{due:%b} {due.day}"


def greeting(now: datetime) -> str:
    if now.hour < 5:
        return "Working late"
    if now.hour < 12:
        return "Good morning"
    if now.hour < 18:
        return "Good afternoon"
    return "Good evening"


def card_for(r: Reminder, now: datetime) -> Card:
    due = r.effective_due
    if not r.active:
        state = "paused"
    elif r.completed_at:
        state = "done"
    elif due <= now:
        state = "overdue"
    elif r.snoozed_until:
        state = "snoozed"
    elif due - now <= timedelta(hours=1):
        state = "soon"
    else:
        state = "later"
    when = {"paused": "Paused", "done": "Finished"}.get(state) or relative(due, now)
    when = re.sub(r" \(\d\d:\d\d\)$", "", when)
    if when == "now":
        when = "In under a minute"
    if state == "overdue":
        when = "Due now" if now - due < timedelta(minutes=1) else when.replace("overdue", "Overdue")
    if state == "snoozed":
        when = f"Snoozed · {when}"
    right = {"paused": "paused", "done": "done"}.get(state) or short_time(due, now)
    return Card(
        id=r.id,
        emoji=emoji_for(r.title),
        title=r.title,
        schedule=r.schedule_label,
        when=when[:1].upper() + when[1:],
        when_exact=due.strftime("%a %b %d, %I:%M %p").replace(" 0", " "),
        right=right,
        state=state,
        tone=STATE_TONE[state],
        priority=r.priority,
        priority_label=PRIORITY_LABEL.get(r.priority, "normal"),
        tags=r.tag_list,
        streak=r.streak,
        notes=r.notes,
        recurring=r.recurring,
        active=r.active,
    )


SECTION_ORDER = ["Due now", "Later today", "Tomorrow", "This week", "Later", "Paused", "Finished"]


def section_for(card_state: str, due: datetime, now: datetime) -> str:
    if card_state == "paused":
        return "Paused"
    if card_state == "done":
        return "Finished"
    if due <= now:
        return "Due now"
    days = (due.date() - now.date()).days
    if days == 0:
        return "Later today"
    if days == 1:
        return "Tomorrow"
    if days < 7:
        return "This week"
    return "Later"


def matches(r: Reminder, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    if q.startswith("#"):
        return q[1:] in r.tag_list
    return q in r.title.lower() or q in r.notes.lower() or any(q in t for t in r.tag_list)


def sections(reminders: list[Reminder], now: datetime, query: str = "") -> list[tuple[str, list[Card]]]:
    """Group reminders into Due now / Later today / Tomorrow / ... in display order."""
    groups: dict[str, list[Card]] = {}
    for r in sorted(reminders, key=lambda x: (x.effective_due, -x.priority)):
        if not matches(r, query):
            continue
        card = card_for(r, now)
        groups.setdefault(section_for(card.state, r.effective_due, now), []).append(card)
    return [(name, groups[name]) for name in SECTION_ORDER if name in groups]


def today(reminders: list[Reminder], now: datetime, query: str = "") -> list[Card]:
    """Open reminders that are due now or later today, soonest first."""
    end = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    picked = [
        r
        for r in reminders
        if r.active and not r.completed_at and r.effective_due < end and matches(r, query)
    ]
    return [card_for(r, now) for r in sorted(picked, key=lambda x: (x.effective_due, -x.priority))]


def next_up(reminders: list[Reminder], now: datetime, count: int = 3) -> list[Card]:
    """The next few open reminders after today (shown when today is clear)."""
    end = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    later = sorted(
        (r for r in reminders if r.active and not r.completed_at and r.effective_due >= end),
        key=lambda x: x.effective_due,
    )
    return [card_for(r, now) for r in later[:count]]


def all_tags(reminders: list[Reminder]) -> list[str]:
    return sorted({t for r in reminders for t in r.tag_list})


def undo_message(action: str, title: str) -> str:
    verbs = {"done": "Done", "skip": "Skipped", "snooze": "Snoozed", "reset": "Back on schedule",
             "pause": "Paused", "resume": "Resumed", "edit": "Updated", "add": "Added"}  # fmt: skip
    return f"{verbs.get(action, action.capitalize())}: {title}"


# --------------------------------------------------------------------------- the smart input line


@dataclass
class Parsed:
    """What the top input line means right now, shown live on the right like a calculator result."""

    mode: str  # empty | search | add | incomplete | error
    title: str = ""
    when: str = ""
    label: str = ""  # "Every Mon at 9:00 AM"
    detail: str = ""  # "next Mon Nov 30, 9:00 AM" or the error / hint
    tags: list[str] = field(default_factory=list)
    priority: int | None = None
    query: str = ""

    @property
    def ok(self) -> bool:
        return self.mode == "add"


def _when(t: datetime, now: datetime) -> str:
    days = (t.date() - now.date()).days
    if days == 0:
        return f"today {clock(t)}"
    if days == 1:
        return f"tomorrow {clock(t)}"
    if days < 7:
        return f"{t:%a} {clock(t)}"
    return f"{t:%a %b} {t.day}, {clock(t)}"


def parse_input(text: str, now: datetime) -> Parsed:
    """Read the input line: ``/word`` or a lone ``#tag`` searches, anything else is a new reminder."""
    raw = text.strip()
    if not raw:
        return Parsed("empty")
    if raw.startswith("/"):
        return Parsed("search", query=raw[1:].strip(), detail="Searching")
    if re.fullmatch(r"#[\w-]+", raw):
        return Parsed("search", query=raw.lower(), detail=f"Showing {raw.lower()}")
    meta = extract_inline_meta(raw)
    title, when = split_title_and_schedule(meta.title, now)
    base = Parsed("incomplete", title=title, tags=meta.tags, priority=meta.priority)
    if not title:
        base.detail = "What should I remind you about?"
        return base
    if not when:
        base.detail = "add when · every day at 9am · in 20 min"
        return base
    try:
        sched, times = explain(when, now, 2)
    except (ScheduleError, ValueError) as e:
        base.mode, base.when, base.detail = "error", when, str(e).split(". ")[0].rstrip(".")
        return base
    base.mode, base.when, base.label = "add", when, sched.label
    base.detail = "next " + _when(times[0], now)
    return base
