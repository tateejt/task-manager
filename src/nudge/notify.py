"""Desktop notifications.

The *content* of a notification is built by :func:`build_spec` (pure, testable anywhere); the
Windows notifier turns that into an actionable toast with the ``windows-toasts`` package:

* a branded app name and icon (registered once per user under HKCU, no admin needed),
* a coloured logo per state (normal / still waiting / urgent),
* a clear title, a status line ("Due now", "Overdue 25 min", "Snoozed"), and a context line,
* Done, a Snooze dropdown (10 min ... tomorrow morning) with its button, and Skip.

Button clicks arrive on a background thread, so they are pushed onto a queue that the runner
drains on its own thread. Elsewhere (or if toasts fail) it falls back to printing.
"""

from __future__ import annotations

import queue
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from importlib import resources
from pathlib import Path
from typing import Protocol

from rich.console import Console

from .models import Reminder

APP_ID = "Nudge.Reminders"
APP_NAME = "Nudge"


@dataclass(frozen=True)
class Action:
    kind: str  # done | snooze | skip
    reminder_id: int
    minutes: int | None = None


def encode_action(kind: str, rid: int, minutes: int | None = None) -> str:
    return f"{kind}:{rid}" + (f":{minutes}" if minutes else "")


def decode_action(arguments: str | None, inputs: dict | None = None) -> Action | None:
    """Parse a toast button's arguments. A snooze without minutes takes them from the dropdown."""
    if not arguments:
        return None
    parts = arguments.split(":")
    try:
        if parts[0] in ("done", "skip") and len(parts) == 2:
            return Action(parts[0], int(parts[1]))
        if parts[0] == "snooze" and len(parts) in (2, 3):
            minutes = int(parts[2]) if len(parts) == 3 else None
            if minutes is None and inputs and str(inputs.get("snooze", "")).isdigit():
                minutes = int(inputs["snooze"])
            return Action("snooze", int(parts[1]), minutes)
    except ValueError:
        return None
    return None


class Notifier(Protocol):
    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None: ...


# --------------------------------------------------------------------------- content

_EMOJI = [
    (r"water|drink|hydrat", "💧"),
    (r"pill|med|vitamin|medicine|dose", "💊"),
    (r"plant|garden", "🪴"),
    (r"gym|workout|exercise|run\b|lift", "🏋️"),
    (r"stretch|stand|posture|yoga|breath", "🧘"),
    (r"walk|dog", "🚶"),
    (r"call|phone|ring", "📞"),
    (r"email|mail|inbox", "📧"),
    (r"pay|rent|bill|invoice|tax|budget", "💳"),
    (r"trash|garbage|bin|recycl", "🗑️"),
    (r"clean|vacuum|laundry|dish|tidy", "🧹"),
    (r"sleep|bed", "😴"),
    (r"read|book|study", "📖"),
    (r"meet|standup|stand-up|review|report", "🗓️"),
    (r"birthday|anniversary", "🎂"),
    (r"eat|lunch|dinner|breakfast|cook|meal", "🍽️"),
    (r"backup|back up|update|laptop|computer", "💾"),
    (r"teeth|floss|brush", "🪥"),
    (r"filter|hvac|car|oil", "🔧"),
    (r"pet|cat|feed", "🐾"),
]


def emoji_for(title: str) -> str:
    t = title.lower()
    for pattern, emoji in _EMOJI:
        if re.search(pattern, t):
            return emoji
    return "⏰"


def _ago(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins} min"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{hours // 24} d"


def _clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def snooze_choices(now: datetime) -> list[tuple[int, str]]:
    """(minutes, label) options for the toast's snooze dropdown."""
    tomorrow_9 = datetime.combine(now.date() + timedelta(days=1), time(9))
    choices = [(10, "10 minutes"), (30, "30 minutes"), (60, "1 hour"), (180, "3 hours")]
    choices.append((int((tomorrow_9 - now).total_seconds() // 60), "Tomorrow at 9 AM"))
    return choices


@dataclass
class ToastSpec:
    title: str
    status: str
    context: str
    attribution: str
    logo: str  # asset name: nudge | nudge-nag | nudge-urgent
    persistent: bool  # stay on screen until acted on
    loud: bool  # use the reminder sound
    due: datetime
    snooze_default: int
    snooze_options: list[tuple[int, str]] = field(default_factory=list)


def build_spec(r: Reminder, *, nag: bool, snooze_minutes: int, now: datetime | None = None) -> ToastSpec:
    now = now or datetime.now().replace(microsecond=0)
    due = r.due_at
    late = now - r.effective_due

    if r.snoozed_until and not nag:
        status = f"Snoozed reminder · was due {_clock(due)}"
    elif nag or late >= timedelta(minutes=5):
        status = f"Still waiting · overdue {_ago(now - due)}"
    else:
        status = f"Due now · {_clock(due)}"
    if r.priority >= 3:
        status = "Urgent · " + status
    elif r.priority == 2:
        status = "Important · " + status

    bits = []
    if r.notes:
        bits.append(r.notes)
    else:
        bits.append(r.schedule_label)
    if r.streak >= 2:
        bits.append(f"🔥 {r.streak} in a row, keep it going")
    elif r.streak == 0 and r.best_streak >= 3:
        bits.append(f"Best streak: {r.best_streak}. Start a new one today")
    context = " · ".join(bits)

    tags = " ".join(f"#{t}" for t in r.tag_list)
    attribution = f"{APP_NAME} · {tags}" if tags else APP_NAME

    logo = (
        "nudge-urgent"
        if r.priority >= 3
        else ("nudge-nag" if nag or late >= timedelta(minutes=5) else "nudge")
    )
    options = snooze_choices(now)
    if snooze_minutes not in [m for m, _ in options]:
        options.insert(0, (snooze_minutes, f"{snooze_minutes} minutes"))
    return ToastSpec(
        title=f"{emoji_for(r.title)}  {r.title}",
        status=status,
        context=context,
        attribution=attribution,
        logo=logo,
        persistent=nag or r.priority >= 2,
        loud=nag or r.priority >= 2,
        due=due,
        snooze_default=snooze_minutes,
        snooze_options=options,
    )


def asset_path(name: str) -> Path:
    return Path(str(resources.files("nudge") / "assets" / name))


# --------------------------------------------------------------------------- notifiers


class ConsoleNotifier:
    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None:
        spec = build_spec(reminder, nag=nag, snooze_minutes=snooze_minutes)
        if sound:
            self.console.bell()
        colour = {"nudge-urgent": "red", "nudge-nag": "yellow"}.get(spec.logo, "cyan")
        self.console.print(
            f"[bold {colour}]{spec.title}[/] [dim]#{reminder.id}[/]\n  {spec.status}\n  [dim]{spec.context}[/]"
        )


def register_app_identity() -> bool:
    """Give toasts the Nudge name and icon instead of "Command Prompt". Safe to call repeatedly."""
    try:
        from windows_toasts.register_hkey_aumid import register_hkey

        register_hkey(APP_ID, APP_NAME, asset_path("nudge.ico"), activator=True)
        return True
    except Exception:  # pragma: no cover - depends on the Windows runtime
        try:
            from windows_toasts.register_hkey_aumid import register_hkey

            register_hkey(APP_ID, APP_NAME, asset_path("nudge.ico"))
            return True
        except Exception:
            return False


class WindowsToastNotifier:  # pragma: no cover - exercised on Windows CI
    def __init__(self, actions: queue.Queue[Action]):
        from windows_toasts import InteractableWindowsToaster

        self.actions = actions
        self.branded = register_app_identity()
        self.toaster = (
            InteractableWindowsToaster(APP_NAME, APP_ID)
            if self.branded
            else InteractableWindowsToaster(APP_NAME)
        )
        # Prefer the app-wide activator: it also catches clicks on toasts shown before a restart.
        self.app_wide = False
        if self.branded:
            try:
                self.toaster.on_activated = self._on_activated
                self.app_wide = True
            except Exception:
                self.app_wide = False

    def _on_activated(self, event) -> None:
        action = decode_action(getattr(event, "arguments", None), getattr(event, "inputs", None))
        if action:
            self.actions.put(action)

    def build_toast(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool):
        from windows_toasts import (
            AudioSource,
            Toast,
            ToastAudio,
            ToastButton,
            ToastButtonColour,
            ToastDisplayImage,
            ToastDuration,
            ToastImagePosition,
            ToastInputSelectionBox,
            ToastScenario,
            ToastSelection,
        )

        spec = build_spec(reminder, nag=nag, snooze_minutes=snooze_minutes)
        rid = reminder.id
        selections = [ToastSelection(str(m), label) for m, label in spec.snooze_options]
        default = next((s for s in selections if s.selection_id == str(spec.snooze_default)), selections[0])
        picker = ToastInputSelectionBox("snooze", "", selections, default)
        if not sound:
            audio = ToastAudio(silent=True)
        else:
            audio = ToastAudio(AudioSource.Reminder if spec.loud else AudioSource.Default)
        return Toast(
            [spec.title, spec.status, spec.context],
            images=[
                ToastDisplayImage.fromPath(
                    asset_path(f"{spec.logo}.png"), "Nudge", position=ToastImagePosition.AppLogo
                )
            ],
            inputs=[picker],
            actions=[
                ToastButton("✓  Done", encode_action("done", rid), colour=ToastButtonColour.Green),
                ToastButton("Snooze", encode_action("snooze", rid), relatedInput=picker),
                ToastButton("Skip", encode_action("skip", rid)),
            ],
            attribution_text=spec.attribution,
            # The Reminder scenario keeps the toast on screen until you act on it.
            scenario=ToastScenario.Reminder if spec.persistent else ToastScenario.Default,
            duration=ToastDuration.Long,
            audio=audio,
            timestamp=spec.due,
            tag=f"nudge-{rid}",  # a nag replaces the previous toast instead of stacking
            group="nudge",
            on_activated=None if self.app_wide else self._on_activated,
        )

    def show(self, reminder: Reminder, *, nag: bool, snooze_minutes: int, sound: bool) -> None:
        self.toaster.show_toast(
            self.build_toast(reminder, nag=nag, snooze_minutes=snooze_minutes, sound=sound)
        )


def make_notifier(actions: queue.Queue[Action], console: Console | None = None) -> Notifier:
    if sys.platform == "win32":
        try:
            return WindowsToastNotifier(actions)
        except Exception as e:  # pragma: no cover - depends on Windows runtime
            (console or Console()).print(f"[red]Toasts unavailable ({e}); falling back to console.[/]")
    return ConsoleNotifier(console)
