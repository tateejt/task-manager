"""The scratchpad: quick, labeled notes grouped by case, for copying into a case or an email.

Type a line the way you'd jot it down and it is split into a label and a value:

* ``INB exported 1,240``      -> INB exported = 1,240
* ``returned: 42 units``      -> returned = 42 units
* ``owner = jsmith``           -> owner = jsmith
* ``case 48213``               -> switches to case 48213 (``ticket``, ``inc``, ``sr`` work too)
* ``case 48213 imported 1198`` -> switches, and saves the rest
* anything else                -> a plain note

Cases have a ``template`` field so case templates (a checklist of fields) can be added later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, col, select

from .models import Case, Entry

_CASE_RE = re.compile(r"^(?:case|ticket|inc|sr|incident)\s*[#:]?\s*([A-Za-z0-9][\w.-]*)(?:\s+(.*))?$", re.I)
_SEP_RE = re.compile(r"^(?P<label>[^:=]*[A-Za-z][^:=]*?)\s*(?::\s+|\s*=\s*)(?P<value>.+)$")
_UNIT = r"(?:%|k|m|b|ms|s|sec|secs|min|mins|h|hrs?|kb|mb|gb|tb|units?|items?|records?|rows?|files?|orders?|lines?|pcs)"
_TRAILING_VALUE_RE = re.compile(
    rf"^(?P<label>.*?[A-Za-z].*?)\s+(?P<value>[-+]?[$€£]?\d[\d,]*(?:\.\d+)?\s*{_UNIT}?)$", re.I
)


@dataclass
class ParsedLine:
    kind: str  # empty | case | entry | note | search
    label: str = ""
    value: str = ""
    case_ref: str | None = None
    query: str = ""
    raw: str = ""

    @property
    def display(self) -> str:
        if self.kind == "entry":
            return f"{self.label} = {self.value}"
        if self.kind == "case":
            return f"case {self.case_ref}"
        return self.value


def split_label_value(text: str) -> tuple[str, str]:
    """``"INB exported 1,240"`` -> ``("INB exported", "1,240")``; no value found -> ``("", text)``."""
    t = " ".join(text.split())
    m = _SEP_RE.match(t)
    if m:
        return m.group("label").strip(), m.group("value").strip()
    m = _TRAILING_VALUE_RE.match(t)
    if m:
        return m.group("label").strip(), m.group("value").strip()
    return "", t


def parse_line(text: str) -> ParsedLine:
    raw = text.strip()
    if not raw:
        return ParsedLine("empty")
    if raw.startswith("/"):
        return ParsedLine("search", query=raw[1:].strip(), raw=raw)
    m = _CASE_RE.match(raw)
    if m and (m.group(2) is None or any(ch.isdigit() for ch in m.group(1))):
        ref, rest = m.group(1), (m.group(2) or "").strip()
        if not rest:
            return ParsedLine("case", case_ref=ref, raw=raw)
        label, value = split_label_value(rest)
        return ParsedLine("entry" if label else "note", label, value, case_ref=ref, raw=raw)
    label, value = split_label_value(raw)
    return ParsedLine("entry" if label else "note", label, value, raw=raw)


# --------------------------------------------------------------------------- groups and summaries


@dataclass
class Group:
    """Entries shown together: one case, or the un-filed notes of one day."""

    case: Case | None
    day: date
    entries: list[Entry]

    @property
    def key(self) -> str:
        return f"case:{self.case.ref}" if self.case else f"day:{self.day.isoformat()}"

    @property
    def title(self) -> str:
        if self.case:
            return f"Case {self.case.ref}" + (f" · {self.case.title}" if self.case.title else "")
        return "Notes"

    @property
    def last_at(self) -> datetime:
        return max(e.created_at for e in self.entries)


def day_label(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{d:%a %b} {d.day}"


def summary(group: Group) -> str:
    """Plain text ready to paste into a case or an email."""
    first = min(e.created_at for e in group.entries)
    head = (
        f"Case {group.case.ref}" + (f" - {group.case.title}" if group.case.title else "")
        if group.case
        else "Notes"
    )
    lines = [f"{head} ({first:%b} {first.day}, {first.year})"]
    width = max((len(e.label) for e in group.entries if e.label), default=0)
    for e in sorted(group.entries, key=lambda x: (x.created_at, x.id or 0)):
        lines.append(f"{(e.label + ':').ljust(width + 1)} {e.value}" if e.label else f"- {e.value}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- storage


class NotFound(LookupError):
    pass


class ScratchPad:
    """Scratchpad storage. Shares the reminders database (pass ``service.engine``)."""

    def __init__(self, engine):
        self.engine = engine

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def case(self, ref: str, now: datetime | None = None) -> Case:
        """Get a case by reference, creating it the first time it's used."""
        ref = ref.strip().lstrip("#")
        with self.session() as s:
            c = s.exec(select(Case).where(col(Case.ref) == ref)).first()
            if c is None:
                stamp = (now or datetime.now()).replace(microsecond=0)
                c = Case(ref=ref, created_at=stamp, updated_at=stamp)
                s.add(c)
                s.commit()
                s.refresh(c)
            return c

    def add(
        self, text: str, case: str | None = None, now: datetime | None = None
    ) -> tuple[Entry | None, Case | None]:
        """Save a typed line. Returns (entry, case); entry is None if the line only switched case."""
        now = (now or datetime.now()).replace(microsecond=0)
        p = parse_line(text)
        if p.kind in ("empty", "search"):
            return None, None
        ref = p.case_ref or case
        c = self.case(ref, now) if ref else None
        if p.kind == "case":
            return None, c
        with self.session() as s:
            entry = Entry(
                case_id=c.id if c else None, label=p.label, value=p.value, raw=p.raw, created_at=now
            )
            s.add(entry)
            if c is not None:
                c.updated_at = now
                s.add(c)
            s.commit()
            s.refresh(entry)
        return entry, c

    def update(self, entry_id: int, text: str) -> Entry:
        label, value = split_label_value(text)
        with self.session() as s:
            e = s.get(Entry, entry_id)
            if e is None:
                raise NotFound(f"No note #{entry_id}")
            e.label, e.value, e.raw = label, value, text.strip()
            s.add(e)
            s.commit()
            s.refresh(e)
            return e

    def delete(self, entry_id: int) -> Entry:
        with self.session() as s:
            e = s.get(Entry, entry_id)
            if e is None:
                raise NotFound(f"No note #{entry_id}")
            s.delete(e)
            s.commit()
            return e

    def restore(self, entry: Entry) -> Entry:
        """Put back a deleted entry (undo)."""
        with self.session() as s:
            e = Entry(case_id=entry.case_id, label=entry.label, value=entry.value, raw=entry.raw,
                      created_at=entry.created_at)  # fmt: skip
            s.add(e)
            s.commit()
            s.refresh(e)
            return e

    def delete_case(self, ref: str) -> None:
        with self.session() as s:
            c = s.exec(select(Case).where(col(Case.ref) == ref)).first()
            if c is None:
                raise NotFound(f"No case {ref}")
            s.exec(sa_delete(Entry).where(col(Entry.case_id) == c.id))
            s.delete(c)
            s.commit()

    def entries(self, *, case: str | None = None, query: str = "", limit: int = 2000) -> list[Entry]:
        with self.session() as s:
            q = select(Entry).order_by(col(Entry.created_at).desc(), col(Entry.id).desc()).limit(limit)
            if case:
                c = s.exec(select(Case).where(col(Case.ref) == case)).first()
                if c is None:
                    return []
                q = q.where(col(Entry.case_id) == c.id)
            rows = list(s.exec(q))
            if query:
                cases = {c.id: c for c in s.exec(select(Case))}
                ql = query.lower()
                rows = [e for e in rows if ql in e.raw.lower() or ql in e.text.lower()
                        or (e.case_id and ql in cases[e.case_id].ref.lower())]  # fmt: skip
            return rows

    def groups(self, *, query: str = "", limit: int = 2000) -> list[Group]:
        """Cases and per-day un-filed notes, most recently used first."""
        rows = self.entries(query=query, limit=limit)
        with self.session() as s:
            cases = {c.id: c for c in s.exec(select(Case))}
        out: dict[str, Group] = {}
        for e in rows:
            c = cases.get(e.case_id) if e.case_id else None
            g = Group(c, e.created_at.date(), [])
            out.setdefault(g.key, g).entries.append(e)
        for g in out.values():
            g.entries.sort(key=lambda x: (x.created_at, x.id or 0))
        return sorted(out.values(), key=lambda g: g.last_at, reverse=True)

    def group_for(self, case: str | None, day: date | None = None) -> Group | None:
        key = f"case:{case}" if case else f"day:{(day or date.today()).isoformat()}"
        return next((g for g in self.groups() if g.key == key), None)
