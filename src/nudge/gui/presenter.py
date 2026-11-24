"""Pure helpers that turn reminders into what the desktop window shows (no Flet imports, easy to test)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import Reminder
from ..notify import emoji_for
from ..schedule import PRIORITY_LABEL
from ..ui import relative

#: Accent colours per state, shared by cards, chips and the tray.
STATE_COLOURS = {
    "overdue": "#E11D48",  # rose
    "soon": "#F59E0B",  # amber, due within the hour
    "later": "#6366F1",  # indigo
    "snoozed": "#0EA5E9",  # sky
    "paused": "#94A3B8",  # slate
    "done": "#16A34A",  # green
}
PRIORITY_COLOURS = {0: "#94A3B8", 1: "#6366F1", 2: "#F59E0B", 3: "#E11D48"}


@dataclass
class Card:
    id: int
    emoji: str
    title: str
    schedule: str
    when: str
    when_exact: str
    state: str
    colour: str
    priority: int
    priority_label: str
    tags: list[str]
    streak: int
    notes: str
    recurring: bool
    active: bool


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
    when = re.sub(r" \(\d\d:\d\d\)$", "", when)  # the exact time is shown underneath
    if when == "now":
        when = "In under a minute"
    if state == "overdue":
        when = "Due now" if now - due < timedelta(minutes=1) else when.replace("overdue", "Overdue")
    if state == "snoozed":
        when = f"Snoozed · {when}"
    return Card(
        id=r.id,
        emoji=emoji_for(r.title),
        title=r.title,
        schedule=r.schedule_label,
        when=when[:1].upper() + when[1:],
        when_exact=due.strftime("%a %b %d, %I:%M %p").replace(" 0", " "),
        state=state,
        colour=STATE_COLOURS[state],
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


def all_tags(reminders: list[Reminder]) -> list[str]:
    return sorted({t for r in reminders for t in r.tag_list})


def undo_message(action: str, title: str) -> str:
    verbs = {"done": "Marked done", "skip": "Skipped", "snooze": "Snoozed", "reset": "Reset",
             "pause": "Paused", "resume": "Resumed", "edit": "Updated"}  # fmt: skip
    return f"{verbs.get(action, action.capitalize())}: {title}"
