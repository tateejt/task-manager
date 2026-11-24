"""Desktop notifications.

On Windows this uses actionable toasts (``windows-toasts``) with Done / Snooze / Skip buttons.
Button clicks are delivered on a background thread, so they are pushed onto a queue that the
runner drains on its own thread. Elsewhere (or if toasts fail) it falls back to printing.
"""

from __future__ import annotations

import queue
import sys
from dataclasses import dataclass
from typing import Protocol

from rich.console import Console

from .models import Reminder
from .schedule import PRIORITY_LABEL


@dataclass(frozen=True)
class Action:
    kind: str  # done | snooze | skip
    reminder_id: int
    minutes: int | None = None


def encode_action(kind: str, rid: int, minutes: int | None = None) -> str:
    return f"{kind}:{rid}" + (f":{minutes}" if minutes else "")


def decode_action(arguments: str | None) -> Action | None:
    if not arguments:
        return None
    parts = arguments.split(":")
    try:
        if parts[0] in ("done", "skip") and len(parts) == 2:
            return Action(parts[0], int(parts[1]))
        if parts[0] == "snooze" and len(parts) in (2, 3):
            return Action("snooze", int(parts[1]), int(parts[2]) if len(parts) == 3 else None)
    except ValueError:
        return None
    return None


class Notifier(Protocol):
    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None: ...


def describe(r: Reminder, nag: bool) -> tuple[str, str]:
    prefix = "⏰ " if not nag else "🔁 Still pending: "
    body = r.notes or r.schedule_label
    if r.priority >= 2:
        body = f"[{PRIORITY_LABEL[r.priority].upper()}] {body}"
    if r.streak >= 2:
        body += f"  ·  🔥 {r.streak} streak"
    return prefix + r.title, body


class ConsoleNotifier:
    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None:
        title, body = describe(reminder, nag)
        if sound:
            self.console.bell()
        self.console.print(f"[bold yellow]{title}[/] [dim]#{reminder.id}[/]  {body}")


class WindowsToastNotifier:
    def __init__(self, actions: queue.Queue[Action]):
        from windows_toasts import InteractableWindowsToaster

        self.actions = actions
        self.toaster = InteractableWindowsToaster("Nudge")

    def _on_activated(self, event) -> None:
        action = decode_action(getattr(event, "arguments", None))
        if action:
            self.actions.put(action)

    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None:
        from windows_toasts import (
            AudioSource,
            Toast,
            ToastAudio,
            ToastButton,
            ToastButtonColour,
            ToastScenario,
        )

        title, body = describe(reminder, nag)
        rid = reminder.id
        toast = Toast(
            [title, body],
            actions=[
                ToastButton("✔ Done", encode_action("done", rid), colour=ToastButtonColour.Green),
                ToastButton(f"Snooze {snooze_minutes}m", encode_action("snooze", rid, snooze_minutes)),
                ToastButton("Snooze 1h", encode_action("snooze", rid, 60)),
                ToastButton("Skip", encode_action("skip", rid)),
            ],
            # The Reminder scenario keeps the toast on screen until you act on it.
            scenario=ToastScenario.Reminder if reminder.priority >= 2 or nag else ToastScenario.Default,
            audio=ToastAudio(AudioSource.Reminder) if sound else ToastAudio(silent=True),
            tag=f"nudge-{rid}",  # a nag replaces the previous toast instead of stacking
            group="nudge",
            on_activated=self._on_activated,
        )
        self.toaster.show_toast(toast)


def make_notifier(actions: queue.Queue[Action], console: Console | None = None) -> Notifier:
    if sys.platform == "win32":
        try:
            return WindowsToastNotifier(actions)
        except Exception as e:  # pragma: no cover - depends on Windows runtime
            (console or Console()).print(f"[red]Toasts unavailable ({e}); falling back to console.[/]")
    return ConsoleNotifier(console)
