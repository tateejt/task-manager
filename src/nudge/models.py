"""Database models (SQLite via SQLModel)."""

from __future__ import annotations

from datetime import datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Reminder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    notes: str = ""
    #: iCalendar recurrence text (DTSTART + RRULE lines); None for one-off reminders.
    rule: str | None = None
    #: The schedule as the user typed it (used for export and editing).
    when_text: str = ""
    #: Human-readable schedule, e.g. "Every Mon, Thu at 9:00 AM".
    schedule_label: str = ""
    #: The occurrence currently waiting to be done (local wall-clock time).
    due_at: NaiveDatetime = Field(index=True)
    snoozed_until: NaiveDatetime | None = None
    last_notified_at: NaiveDatetime | None = None
    notify_count: int = 0
    priority: int = 1  # 0 low, 1 normal, 2 high, 3 urgent
    tags: str = ""  # comma separated, lower-case
    active: bool = True  # paused reminders are inactive
    completed_at: NaiveDatetime | None = None  # set when a one-off is done
    streak: int = 0
    best_streak: int = 0
    created_at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))

    @property
    def recurring(self) -> bool:
        return self.rule is not None

    @property
    def tag_list(self) -> list[str]:
        return [t for t in self.tags.split(",") if t]

    @property
    def effective_due(self) -> datetime:
        """When this reminder should next alert (respecting snooze)."""
        return self.snoozed_until or self.due_at

    @property
    def is_open(self) -> bool:
        return self.active and self.completed_at is None


class Completion(SQLModel, table=True):
    """History of what happened to each occurrence."""

    id: int | None = Field(default=None, primary_key=True)
    reminder_id: int = Field(index=True, foreign_key="reminder.id", ondelete="CASCADE")
    due_at: NaiveDatetime
    at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))
    status: str = "done"  # done | skipped | missed


class UndoEntry(SQLModel, table=True):
    """A snapshot taken before an action so it can be undone."""

    id: int | None = Field(default=None, primary_key=True)
    reminder_id: int = Field(index=True, foreign_key="reminder.id", ondelete="CASCADE")
    action: str  # done | skip | snooze | reset | pause | resume | edit
    at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))
    #: JSON of the reminder's fields before the action.
    state: str
    #: History row created by the action, removed again on undo.
    completion_id: int | None = None
