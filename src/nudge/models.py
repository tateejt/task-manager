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


class Case(SQLModel, table=True):
    """A support case (or ticket) that scratchpad entries are grouped under.

    ``template`` is reserved for case templates (a named checklist of fields to fill in).
    """

    id: int | None = Field(default=None, primary_key=True)
    ref: str = Field(index=True, unique=True)  # "48213", "INC-0042"
    title: str = ""
    template: str | None = None
    created_at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))
    updated_at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))


class Entry(SQLModel, table=True):
    """One scratchpad line: a labeled value ("INB exported" = "1,240") or a plain note (no label)."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: int | None = Field(default=None, index=True, foreign_key="case.id", ondelete="CASCADE")
    label: str = ""
    value: str = ""
    raw: str = ""  # exactly what was typed
    created_at: NaiveDatetime = Field(
        default_factory=lambda: datetime.now().replace(microsecond=0), index=True
    )

    @property
    def text(self) -> str:
        return f"{self.label}: {self.value}" if self.label else self.value


class Template(SQLModel, table=True):
    """A reusable list of fields (labels) to start a scratchpad page from, e.g. INB exported/imported/returned."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    fields: str = ""  # one label per line, in order
    created_at: NaiveDatetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))

    @property
    def field_list(self) -> list[str]:
        return [f for f in self.fields.splitlines() if f.strip()]
