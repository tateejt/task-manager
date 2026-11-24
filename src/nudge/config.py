"""Paths and user settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, time
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "nudge"


def data_dir() -> Path:
    """Where the database and settings live. Override with the NUDGE_HOME env var."""
    override = os.environ.get("NUDGE_HOME")
    path = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "nudge.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


@dataclass
class Settings:
    #: Re-alert an un-done reminder every N minutes (0 disables nagging).
    nag_minutes: int = 15
    #: Stop nagging after this many alerts for one occurrence (0 = never stop).
    max_nags: int = 8
    #: Default snooze length used by toasts and `nudge snooze`.
    snooze_minutes: int = 10
    #: Quiet hours as "HH:MM" strings; empty disables. Can wrap midnight (22:00 -> 07:00).
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    #: Reminders at or above this priority still alert during quiet hours (3 = urgent only).
    quiet_bypass_priority: int = 3
    #: How often the background runner checks for due reminders.
    poll_seconds: int = 20
    #: Play a sound with toasts.
    sound: bool = True
    #: Desktop app appearance: system, dark or light.
    theme: str = "system"

    @classmethod
    def load(cls) -> Settings:
        p = settings_path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        settings_path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def set(self, key: str, value: str) -> None:
        types = {f.name: f.type for f in fields(self)}
        if key not in types:
            raise KeyError(f"Unknown setting '{key}'. Known: {', '.join(types)}")
        current = getattr(self, key)
        if isinstance(current, bool):
            parsed: object = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            parsed = int(value)
        else:
            parsed = value.strip()
            if key.startswith("quiet_") and parsed:
                _parse_hhmm(str(parsed))
            if key == "theme" and parsed not in ("system", "dark", "light"):
                raise ValueError("theme must be system, dark or light")
        setattr(self, key, parsed)

    def in_quiet_hours(self, moment: datetime) -> bool:
        if not self.quiet_start or not self.quiet_end:
            return False
        start, end, now = _parse_hhmm(self.quiet_start), _parse_hhmm(self.quiet_end), moment.time()
        if start == end:
            return False
        if start < end:
            return start <= now < end
        return now >= start or now < end


def _parse_hhmm(value: str) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except ValueError as e:
        raise ValueError(f"'{value}' isn't a HH:MM time") from e
