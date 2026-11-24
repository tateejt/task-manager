"""Core reminder logic. Every time-dependent method takes ``now`` so it is easy to test."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import event
from sqlmodel import Session, SQLModel, col, create_engine, select

from .config import Settings, db_path
from .models import Completion, Reminder, UndoEntry
from .schedule import (
    ScheduleError,
    extract_inline_meta,
    latest_occurrence,
    next_occurrence,
    occurrences_between,
    parse_priority,
    parse_schedule,
    split_title_and_schedule,
)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class NotFound(LookupError):
    pass


class AlreadyDone(ValueError):
    """Raised when Done/Skip is pressed again for an occurrence that's already handled."""

    def __init__(self, reminder: Reminder):
        self.reminder = reminder
        super().__init__(
            f"'{reminder.title}' is already done for now. The next one is "
            f"{reminder.due_at:%a %b %d %H:%M}. Use undo if that was a mistake."
        )


class NothingToUndo(LookupError):
    pass


#: Fields an action can change, and therefore what undo restores.
_UNDO_FIELDS = (
    "due_at", "snoozed_until", "last_notified_at", "notify_count", "active", "completed_at",
    "streak", "best_streak", "title", "notes", "rule", "when_text", "schedule_label", "priority", "tags",
)  # fmt: skip
_DT_FIELDS = {"due_at", "snoozed_until", "last_notified_at", "completed_at"}


def _snapshot(r: Reminder) -> str:
    data = {}
    for f in _UNDO_FIELDS:
        v = getattr(r, f)
        data[f] = v.isoformat() if isinstance(v, datetime) else v
    return json.dumps(data)


def _restore(r: Reminder, state: str) -> None:
    for f, v in json.loads(state).items():
        setattr(r, f, datetime.fromisoformat(v) if f in _DT_FIELDS and v else v)


@dataclass
class Stats:
    total: int
    active: int
    overdue: int
    due_today: int
    done_7d: int
    missed_7d: int
    best: list[tuple[str, int]]


class ReminderService:
    def __init__(self, path: Path | str | None = None):
        url = "sqlite://" if path == ":memory:" else f"sqlite:///{path or db_path()}"
        self.engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        SQLModel.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # ------------------------------------------------------------------ create / edit

    def add(
        self,
        title: str,
        when: str = "",
        *,
        notes: str = "",
        tags: Iterable[str] = (),
        priority: str | int | None = None,
        now: datetime | None = None,
    ) -> Reminder:
        """Add a reminder. ``title`` may contain the schedule too ("Stretch every 2 hours")."""
        now = now or _now()
        if not when:
            title, when = split_title_and_schedule(title, now)
        meta = extract_inline_meta(title)
        if not meta.title:
            raise ScheduleError("The reminder needs a title.")
        if not when:
            raise ScheduleError(
                "When should I remind you? e.g. 'every day at 9am', 'weekdays at 8', 'in 30 minutes'."
            )
        sched = parse_schedule(when, now)
        all_tags = sorted({t.strip().lower().lstrip("#") for t in [*tags, *meta.tags] if t.strip()})
        prio = (
            parse_priority(priority)
            if priority is not None
            else (meta.priority if meta.priority is not None else 1)
        )
        r = Reminder(
            title=meta.title,
            notes=notes,
            rule=sched.rule,
            when_text=when.strip(),
            schedule_label=sched.label,
            due_at=sched.due,
            priority=prio,
            tags=",".join(all_tags),
            created_at=now,
        )
        with self.session() as s:
            s.add(r)
            s.commit()
            s.refresh(r)
        return r

    def get(self, rid: int) -> Reminder:
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            return r

    def _save(self, r: Reminder) -> Reminder:
        with self.session() as s:
            r = s.merge(r)
            s.commit()
            s.refresh(r)
            return r

    def edit(
        self,
        rid: int,
        *,
        title: str | None = None,
        when: str | None = None,
        notes: str | None = None,
        tags: Iterable[str] | None = None,
        priority: str | int | None = None,
        now: datetime | None = None,
    ) -> Reminder:
        now = now or _now()
        r = self.get(rid)
        with self.session() as s:
            self._record(s, r, "edit", now)
            s.commit()
        if title:
            r.title = title
        if notes is not None:
            r.notes = notes
        if tags is not None:
            r.tags = ",".join(sorted({t.strip().lower().lstrip("#") for t in tags if t.strip()}))
        if priority is not None:
            r.priority = parse_priority(priority)
        if when:
            sched = parse_schedule(when, now)
            r.rule, r.schedule_label, r.due_at = sched.rule, sched.label, sched.due
            r.when_text = when.strip()
            r.snoozed_until = r.last_notified_at = r.completed_at = None
            r.notify_count = 0
        return self._save(r)

    def delete(self, rid: int) -> Reminder:
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            s.exec(sa_delete(Completion).where(Completion.reminder_id == rid))
            s.exec(sa_delete(UndoEntry).where(UndoEntry.reminder_id == rid))
            s.delete(r)
            s.commit()
            return r

    def set_active(self, rid: int, active: bool, now: datetime | None = None) -> Reminder:
        now = now or _now()
        r = self.get(rid)
        with self.session() as s:
            self._record(s, r, "resume" if active else "pause", now)
            s.commit()
        r.active = active
        if active and r.recurring and r.due_at < now:
            # Resuming after a break shouldn't flood you with old occurrences.
            r.due_at = next_occurrence(r.rule, now) or r.due_at
            r.snoozed_until = r.last_notified_at = None
            r.notify_count = 0
        return self._save(r)

    # ------------------------------------------------------------------ queries

    def list(
        self, *, include_inactive: bool = False, include_done: bool = False, tag: str | None = None
    ) -> list[Reminder]:
        with self.session() as s:
            q = select(Reminder)
            if not include_inactive:
                q = q.where(Reminder.active == True)  # noqa: E712
            if not include_done:
                q = q.where(col(Reminder.completed_at).is_(None))
            rows = list(s.exec(q))
        if tag:
            tag = tag.lower().lstrip("#")
            rows = [r for r in rows if tag in r.tag_list]
        return sorted(rows, key=lambda r: (r.effective_due, -r.priority))

    def search(self, text: str) -> list[Reminder]:
        text = text.lower()
        return [
            r for r in self.list(include_inactive=True) if text in r.title.lower() or text in r.notes.lower()
        ]

    def agenda(self, start: datetime, end: datetime) -> list[tuple[datetime, Reminder]]:
        """Every occurrence between ``start`` and ``end`` for open reminders."""
        out: list[tuple[datetime, Reminder]] = []
        for r in self.list():
            if r.due_at < start:
                out.append((r.effective_due, r))  # still open from before the window
            if r.rule:
                times = [t for t in occurrences_between(r.rule, max(start, r.due_at), end)]
                if r.snoozed_until and times and times[0] == r.due_at:
                    times[0] = r.snoozed_until
                out += [(t, r) for t in times]
            elif start <= r.due_at <= end:
                out.append((r.effective_due, r))
        return sorted(out, key=lambda x: (x[0], -x[1].priority))

    def history(self, rid: int | None = None, limit: int = 50) -> list[tuple[Completion, Reminder]]:
        with self.session() as s:
            q = select(Completion, Reminder).join(Reminder).order_by(col(Completion.at).desc()).limit(limit)
            if rid is not None:
                q = q.where(Completion.reminder_id == rid)
            return list(s.exec(q))

    def stats(self, now: datetime | None = None) -> Stats:
        now = now or _now()
        all_r = self.list(include_inactive=True, include_done=True)
        open_r = [r for r in all_r if r.is_open]
        end_of_day = now.replace(hour=23, minute=59, second=59)
        week_ago = now - timedelta(days=7)
        with self.session() as s:
            recent = list(s.exec(select(Completion).where(Completion.at >= week_ago)))
        best = sorted(((r.title, r.best_streak) for r in all_r if r.best_streak), key=lambda x: -x[1])[:5]
        return Stats(
            total=len(all_r),
            active=len(open_r),
            overdue=sum(1 for r in open_r if r.effective_due <= now),
            due_today=sum(1 for r in open_r if r.effective_due <= end_of_day),
            done_7d=sum(1 for c in recent if c.status == "done"),
            missed_7d=sum(1 for c in recent if c.status == "missed"),
            best=best,
        )

    # ------------------------------------------------------------------ actions

    def _log(self, s: Session, r: Reminder, status: str, now: datetime) -> None:
        s.add(Completion(reminder_id=r.id, due_at=r.due_at, at=now, status=status))

    def _advance(self, r: Reminder, now: datetime) -> None:
        """Move a reminder to its next occurrence (or finish a one-off)."""
        r.snoozed_until = r.last_notified_at = None
        r.notify_count = 0
        if r.rule:
            nxt = next_occurrence(r.rule, max(r.due_at, now))
            if nxt is None:
                r.completed_at = now
            else:
                r.due_at = nxt
        else:
            r.completed_at = now

    def _record(self, s: Session, r: Reminder, action: str, now: datetime, completion_id=None) -> None:
        s.add(
            UndoEntry(
                reminder_id=r.id, action=action, at=now, state=_snapshot(r), completion_id=completion_id
            )
        )

    def _already_handled(self, s: Session, r: Reminder, now: datetime) -> bool:
        """True when the current occurrence was already done/skipped and the next isn't due yet.

        Completing *early* once is fine (e.g. ticking off tonight's task this afternoon), but a
        second press would silently eat the following occurrence too, so it's refused.
        """
        if not r.rule or r.due_at <= now:
            return False
        last = s.exec(
            select(Completion)
            .where(Completion.reminder_id == r.id, col(Completion.status).in_(["done", "skipped"]))
            .order_by(col(Completion.id).desc())
        ).first()
        if last is None:
            return False
        started = latest_occurrence(r.rule, now)
        return started is None or last.at >= started

    def _finish_occurrence(self, rid: int, status: str, now: datetime | None, force: bool) -> Reminder:
        now = now or _now()
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            if r.completed_at:
                return r
            if not force and self._already_handled(s, r, now):
                raise AlreadyDone(r)
            before = _snapshot(r)
            c = Completion(reminder_id=r.id, due_at=r.due_at, at=now, status=status)
            s.add(c)
            s.flush()
            s.add(UndoEntry(reminder_id=r.id, action="done" if status == "done" else "skip", at=now,
                            state=before, completion_id=c.id))  # fmt: skip
            if status == "done":
                r.streak += 1
                r.best_streak = max(r.best_streak, r.streak)
            self._advance(r, now)
            s.add(r)
            s.commit()
            s.refresh(r)
            return r

    def complete(self, rid: int, now: datetime | None = None, force: bool = False) -> Reminder:
        """Mark the current occurrence done. ``force`` allows completing further ahead."""
        return self._finish_occurrence(rid, "done", now, force)

    def skip(self, rid: int, now: datetime | None = None, force: bool = False) -> Reminder:
        """Skip this occurrence without breaking the streak."""
        return self._finish_occurrence(rid, "skipped", now, force)

    def _mutate(self, rid: int, action: str, now: datetime, fn) -> Reminder:
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            self._record(s, r, action, now)
            fn(r)
            s.add(r)
            s.commit()
            s.refresh(r)
            return r

    def snooze(self, rid: int, minutes: int | None = None, now: datetime | None = None) -> Reminder:
        now = now or _now()
        minutes = minutes or Settings.load().snooze_minutes

        def apply(r: Reminder) -> None:
            # Snoozing something not yet due pushes it back from when it was going to fire.
            r.snoozed_until = max(now, r.effective_due) + timedelta(minutes=minutes)
            r.last_notified_at = None

        return self._mutate(rid, "snooze", now, apply)

    def reset(self, rid: int, now: datetime | None = None) -> Reminder:
        """Put a reminder back on its schedule: the next occurrence from now, no snooze, no nags.

        A finished one-off whose time hasn't passed yet is reopened.
        """
        now = now or _now()

        def apply(r: Reminder) -> None:
            if r.rule:
                nxt = next_occurrence(r.rule, now - timedelta(seconds=1))
                if nxt is None:
                    raise ScheduleError("That schedule has no more occurrences.")
                r.due_at = nxt
                r.completed_at = None
            elif r.completed_at and r.due_at > now:
                r.completed_at = None
            r.snoozed_until = r.last_notified_at = None
            r.notify_count = 0

        return self._mutate(rid, "reset", now, apply)

    def undo(self, rid: int | None = None) -> tuple[str, Reminder]:
        """Undo the most recent action (optionally for one reminder). Returns (action, reminder)."""
        with self.session() as s:
            q = select(UndoEntry).order_by(col(UndoEntry.id).desc())
            if rid is not None:
                q = q.where(UndoEntry.reminder_id == rid)
            entry = s.exec(q).first()
            if entry is None:
                raise NothingToUndo("Nothing to undo.")
            r = s.get(Reminder, entry.reminder_id)
            _restore(r, entry.state)
            if entry.completion_id:
                c = s.get(Completion, entry.completion_id)
                if c:
                    s.delete(c)
            s.delete(entry)
            s.add(r)
            s.commit()
            s.refresh(r)
            return entry.action, r

    def last_undoable(self) -> tuple[str, Reminder] | None:
        with self.session() as s:
            entry = s.exec(select(UndoEntry).order_by(col(UndoEntry.id).desc())).first()
            if entry is None:
                return None
            return entry.action, s.get(Reminder, entry.reminder_id)

    # ------------------------------------------------------------------ the scheduler's view

    def roll_missed(self, now: datetime | None = None) -> list[Reminder]:
        """Recurring reminders whose *next* occurrence has arrived while the old one was never done.

        The old occurrence is logged as missed, the streak resets and the reminder moves to the
        latest occurrence that has already started (so it alerts once, not once per missed slot).
        """
        now = now or _now()
        rolled: list[Reminder] = []
        with self.session() as s:
            q = select(Reminder).where(
                Reminder.active == True,  # noqa: E712
                col(Reminder.completed_at).is_(None),
                col(Reminder.rule).is_not(None),
                Reminder.due_at < now,
            )
            for r in s.exec(q):
                latest = latest_occurrence(r.rule, now)
                if latest is None or latest <= r.due_at:
                    continue
                self._log(s, r, "missed", now)
                r.streak = 0
                r.due_at = latest
                r.snoozed_until = r.last_notified_at = None
                r.notify_count = 0
                s.add(r)
                rolled.append(r)
            s.commit()
        return rolled

    def due_for_alert(self, now: datetime | None = None, settings: Settings | None = None) -> list[Reminder]:
        """Reminders that should pop a notification right now."""
        now = now or _now()
        settings = settings or Settings.load()
        quiet = settings.in_quiet_hours(now)
        out = []
        for r in self.list():
            if r.effective_due > now:
                continue
            if quiet and r.priority < settings.quiet_bypass_priority:
                continue
            if r.last_notified_at is None:
                out.append(r)
                continue
            if settings.nag_minutes <= 0:
                continue
            if settings.max_nags and r.notify_count >= settings.max_nags:
                continue
            if now - r.last_notified_at >= timedelta(minutes=settings.nag_minutes):
                out.append(r)
        return out

    def mark_notified(self, rid: int, now: datetime | None = None) -> None:
        now = now or _now()
        r = self.get(rid)
        r.last_notified_at = now
        r.notify_count += 1
        self._save(r)

    # ------------------------------------------------------------------ bulk

    def add_many(
        self, items: Iterable[dict], now: datetime | None = None
    ) -> tuple[list[Reminder], list[str]]:
        added, errors = [], []
        for i, item in enumerate(items, 1):
            try:
                tags = item.get("tags") or []
                if isinstance(tags, str):
                    tags = [t for t in tags.replace(";", ",").split(",")]
                added.append(
                    self.add(
                        str(item["title"]),
                        str(item.get("when") or item.get("schedule") or ""),
                        notes=str(item.get("notes") or ""),
                        tags=tags,
                        priority=item.get("priority"),
                        now=now,
                    )
                )
            except (KeyError, ScheduleError, ValueError) as e:
                errors.append(f"item {i}: {e}")
        return added, errors
