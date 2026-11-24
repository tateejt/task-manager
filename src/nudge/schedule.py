"""Recurrence rules and natural-language schedule parsing.

A schedule is either a one-off due time or a recurrence stored as iCalendar
RRULE text (``DTSTART:...\\nRRULE:...``, possibly several RRULE lines for
multiple times of day). Times are naive *local wall-clock* datetimes on
purpose: "every day at 9am" must stay 9am across daylight-saving changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import dateparser
from dateutil.rrule import rrulestr

WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
DAY_NAMES = {
    "mon": "MO", "monday": "MO", "mondays": "MO",
    "tue": "TU", "tues": "TU", "tuesday": "TU", "tuesdays": "TU",
    "wed": "WE", "weds": "WE", "wednesday": "WE", "wednesdays": "WE",
    "thu": "TH", "thur": "TH", "thurs": "TH", "thursday": "TH", "thursdays": "TH",
    "fri": "FR", "friday": "FR", "fridays": "FR",
    "sat": "SA", "saturday": "SA", "saturdays": "SA",
    "sun": "SU", "sunday": "SU", "sundays": "SU",
}  # fmt: skip
DAY_LABEL = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
         ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
         ("oct", "october"), ("nov", "november"), ("dec", "december")]
    )
    for m in names
}  # fmt: skip
UNITS = {
    "min": "MINUTELY", "mins": "MINUTELY", "minute": "MINUTELY", "minutes": "MINUTELY", "m": "MINUTELY",
    "hour": "HOURLY", "hours": "HOURLY", "hr": "HOURLY", "hrs": "HOURLY", "h": "HOURLY",
    "day": "DAILY", "days": "DAILY", "d": "DAILY",
    "week": "WEEKLY", "weeks": "WEEKLY", "w": "WEEKLY",
    "month": "MONTHLY", "months": "MONTHLY",
    "year": "YEARLY", "years": "YEARLY",
}  # fmt: skip
FREQ_LABEL = {"MINUTELY": "minute", "HOURLY": "hour", "DAILY": "day", "WEEKLY": "week",
              "MONTHLY": "month", "YEARLY": "year"}  # fmt: skip

DEFAULT_TIME = time(9, 0)


class ScheduleError(ValueError):
    """Raised when a schedule expression can't be understood."""


@dataclass(frozen=True)
class Schedule:
    """A parsed schedule: either recurring (``rule`` set) or one-off (``due`` only)."""

    due: datetime
    rule: str | None = None
    label: str = ""

    @property
    def recurring(self) -> bool:
        return self.rule is not None

    def next_after(self, moment: datetime) -> datetime | None:
        """Next occurrence strictly after ``moment`` (None for one-off schedules)."""
        return next_occurrence(self.rule, moment) if self.rule else None


def _rule_set(rule: str):
    return rrulestr(rule, forceset=True)


def next_occurrence(rule: str, after: datetime) -> datetime | None:
    return _rule_set(rule).after(after, inc=False)


def latest_occurrence(rule: str, at_or_before: datetime) -> datetime | None:
    return _rule_set(rule).before(at_or_before, inc=True)


def occurrences_between(rule: str, start: datetime, end: datetime) -> list[datetime]:
    return list(_rule_set(rule).between(start, end, inc=True))


def build_rule(dtstart: datetime, rrules: list[str]) -> str:
    lines = [f"DTSTART:{dtstart.strftime('%Y%m%dT%H%M%S')}"]
    lines += [f"RRULE:{r}" for r in rrules]
    return "\n".join(lines)


# --------------------------------------------------------------------------- time parsing

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$")
_NAMED_TIMES = {"noon": time(12), "midday": time(12), "midnight": time(0), "morning": time(8),
                "afternoon": time(14), "evening": time(18), "night": time(21), "tonight": time(20)}  # fmt: skip


def parse_time(text: str) -> time:
    """Parse '9', '9am', '9:30pm', '21:15', 'noon'."""
    t = text.strip().lower().replace(".", "")
    if t in _NAMED_TIMES:
        return _NAMED_TIMES[t]
    m = _TIME_RE.match(t)
    if not m:
        raise ScheduleError(f"Couldn't read the time '{text}'. Try '9am', '18:30' or 'noon'.")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            raise ScheduleError(f"'{text}' isn't a valid 12-hour time.")
        hour = hour % 12 + (12 if ampm.startswith("p") else 0)
    if hour > 23 or minute > 59:
        raise ScheduleError(f"'{text}' isn't a valid time.")
    return time(hour, minute)


def _split_list(text: str) -> list[str]:
    return [p for p in re.split(r"\s*(?:,|\band\b|&|\+)\s*", text.strip()) if p]


def _parse_times(text: str) -> list[time]:
    return sorted({parse_time(p) for p in _split_list(text)})


def _fmt_time(t: time) -> str:
    return datetime.combine(datetime.today(), t).strftime("%I:%M %p").lstrip("0")


def _ordinal(n: int) -> str:
    if n == -1:
        return "last day"
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# --------------------------------------------------------------------------- schedule parsing

_AT_SPLIT = re.compile(r"\s+at\s+|^at\s+")
_STARTING = re.compile(r"\s+(?:starting|from|beginning)\s+(.+)$")


def parse_schedule(text: str, now: datetime | None = None) -> Schedule:
    """Turn an expression like ``every mon,thu at 9am`` into a :class:`Schedule`.

    Supported (case-insensitive):

    * ``every day at 9am`` / ``daily at 7:30`` / ``every day at 9am and 9pm``
    * ``weekdays at 8``, ``weekends at 10am``
    * ``every mon,thu at 6pm`` / ``every monday and friday``
    * ``every 2 hours`` / ``every 90 minutes`` / ``every 3 days at noon``
    * ``every week`` / ``weekly on fri at 5pm`` / ``every 2 weeks on sat``
    * ``monthly on the 1st at 9am`` / ``every month on the last day`` / ``monthly on the 15th``
    * ``yearly on mar 14`` / ``every year on dec 25 at 8am``
    * optional ``starting <date>`` suffix for recurring schedules
    * one-offs: ``in 20 minutes``, ``tomorrow at 5pm``, ``friday at noon``, ``dec 3 at 2pm``, ``at 6pm``
    """
    now = (now or datetime.now()).replace(microsecond=0)
    raw = " ".join(text.strip().lower().split())
    if not raw:
        raise ScheduleError("No schedule given. Try 'every day at 9am' or 'in 30 minutes'.")

    start = now
    m = _STARTING.search(raw)
    if m:
        parsed = dateparser.parse(m.group(1), settings={"RELATIVE_BASE": now, "PREFER_DATES_FROM": "future"})
        if not parsed:
            raise ScheduleError(f"Couldn't read the start date '{m.group(1)}'.")
        start = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        raw = raw[: m.start()]

    recurring = _parse_recurring(raw, now, start)
    if recurring:
        return recurring
    return _parse_one_off(raw, now)


def _with_times(rest: str) -> tuple[str, list[time] | None]:
    parts = _AT_SPLIT.split(rest, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), _parse_times(parts[1])
    return rest.strip(), None


def _parse_recurring(raw: str, now: datetime, start: datetime) -> Schedule | None:
    aliases = {
        r"^daily\b": "every day", r"^weekly\b": "every week", r"^monthly\b": "every month",
        r"^yearly\b|^annually\b": "every year", r"^hourly\b": "every hour",
        r"^(?:every\s+)?weekdays?\b": "every weekday", r"^(?:every\s+)?weekends?\b": "every weekend",
        r"^each\b": "every",
    }  # fmt: skip
    for pat, rep in aliases.items():
        raw = re.sub(pat, rep, raw)
    if not raw.startswith("every"):
        return None
    body, times = _with_times(raw[len("every") :].strip())

    if body in ("weekday", "weekdays"):
        return _weekly(now, start, WEEKDAYS[:5], times, 1, "Weekdays")
    if body in ("weekend", "weekends"):
        return _weekly(now, start, WEEKDAYS[5:], times, 1, "Weekends")

    # "every [N|other] <unit|days> [on ...]"
    m = re.match(r"^(?:(\d+|other)\s+)?(.+?)(?:\s+on\s+(.+))?$", body)
    if not m:
        raise ScheduleError(f"Couldn't understand '{raw}'.")
    count_s, word, on = m.group(1), m.group(2), m.group(3)
    interval = 2 if count_s == "other" else int(count_s or 1)
    if interval < 1:
        raise ScheduleError("The interval must be at least 1.")

    if word not in UNITS:
        # Day-name lists: "every mon,thu", "every monday and friday", "every other sat"
        if on:
            raise ScheduleError(f"Couldn't understand '{raw}'.")
        return _weekly(now, start, _parse_days(word), times, interval, None)

    freq = UNITS[word]
    every = f"Every {interval} {FREQ_LABEL[freq]}s" if interval > 1 else f"Every {FREQ_LABEL[freq]}"

    if freq in ("MINUTELY", "HOURLY"):
        if times or on:
            raise ScheduleError("Minute/hour intervals can't have 'at' or 'on' parts.")
        anchor = start if start > now else now
        rule = build_rule(anchor, [f"FREQ={freq};INTERVAL={interval}"])
        first = next_occurrence(rule, now)
        return Schedule(due=first, rule=rule, label=every)

    times = times or [DEFAULT_TIME]
    tlabel = " and ".join(_fmt_time(t) for t in times)

    if freq == "DAILY":
        if on:
            raise ScheduleError("Use 'every mon,wed' style for specific days.")
        dtstart = start.replace(hour=0, minute=0, second=0)
        rules = [
            f"FREQ=DAILY;INTERVAL={interval};BYHOUR={t.hour};BYMINUTE={t.minute};BYSECOND=0" for t in times
        ]
        return _finish(now, dtstart, rules, f"{every} at {tlabel}")

    if freq == "WEEKLY":
        days = _parse_days(on) if on else [WEEKDAYS[start.weekday()]]
        return _weekly(now, start, days, times, interval, None)

    if freq == "MONTHLY":
        mdays = _parse_month_days(on) if on else [start.day]
        dtstart = start.replace(day=1, hour=0, minute=0, second=0)
        if interval > 1 and all(0 < d < start.day for d in mdays):
            # Every day asked for has passed this month, so count the interval from next month.
            dtstart = (dtstart + timedelta(days=32)).replace(day=1)
        bymd = ",".join(str(d) for d in mdays)
        rules = [
            f"FREQ=MONTHLY;INTERVAL={interval};BYMONTHDAY={bymd};BYHOUR={t.hour};BYMINUTE={t.minute};BYSECOND=0"
            for t in times
        ]
        days_label = ", ".join(_ordinal(d) for d in mdays)
        return _finish(now, dtstart, rules, f"{every} on the {days_label} at {tlabel}")

    # YEARLY
    if on:
        month, day = _parse_month_and_day(on)
    else:
        month, day = start.month, start.day
    dtstart = start.replace(month=1, day=1, hour=0, minute=0, second=0)
    rules = [
        f"FREQ=YEARLY;INTERVAL={interval};BYMONTH={month};BYMONTHDAY={day};BYHOUR={t.hour};"
        f"BYMINUTE={t.minute};BYSECOND=0"
        for t in times
    ]
    mlabel = datetime(2000, month, 1).strftime("%b")
    return _finish(now, dtstart, rules, f"{every} on {mlabel} {day} at {tlabel}")


def _weekly(now, start, days, times, interval, label) -> Schedule:
    times = times or [DEFAULT_TIME]
    tlabel = " and ".join(_fmt_time(t) for t in times)
    # Anchor to the Monday of the start week so INTERVAL>1 counts whole weeks predictably.
    monday = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0)
    byday = ",".join(days)
    rules = [
        f"FREQ=WEEKLY;INTERVAL={interval};WKST=MO;BYDAY={byday};BYHOUR={t.hour};BYMINUTE={t.minute};BYSECOND=0"
        for t in times
    ]
    if label is None:
        dlabel = ", ".join(DAY_LABEL[d] for d in days)
        label = f"Every {dlabel}" if interval == 1 else f"Every {interval} weeks on {dlabel}"
    return _finish(now, monday, rules, f"{label} at {tlabel}", not_before=start)


def _finish(
    now: datetime, dtstart: datetime, rules: list[str], label: str, not_before: datetime | None = None
) -> Schedule:
    rule = build_rule(dtstart, rules)
    # Never earlier than now, nor earlier than the requested start.
    floor = not_before or dtstart
    first = next_occurrence(rule, max(now, floor - timedelta(seconds=1)))
    if first is None:
        raise ScheduleError("That schedule never occurs.")
    return Schedule(due=first, rule=rule, label=label)


def _parse_days(text: str) -> list[str]:
    days: list[str] = []
    for part in _split_list(text):
        if part in ("weekday", "weekdays"):
            days += WEEKDAYS[:5]
        elif part in ("weekend", "weekends"):
            days += WEEKDAYS[5:]
        elif part in DAY_NAMES:
            days.append(DAY_NAMES[part])
        else:
            raise ScheduleError(f"'{part}' isn't a day of the week.")
    if not days:
        raise ScheduleError("No days given.")
    return sorted(set(days), key=WEEKDAYS.index)


def _parse_month_days(text: str) -> list[int]:
    text = re.sub(r"\bthe\b|\bday\b|\bdays\b|\bof the month\b", "", text).strip()
    out: list[int] = []
    for part in _split_list(text):
        if part in ("last", "end", "the last"):
            out.append(-1)
            continue
        m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?$", part)
        if not m or not 1 <= int(m.group(1)) <= 31:
            raise ScheduleError(f"'{part}' isn't a day of the month (use 1st..31st or 'last').")
        out.append(int(m.group(1)))
    if not out:
        raise ScheduleError("No days of the month given.")
    return sorted(set(out), key=lambda d: 99 if d == -1 else d)


def _parse_month_and_day(text: str) -> tuple[int, int]:
    text = text.replace("the ", "").replace(" of ", " ").strip()
    m = re.match(r"^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$", text) or re.match(
        r"^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)$", text
    )
    if not m:
        raise ScheduleError(f"Couldn't read the date '{text}'. Try 'mar 14'.")
    a, b = m.groups()
    month_name, day = (a, int(b)) if a.isalpha() else (b, int(a))
    if month_name not in MONTHS:
        raise ScheduleError(f"'{month_name}' isn't a month.")
    return MONTHS[month_name], day


def _parse_one_off(raw: str, now: datetime) -> Schedule:
    m = re.match(r"^in\s+(\d+)\s*([a-z]+)$", raw)
    if m and m.group(2) in UNITS and UNITS[m.group(2)] in ("MINUTELY", "HOURLY", "DAILY", "WEEKLY"):
        n, freq = int(m.group(1)), UNITS[m.group(2)]
        delta = {"MINUTELY": timedelta(minutes=n), "HOURLY": timedelta(hours=n),
                 "DAILY": timedelta(days=n), "WEEKLY": timedelta(weeks=n)}[freq]  # fmt: skip
        due = now + delta
        return Schedule(due=due, label=f"Once, {due:%a %b %d %I:%M %p}".replace(" 0", " "))

    m = re.match(r"^(?:(today|tonight|tomorrow)\s+)?at\s+(.+)$", raw) or re.match(
        r"^(today|tonight|tomorrow)(?:\s+at\s+(.+))?$", raw
    )
    if m:
        day_word, t_text = m.group(1), m.group(2)
        t = parse_time(t_text) if t_text else (time(20) if day_word == "tonight" else DEFAULT_TIME)
        base = now.date() + timedelta(days=1 if day_word == "tomorrow" else 0)
        due = datetime.combine(base, t)
        if due <= now and day_word is None:
            due += timedelta(days=1)
        if due <= now:
            raise ScheduleError("That time has already passed today.")
        return Schedule(due=due, label=f"Once, {due:%a %b %d %I:%M %p}".replace(" 0", " "))

    parsed = dateparser.parse(
        raw,
        settings={"RELATIVE_BASE": now, "PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if not parsed:
        raise ScheduleError(
            f"Couldn't understand '{raw}'. Try 'every day at 9am', 'every mon,thu at 6pm', "
            "'every 2 hours', 'monthly on the 1st' or 'tomorrow at 5pm'."
        )
    parsed = parsed.replace(microsecond=0)
    if not re.search(r"\d|noon|midnight|morning|evening|night", raw):
        # A bare date ("friday") means the default time on that day.
        parsed = datetime.combine(parsed.date(), DEFAULT_TIME)
    if parsed <= now:
        raise ScheduleError(f"'{raw}' is in the past ({parsed:%Y-%m-%d %H:%M}).")
    return Schedule(due=parsed, label=f"Once, {parsed:%a %b %d %I:%M %p}".replace(" 0", " "))


# --------------------------------------------------------------------------- quick add

_SCHEDULE_START = re.compile(
    r"\s(?=(?:every|each|daily|weekly|monthly|yearly|annually|hourly|weekdays?|weekends?|in\s+\d+|"
    r"tomorrow|today|tonight|at\s+\d+|at\s+noon|at\s+midnight|on\s+[a-z]+|next\s+[a-z]+)\b)"
)


def split_title_and_schedule(text: str, now: datetime | None = None) -> tuple[str, str]:
    """Split ``"Water plants every mon,thu at 9am"`` into title and schedule text.

    Tries each place a schedule could start, left to right, and keeps the first one that
    parses, so titles like "Turn on lights every day at 7pm" still work.
    """
    candidates = [m.end() for m in _SCHEDULE_START.finditer(text)]
    for pos in candidates:
        title, when = text[: pos - 1].strip(), text[pos:].strip()
        try:
            parse_schedule(when, now)
        except ScheduleError:
            continue
        if title:
            return title, when
    if candidates:  # nothing parsed: surface the most likely schedule so the error is useful
        return text[: candidates[-1] - 1].strip(), text[candidates[-1] :].strip()
    return text.strip(), ""


@dataclass
class QuickAdd:
    title: str
    tags: list[str]
    priority: int | None


_TAG_RE = re.compile(r"(?:^|\s)#([\w-]+)")
_PRIO_RE = re.compile(r"(?:^|\s)!(low|normal|high|urgent|[0-3])\b", re.I)
PRIORITY_NAMES = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
PRIORITY_LABEL = {0: "low", 1: "normal", 2: "high", 3: "urgent"}


def parse_priority(value: str | int | None) -> int:
    if value is None:
        return 1
    if isinstance(value, int):
        if not 0 <= value <= 3:
            raise ScheduleError("Priority must be 0-3.")
        return value
    v = str(value).strip().lower()
    if v.isdigit():
        return parse_priority(int(v))
    if v not in PRIORITY_NAMES:
        raise ScheduleError(f"Unknown priority '{value}'. Use low, normal, high or urgent.")
    return PRIORITY_NAMES[v]


def extract_inline_meta(title: str) -> QuickAdd:
    """Pull ``#tags`` and ``!priority`` markers out of a title."""
    tags = [t.lower() for t in _TAG_RE.findall(title)]
    pm = _PRIO_RE.search(title)
    priority = parse_priority(pm.group(1)) if pm else None
    clean = _PRIO_RE.sub(" ", _TAG_RE.sub(" ", title))
    return QuickAdd(title=" ".join(clean.split()), tags=tags, priority=priority)
