"""Shared formatting helpers for the CLI and TUI."""

from __future__ import annotations

from datetime import datetime

from rich.table import Table
from rich.text import Text

from .models import Reminder
from .schedule import PRIORITY_LABEL

PRIORITY_STYLE = {0: "dim", 1: "", 2: "bold yellow", 3: "bold red"}
PRIORITY_ICON = {0: "·", 1: " ", 2: "!", 3: "‼"}


def relative(moment: datetime, now: datetime) -> str:
    """'in 5m', 'in 3h 10m', 'tomorrow 09:00', 'overdue 12m'."""
    delta = moment - now
    secs = int(delta.total_seconds())
    past = secs < 0
    secs = abs(secs)
    if secs < 60:
        core = "now" if not past else "just now"
        return core
    mins, hours, days = secs // 60, secs // 3600, secs // 86400
    if mins < 60:
        core = f"{mins}m"
    elif hours < 24:
        core = f"{hours}h {mins % 60}m" if mins % 60 and hours < 6 else f"{hours}h"
    else:
        core = f"{days}d"
    if past:
        return f"overdue {core}"
    if moment.date() == now.date():
        return f"in {core} ({moment:%H:%M})"
    if (moment.date() - now.date()).days == 1:
        return f"tomorrow {moment:%H:%M}"
    if days < 6:
        return f"{moment:%a %H:%M}"
    return f"{moment:%b %d %H:%M}"


def due_text(r: Reminder, now: datetime) -> Text:
    when = r.effective_due
    label = relative(when, now)
    if r.snoozed_until:
        label = f"💤 {label}"
    if not r.active:
        return Text("paused", style="dim italic")
    if r.completed_at:
        return Text("done", style="green")
    style = "bold red" if when <= now else ("yellow" if (when - now).total_seconds() < 3600 else "")
    return Text(label, style=style)


def reminder_table(reminders: list[Reminder], now: datetime, title: str | None = None) -> Table:
    t = Table(title=title, header_style="bold cyan", border_style="grey37", expand=False, pad_edge=False)
    t.add_column("#", justify="right", style="dim")
    t.add_column("", width=1)
    t.add_column("Reminder", min_width=18)
    t.add_column("Next", no_wrap=True)
    t.add_column("Schedule", style="dim")
    t.add_column("Tags", style="magenta")
    t.add_column("🔥", justify="right")
    for r in reminders:
        t.add_row(
            str(r.id),
            Text(PRIORITY_ICON.get(r.priority, " "), style=PRIORITY_STYLE.get(r.priority, "")),
            Text(r.title, style=PRIORITY_STYLE.get(r.priority, "")),
            due_text(r, now),
            r.schedule_label,
            " ".join(f"#{x}" for x in r.tag_list),
            str(r.streak) if r.streak else "",
        )
    return t


def priority_label(p: int) -> str:
    return PRIORITY_LABEL.get(p, "normal")
