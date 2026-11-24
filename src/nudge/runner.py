"""The background loop that fires notifications."""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import Settings, data_dir
from .notify import Action, Notifier, make_notifier
from .service import NotFound, ReminderService


def apply_action(service: ReminderService, action: Action, now: datetime | None = None) -> str:
    try:
        if action.kind == "done":
            r = service.complete(action.reminder_id, now=now)
            return f"Done: {r.title}"
        if action.kind == "skip":
            r = service.skip(action.reminder_id, now=now)
            return f"Skipped: {r.title}"
        if action.kind == "snooze":
            r = service.snooze(action.reminder_id, action.minutes, now=now)
            return f"Snoozed: {r.title} until {r.snoozed_until:%H:%M}"
    except NotFound:
        return f"Reminder {action.reminder_id} no longer exists"
    return f"Unknown action {action.kind}"


def tick(
    service: ReminderService,
    notifier: Notifier,
    settings: Settings,
    actions: queue.Queue[Action] | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> int:
    """One pass of the scheduler. Returns how many notifications were shown."""
    while actions is not None:
        try:
            log(apply_action(service, actions.get_nowait(), now))
        except queue.Empty:
            break
    for r in service.roll_missed(now):
        log(f"Missed an occurrence of '{r.title}', moved to {r.due_at:%a %H:%M}")
    shown = 0
    for r in service.due_for_alert(now, settings):
        try:
            notifier.show(
                r, nag=r.notify_count > 0, snooze_minutes=settings.snooze_minutes, sound=settings.sound
            )
        except Exception as e:  # keep the loop alive whatever the notifier does
            log(f"Notification failed for #{r.id}: {e}")
            continue
        service.mark_notified(r.id, now)
        shown += 1
    return shown


class SingleInstance:
    """A lock file so only one runner fires notifications at a time."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "runner.lock"
        self._fh = None

    def acquire(self) -> bool:
        self._fh = open(self.path, "a+")  # noqa: SIM115
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        return True

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def run_forever(
    service: ReminderService, console: Console | None = None, stop: threading.Event | None = None
):
    console = console or Console()
    stop = stop or threading.Event()
    actions: queue.Queue[Action] = queue.Queue()
    notifier = make_notifier(actions, console)
    lock = SingleInstance()
    if not lock.acquire():
        console.print("[yellow]Another Nudge runner is already active.[/]")
        return
    console.print(
        f"[green]Nudge is watching your reminders[/] [dim](using {type(notifier).__name__}, Ctrl+C to stop)[/]"
    )
    try:
        while not stop.is_set():
            settings = Settings.load()  # pick up `nudge config set` changes live
            tick(
                service,
                notifier,
                settings,
                actions,
                log=lambda m: console.print(f"[dim]{datetime.now():%H:%M}[/] {m}"),
            )
            # Wake early when a toast button is pressed.
            try:
                action = actions.get(timeout=settings.poll_seconds)
                actions.put(action)
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        console.print("Stopped.")
    finally:
        lock.release()
