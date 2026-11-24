"""Core reminder logic. Every time-dependent method takes ``now`` so it is easy to test."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, col, create_engine, select

from .config import Settings, db_path
from .models import Completion, Reminder
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
            for c in s.exec(select(Completion).where(Completion.reminder_id == rid)):
                s.delete(c)
            s.delete(r)
            s.commit()
            return r

    def set_active(self, rid: int, active: bool, now: datetime | None = None) -> Reminder:
        now = now or _now()
        r = self.get(rid)
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

    def complete(self, rid: int, now: datetime | None = None) -> Reminder:
        now = now or _now()
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            if r.completed_at:
                return r
            self._log(s, r, "done", now)
            r.streak += 1
            r.best_streak = max(r.best_streak, r.streak)
            self._advance(r, now)
            s.add(r)
            s.commit()
            s.refresh(r)
            return r

    def skip(self, rid: int, now: datetime | None = None) -> Reminder:
        """Skip this occurrence without breaking the streak."""
        now = now or _now()
        with self.session() as s:
            r = s.get(Reminder, rid)
            if r is None:
                raise NotFound(f"No reminder with id {rid}.")
            self._log(s, r, "skipped", now)
            self._advance(r, now)
            s.add(r)
            s.commit()
            s.refresh(r)
            return r

    def snooze(self, rid: int, minutes: int | None = None, now: datetime | None = None) -> Reminder:
        now = now or _now()
        minutes = minutes or Settings.load().snooze_minutes
        r = self.get(rid)
        # Snoozing something not yet due pushes it back from when it was going to fire.
        r.snoozed_until = max(now, r.effective_due) + timedelta(minutes=minutes)
        r.last_notified_at = None
        return self._save(r)

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
