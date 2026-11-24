"""Bulk import / export in YAML or CSV."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import yaml

from .models import Reminder
from .schedule import PRIORITY_LABEL

FIELDS = ["title", "when", "tags", "priority", "notes"]


def reminder_to_dict(r: Reminder) -> dict:
    # One-offs export their absolute time so "in 20 minutes" doesn't drift on re-import.
    when = r.when_text if r.recurring and r.when_text else r.due_at.strftime("%Y-%m-%d %H:%M")
    return {
        "title": r.title,
        "when": when,
        "tags": r.tag_list,
        "priority": PRIORITY_LABEL.get(r.priority, "normal"),
        "notes": r.notes,
    }


def dump(reminders: list[Reminder], fmt: str) -> str:
    rows = [reminder_to_dict(r) for r in reminders]
    if fmt == "yaml":
        return yaml.safe_dump({"reminders": rows}, sort_keys=False, allow_unicode=True)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({**row, "tags": ",".join(row["tags"])})
        return buf.getvalue()
    raise ValueError(f"Unknown format '{fmt}' (use yaml or csv)")


def load(text: str, fmt: str) -> list[dict]:
    if fmt == "yaml":
        data = yaml.safe_load(text) or []
        if isinstance(data, dict):
            data = data.get("reminders", [])
        if not isinstance(data, list):
            raise ValueError("YAML must be a list of reminders or a 'reminders:' mapping")
        return [d for d in data if isinstance(d, dict)]
    if fmt == "csv":
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    raise ValueError(f"Unknown format '{fmt}' (use yaml or csv)")


def guess_format(path: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit.lower()
    return "csv" if path.suffix.lower() == ".csv" else "yaml"
