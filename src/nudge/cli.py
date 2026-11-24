"""The `nudge` command line."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import Settings, data_dir, db_path
from .schedule import ScheduleError
from .service import AlreadyDone, NotFound, NothingToUndo, ReminderService
from .ui import priority_label, relative, reminder_table

app = typer.Typer(
    name="nudge",
    help="⏰ Nudge: recurring reminders with Windows notifications.\n\n"
    'Quick start:  nudge add "Water plants" every mon,thu at 9am   then   nudge run',
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
startup_app = typer.Typer(help="Start the reminder runner automatically when you sign in.")
config_app = typer.Typer(help="View or change settings (quiet hours, nagging, snooze length...).")
app.add_typer(startup_app, name="startup")
app.add_typer(config_app, name="config")

console = Console()
err = Console(stderr=True)


def svc() -> ReminderService:
    return ReminderService(db_path())


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def fail(msg: str) -> None:
    err.print(f"[bold red]✗[/] {msg}")
    raise typer.Exit(1)


def _ids(ids: list[int], fn, verb: str) -> None:
    for rid in ids:
        try:
            r = fn(rid)
        except NotFound as e:
            err.print(f"[red]✗[/] {e}")
            continue
        except AlreadyDone as e:
            err.print(f"[yellow]![/] {e} [dim](add --early to do the next one ahead of time)[/]")
            continue
        extra = ""
        if not r.active:
            extra = ""
        elif r.completed_at:
            extra = " [dim](finished)[/]"
        elif r.recurring:
            extra = f" [dim]next {relative(r.effective_due, now())}[/]"
        streak = f" 🔥 {r.streak}" if verb == "Done" and r.streak > 1 else ""
        console.print(f"[green]✓[/] {verb}: [bold]{r.title}[/]{streak}{extra}")


def parse_duration(text: str) -> int:
    """'15', '15m', '2h', '1h30m' -> minutes."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    m = re.fullmatch(r"(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*m(?:in(?:ute)?s?)?)?", text)
    if not m or not any(m.groups()):
        raise typer.BadParameter(f"'{text}' isn't a duration like 15m, 2h or 1h30m")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version.")] = False,
):
    """Run with no command to open the interactive dashboard."""
    if version:
        console.print(f"nudge {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        from . import gui

        if sys.platform == "win32" and gui.available():
            gui.main()
        elif sys.stdout.isatty():
            from .tui import NudgeApp

            NudgeApp(svc()).run()
        else:
            console.print(ctx.get_help())


def _print_schedule(label: str, times: list[datetime], understood: str = "", typed: str = "") -> None:
    nxt = "  ·  ".join(t.strftime("%a %b %d %H:%M") for t in times)
    console.print(f"  [bold]{label}[/]\n  [dim]next:[/] {nxt}")
    if understood and understood != " ".join(typed.lower().split()):
        console.print(f"  [dim]understood as “{understood}”[/]")


def _ask_schedule(default: str = "") -> str:
    """Prompt until the user types a schedule we understand, showing what it means."""
    from rich.prompt import Prompt

    from .schedule import EXAMPLES, explain

    console.print("[dim]Examples: " + " · ".join(EXAMPLES[:6]) + "[/]")
    while True:
        text = Prompt.ask("[cyan]When?[/]", default=default or None, console=console)
        if not text:
            continue
        try:
            sched, times = explain(text, now(), 3)
        except ScheduleError as e:
            err.print(f"  [red]✗[/] {e}")
            continue
        _print_schedule(sched.label, times, sched.understood, text)
        return text


@app.command()
def add(
    title: Annotated[str | None, typer.Argument(help="What to do. May include #tags and !priority.")] = None,
    when: Annotated[list[str] | None, typer.Argument(help="When, e.g. every mon,thu at 9am")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Tag (repeatable).")] = None,
    priority: Annotated[str | None, typer.Option("--priority", "-p", help="low|normal|high|urgent")] = None,
    notes: Annotated[str, typer.Option("--notes", "-n", help="Extra detail shown in the notification.")] = "",
):
    """Add a reminder. Run it with no arguments to be asked step by step.

    [bold]Examples[/]

      nudge add
      nudge add "Water plants" every mon,thu at 9am
      nudge add "Take meds #health !high" every day at 8am and 8pm
      nudge add "Stretch" every 90 minutes
      nudge add "Pay rent" monthly on the 1st at 10am -t bills
      nudge add "Call mom tomorrow at 6pm"

    Typos and casual phrasing are fine: "evry thrusday 9.30am", "mondays and fridays at 6", "twice a day".
    """
    from .schedule import explain, split_title_and_schedule

    interactive = sys.stdin.isatty()
    if title is None:
        if not interactive:
            fail('Give a title, e.g. nudge add "Water plants" every mon at 9am')
        from rich.prompt import Prompt

        title = Prompt.ask("[cyan]What do you want to be reminded about?[/]", console=console).strip()
        if not title:
            fail("A reminder needs a title.")
    when_text = " ".join(when or [])
    if not when_text:
        title, when_text = split_title_and_schedule(title, now())
    if not when_text:
        if not interactive:
            fail("When should I remind you? e.g. 'every day at 9am', 'weekdays at 8', 'in 30 minutes'.")
        when_text = _ask_schedule()
    try:
        r = svc().add(title, when_text, tags=tag or [], priority=priority, notes=notes)
    except ScheduleError as e:
        if not interactive:
            fail(str(e))
        err.print(f"[red]✗[/] {e}")
        when_text = _ask_schedule()
        r = svc().add(title, when_text, tags=tag or [], priority=priority, notes=notes)
    sched, times = explain(when_text, now(), 3)
    console.print(
        f"[green]✓[/] Added [bold]#{r.id} {r.title}[/]  [dim](first one {relative(r.due_at, now())})[/]"
    )
    _print_schedule(r.schedule_label, times, sched.understood, when_text)


@app.command()
def preview(when: Annotated[list[str], typer.Argument(help="A schedule, e.g. every other friday at 5pm")]):
    """Check what a schedule means before adding it: shows the next few times."""
    from .schedule import EXAMPLES, explain

    text = " ".join(when)
    try:
        sched, times = explain(text, now(), 5)
    except ScheduleError as e:
        err.print(f"[red]✗[/] {e}")
        err.print("[dim]Try: " + " · ".join(EXAMPLES) + "[/]")
        raise typer.Exit(1) from None
    _print_schedule(sched.label, times, sched.understood, text)


@app.command("list")
def list_cmd(
    all_: Annotated[bool, typer.Option("--all", "-a", help="Include paused and finished reminders.")] = False,
    tag: Annotated[str | None, typer.Option("--tag", "-t", help="Only this tag.")] = None,
    search: Annotated[str | None, typer.Option("--search", "-s", help="Filter by text.")] = None,
):
    """List reminders, soonest first."""
    s = svc()
    rows = s.search(search) if search else s.list(include_inactive=all_, include_done=all_, tag=tag)
    if tag and search:
        rows = [r for r in rows if tag.lower().lstrip("#") in r.tag_list]
    if not rows:
        console.print('[dim]No reminders yet. Try:[/] nudge add "Drink water" every 2 hours')
        return
    console.print(reminder_table(rows, now()))


app.command("ls", hidden=True)(list_cmd)


@app.command()
def today():
    """What's overdue or due before midnight."""
    n = now()
    end = n.replace(hour=23, minute=59, second=59)
    rows = [(t, r) for t, r in svc().agenda(n.replace(hour=0, minute=0, second=0), end)]
    overdue = [r for t, r in rows if t <= n]
    later = [(t, r) for t, r in rows if t > n]
    if not rows:
        console.print("[green]Nothing due today. 🎉[/]")
        return
    seen = set()
    if overdue:
        console.print("[bold red]Due now[/]")
        for r in overdue:
            if r.id in seen:
                continue
            seen.add(r.id)
            console.print(f"  [red]●[/] [bold]#{r.id}[/] {r.title}  [dim]{relative(r.effective_due, n)}[/]")
    if later:
        console.print("[bold]Later today[/]")
        for t, r in later:
            console.print(f"  [cyan]○[/] {t:%H:%M}  [bold]#{r.id}[/] {r.title}")


@app.command()
def agenda(days: Annotated[int, typer.Option("--days", "-d", help="How many days ahead.")] = 7):
    """Every upcoming occurrence for the next few days."""
    n = now()
    items = svc().agenda(n, n + timedelta(days=days))
    if not items:
        console.print("[dim]Nothing scheduled.[/]")
        return
    by_day: dict = {}
    for t, r in items:
        by_day.setdefault(t.date(), []).append((t, r))
    for day, entries in by_day.items():
        label = "Today" if day == n.date() else ("Tomorrow" if day == n.date() + timedelta(days=1) else "")
        console.print(f"\n[bold cyan]{day:%A %b %d}[/] [dim]{label}[/]")
        counts: dict[int, int] = {}
        for _, r in entries:
            counts[r.id] = counts.get(r.id, 0) + 1
        printed: set[int] = set()
        for t, r in entries:
            if r.id in printed:
                continue
            many = counts[r.id] > 4  # collapse frequent ones like "every 90 minutes"
            if many:
                printed.add(r.id)
            style = "red" if t <= n else "cyan"
            tags = f"  [magenta]{' '.join('#' + x for x in r.tag_list)}[/]" if r.tags else ""
            more = f"  [dim]×{counts[r.id]} ({r.schedule_label.lower()})[/]" if many else ""
            console.print(f"  [{style}]{t:%H:%M}[/]  #{r.id} {r.title}{tags}{more}")


@app.command()
def show(rid: Annotated[int, typer.Argument(help="Reminder id.")]):
    """Show one reminder in detail, with recent history."""
    s = svc()
    try:
        r = s.get(rid)
    except NotFound as e:
        fail(str(e))
    n = now()
    body = Table.grid(padding=(0, 2))
    body.add_row("[dim]Schedule[/]", r.schedule_label)
    body.add_row("[dim]Next[/]", f"{r.effective_due:%a %b %d %H:%M} ({relative(r.effective_due, n)})")
    body.add_row("[dim]Priority[/]", priority_label(r.priority))
    if r.tags:
        body.add_row("[dim]Tags[/]", " ".join("#" + t for t in r.tag_list))
    if r.notes:
        body.add_row("[dim]Notes[/]", r.notes)
    body.add_row("[dim]Streak[/]", f"{r.streak} (best {r.best_streak})")
    body.add_row("[dim]Status[/]", "paused" if not r.active else ("finished" if r.completed_at else "active"))
    hist = s.history(rid, limit=8)
    if hist:
        body.add_row("", "")
        for c, _ in hist:
            icon = {"done": "[green]✓[/]", "skipped": "[yellow]↷[/]", "missed": "[red]✗[/]"}[c.status]
            body.add_row(f"[dim]{c.at:%b %d %H:%M}[/]", f"{icon} {c.status} (due {c.due_at:%b %d %H:%M})")
    console.print(Panel(body, title=f"#{r.id} {r.title}", border_style="cyan", expand=False))


@app.command()
def done(
    ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")],
    early: Annotated[
        bool, typer.Option("--early", help="Also allow ticking off the next occurrence.")
    ] = False,
):
    """Mark reminder(s) done. Recurring ones move to their next time and grow their streak.

    Pressing done twice won't skip ahead: once the current one is done, the next must come due first
    (or use --early). Made a mistake? `nudge undo`.
    """
    s = svc()
    _ids(ids, lambda i: s.complete(i, force=early), "Done")


@app.command()
def skip(
    ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")],
    early: Annotated[bool, typer.Option("--early", help="Also allow skipping the next occurrence.")] = False,
):
    """Skip the current occurrence without breaking your streak."""
    s = svc()
    _ids(ids, lambda i: s.skip(i, force=early), "Skipped")


UNDO_VERBS = {"done": "Done", "skip": "Skip", "snooze": "Snooze", "reset": "Reset",
              "pause": "Pause", "resume": "Resume", "edit": "Edit"}  # fmt: skip


@app.command()
def undo(rid: Annotated[int | None, typer.Argument(help="Only undo this reminder's last action.")] = None):
    """Undo the last done / skip / snooze / reset / pause / edit. Run it again to go further back."""
    try:
        action, r = svc().undo(rid)
    except NothingToUndo as e:
        fail(str(e))
    console.print(
        f"[green]↶[/] Undid {UNDO_VERBS.get(action, action).lower()} on [bold]{r.title}[/]  "
        f"[dim]next {relative(r.effective_due, now())} ({r.effective_due:%a %b %d %H:%M})[/]"
    )


@app.command()
def reset(ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")]):
    """Put reminders back on their schedule: next time from now, no snooze. Undoable."""
    s = svc()
    _ids(ids, s.reset, "Reset")


@app.command()
def snooze(
    rid: Annotated[int, typer.Argument(help="Reminder id.")],
    duration: Annotated[
        str | None, typer.Argument(help="e.g. 10m, 1h, 1h30m (default from settings)")
    ] = None,
):
    """Snooze a reminder."""
    minutes = parse_duration(duration) if duration else None
    try:
        r = svc().snooze(rid, minutes)
    except NotFound as e:
        fail(str(e))
    console.print(f"[green]💤[/] Snoozed [bold]{r.title}[/] until {r.snoozed_until:%H:%M}")


@app.command()
def pause(ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")]):
    """Pause reminders (no notifications until resumed)."""
    s = svc()
    _ids(ids, lambda i: s.set_active(i, False), "Paused")


@app.command()
def resume(ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")]):
    """Resume paused reminders."""
    s = svc()
    _ids(ids, lambda i: s.set_active(i, True), "Resumed")


@app.command("rm")
def remove(
    ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
):
    """Delete reminders and their history."""
    s = svc()
    for rid in ids:
        try:
            r = s.get(rid)
        except NotFound as e:
            err.print(f"[red]✗[/] {e}")
            continue
        if not yes and not typer.confirm(f"Delete #{r.id} '{r.title}'?"):
            continue
        s.delete(rid)
        console.print(f"[green]✓[/] Deleted [bold]{r.title}[/]")


@app.command()
def edit(
    rid: Annotated[int, typer.Argument(help="Reminder id.")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    when: Annotated[str | None, typer.Option("--when", "-w", help="New schedule.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Replace tags (repeatable).")] = None,
    priority: Annotated[str | None, typer.Option("--priority", "-p")] = None,
    notes: Annotated[str | None, typer.Option("--notes", "-n")] = None,
):
    """Change a reminder's title, schedule, tags, priority or notes."""
    try:
        r = svc().edit(rid, title=title, when=when, tags=tag, priority=priority, notes=notes)
    except (NotFound, ScheduleError) as e:
        fail(str(e))
    console.print(f"[green]✓[/] Updated [bold]#{r.id} {r.title}[/]  [dim]{r.schedule_label}[/]")


@app.command()
def history(
    rid: Annotated[int | None, typer.Argument(help="Only this reminder.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l")] = 30,
):
    """What you've done, skipped and missed."""
    rows = svc().history(rid, limit)
    if not rows:
        console.print("[dim]No history yet.[/]")
        return
    t = Table(header_style="bold cyan", border_style="grey37")
    for col in ("When", "Reminder", "Result", "Was due"):
        t.add_column(col)
    style = {"done": "green", "skipped": "yellow", "missed": "red"}
    for c, r in rows:
        t.add_row(
            f"{c.at:%b %d %H:%M}",
            f"#{r.id} {r.title}",
            f"[{style[c.status]}]{c.status}[/]",
            f"{c.due_at:%b %d %H:%M}",
        )
    console.print(t)


@app.command()
def stats():
    """Your streaks and completion rate."""
    st = svc().stats()
    rate = st.done_7d / (st.done_7d + st.missed_7d) * 100 if (st.done_7d + st.missed_7d) else 100.0
    grid = Table.grid(padding=(0, 3))
    grid.add_row("[dim]Active reminders[/]", str(st.active), "[dim]Due now[/]", f"[red]{st.overdue}[/]")
    grid.add_row("[dim]Due today[/]", str(st.due_today), "[dim]7-day completion[/]", f"{rate:.0f}%")
    grid.add_row(
        "[dim]Done (7d)[/]", f"[green]{st.done_7d}[/]", "[dim]Missed (7d)[/]", f"[red]{st.missed_7d}[/]"
    )
    console.print(Panel(grid, title="📊 Nudge stats", border_style="cyan", expand=False))
    if st.best:
        console.print("[bold]Best streaks[/]")
        for title, n in st.best:
            console.print(f"  🔥 {n:>3}  {title}")


@app.command()
def run(
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Log to a file instead of the terminal.")
    ] = False,
):
    """Run the background notifier. Leave it running (or use `nudge startup install`)."""
    from .runner import run_forever

    out = console
    if quiet or sys.stdout is None:
        log = open(data_dir() / "runner.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        out = Console(file=log, force_terminal=False, width=120)
    run_forever(svc(), out)


@app.command("test-notify")
def test_notify():
    """Show a sample notification to check that toasts work."""
    import queue

    from .models import Reminder
    from .notify import make_notifier

    console.print(f"nudge {__version__}")
    r = Reminder(id=0, title="Nudge is working", notes="Notifications are set up correctly.", due_at=now())
    notifier = make_notifier(queue.Queue(), console)
    notifier.show(r, nag=False, snooze_minutes=10, sound=Settings.load().sound)
    if type(notifier).__name__ == "WindowsToastNotifier":
        brand = (
            "as Nudge" if getattr(notifier, "branded", False) else "without the Nudge name (branding failed)"
        )
        console.print(
            f"[green]✓[/] Sent a Windows notification {brand}. Look at the bottom-right of your screen."
        )
    else:
        console.print(
            "[yellow]![/] Windows notifications aren't available here, so it was printed above instead."
        )


@app.command("export")
def export_cmd(
    file: Annotated[
        Path | None, typer.Argument(help="Output file (.yaml or .csv). Prints if omitted.")
    ] = None,
    fmt: Annotated[str | None, typer.Option("--format", "-f", help="yaml or csv")] = None,
):
    """Export reminders to YAML or CSV."""
    from .transfer import dump, guess_format

    fmt = guess_format(file or Path("x.yaml"), fmt)
    text = dump(svc().list(include_inactive=True), fmt)
    if file:
        file.write_text(text, encoding="utf-8")
        console.print(f"[green]✓[/] Exported to {file}")
    else:
        sys.stdout.write(text)


@app.command("import")
def import_cmd(
    file: Annotated[Path, typer.Argument(help="A .yaml or .csv file with title/when/tags/priority/notes.")],
    fmt: Annotated[str | None, typer.Option("--format", "-f", help="yaml or csv")] = None,
):
    """Import many reminders at once."""
    from .transfer import guess_format, load

    if not file.exists():
        fail(f"{file} doesn't exist.")
    try:
        items = load(file.read_text(encoding="utf-8"), guess_format(file, fmt))
    except ValueError as e:
        fail(str(e))
    added, errors = svc().add_many(items)
    console.print(f"[green]✓[/] Imported {len(added)} reminder(s).")
    for e in errors:
        err.print(f"[yellow]![/] Skipped {e}")


# ---------------------------------------------------------------- config


@config_app.command("show")
def config_show():
    """Show current settings."""
    from dataclasses import asdict

    t = Table(header_style="bold cyan", border_style="grey37")
    t.add_column("Setting")
    t.add_column("Value")
    for k, v in asdict(Settings.load()).items():
        t.add_row(k, str(v))
    console.print(t)
    console.print(f"[dim]Data folder: {data_dir()}[/]")


@config_app.command("set")
def config_set(key: str, value: str):
    """Change a setting, e.g. `nudge config set quiet_start 23:00`. Use an empty value to disable quiet hours."""
    st = Settings.load()
    try:
        st.set(key, value)
    except (KeyError, ValueError) as e:
        fail(str(e).strip("'\""))
    st.save()
    console.print(f"[green]✓[/] {key} = {getattr(st, key)}")


# ---------------------------------------------------------------- startup


@startup_app.command("install")
def startup_install(
    headless: Annotated[
        bool, typer.Option("--headless", help="Only the notifier, without the tray app window.")
    ] = False,
):
    """Start Nudge in the tray every time you sign in to Windows."""
    from . import gui, startup

    try:
        cmd = startup.install(app=not headless and gui.available())
    except RuntimeError as e:
        fail(str(e))
    console.print(f"[green]✓[/] Nudge will start at login:\n  [dim]{cmd}[/]")


@startup_app.command("uninstall")
def startup_uninstall():
    """Stop starting Nudge at login."""
    from . import startup

    try:
        removed = startup.uninstall()
    except RuntimeError as e:
        fail(str(e))
    console.print("[green]✓[/] Removed from startup." if removed else "Nudge wasn't set to start at login.")


@startup_app.command("status")
def startup_status():
    """Check whether Nudge starts at login."""
    from . import startup

    try:
        cmd = startup.status()
    except RuntimeError as e:
        fail(str(e))
    console.print(f"[green]On[/]: {cmd}" if cmd else "[yellow]Off[/]. Enable with `nudge startup install`.")


@app.command()
def note(
    text: Annotated[list[str], typer.Argument(help="e.g. INB exported 1,240   or   case 48213")],
    case: Annotated[str | None, typer.Option("--case", "-c", help="Case or ticket number.")] = None,
):
    """Jot a value in the scratchpad (label + value), optionally under a case."""
    from .scratch import ScratchPad

    entry, c = ScratchPad(svc().engine).add(" ".join(text), case=case)
    where = f"case {c.ref}" if c else "today's notes"
    if entry is None:
        console.print(f"[green]✓[/] Case {c.ref} is ready" if c else "[yellow]Nothing to save[/]")
        return
    shown = f"{entry.label} = [bold cyan]{entry.value}[/]" if entry.label else entry.value
    console.print(f"[green]✓[/] {shown} [dim]→ {where}[/]")


@app.command()
def notes(
    case: Annotated[str | None, typer.Option("--case", "-c", help="Only this case.")] = None,
    search: Annotated[str | None, typer.Option("--search", "-s", help="Find text or a case number.")] = None,
    copy: Annotated[bool, typer.Option("--copy", help="Print just the paste-ready summary.")] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many groups to show.")] = 5,
):
    """Show scratchpad notes grouped by case, ready to paste."""
    from .scratch import ScratchPad, summary

    pad = ScratchPad(svc().engine)
    groups = pad.groups(query=search or "")
    if case:
        groups = [g for g in groups if g.case and g.case.ref == case]
    if not groups:
        console.print("[dim]No notes yet. Try: nudge note INB exported 1,240 -c 48213[/]")
        return
    for g in groups[:limit]:
        text = summary(g)
        if copy:
            sys.stdout.write(text + "\n\n")
        else:
            console.print(text, highlight=False)
            console.print()


@app.command()
def pad():
    """Open the scratchpad window."""
    from . import gui

    if not gui.available():
        fail("The desktop app needs Flet: uv tool install --reinstall .")
    from .gui.app import run as run_app

    run_app(pad=True)


@app.command("app")
def open_app(
    minimized: Annotated[bool, typer.Option("--minimized", help="Start hidden in the tray.")] = False,
    pad: Annotated[bool, typer.Option("--pad", help="Open straight into the scratchpad.")] = False,
    web: Annotated[
        int | None, typer.Option("--web", help="Serve the window in a browser on this port.")
    ] = None,
):
    """Open the Nudge desktop window (also what `nudge` does on Windows)."""
    from . import gui

    if not gui.available():
        fail("The desktop app needs Flet: uv tool install --reinstall . (or pip install flet)")
    from .gui.app import run as run_app

    run_app(minimized=minimized, web_port=web, pad=pad)


@app.command()
def shortcut(
    desktop: Annotated[
        bool, typer.Option("--desktop/--no-desktop", help="Also put one on the desktop.")
    ] = True,
):
    """Add Nudge to the Start menu (and desktop) so it opens with a click."""
    from . import startup

    try:
        paths, errors = startup.create_shortcuts(desktop)
    except RuntimeError as e:
        fail(str(e))
    for problem in errors:
        err.print(f"[yellow]![/] Couldn't add the shortcut to {problem}")
    if not paths:
        fail("No shortcuts were made. You can still open the app by typing: nudge")
    for path in paths:
        console.print(f"[green]✓[/] {path}")


@app.command()
def tui():
    """Open the interactive dashboard."""
    from .tui import NudgeApp

    NudgeApp(svc()).run()
