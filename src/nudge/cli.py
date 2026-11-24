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
from .service import NotFound, ReminderService
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
        if sys.stdout.isatty():
            from .tui import NudgeApp

            NudgeApp(svc()).run()
        else:
            console.print(ctx.get_help())


@app.command()
def add(
    title: Annotated[str, typer.Argument(help="What to do. May include #tags and !priority.")],
    when: Annotated[list[str] | None, typer.Argument(help="When, e.g. every mon,thu at 9am")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Tag (repeatable).")] = None,
    priority: Annotated[str | None, typer.Option("--priority", "-p", help="low|normal|high|urgent")] = None,
    notes: Annotated[str, typer.Option("--notes", "-n", help="Extra detail shown in the notification.")] = "",
):
    """Add a reminder.

    [bold]Examples[/]

      nudge add "Water plants" every mon,thu at 9am
      nudge add "Take meds #health !high" every day at 8am and 8pm
      nudge add "Stretch" every 90 minutes
      nudge add "Pay rent" monthly on the 1st at 10am -t bills
      nudge add "Call mom tomorrow at 6pm"
    """
    try:
        r = svc().add(title, " ".join(when or []), tags=tag or [], priority=priority, notes=notes)
    except ScheduleError as e:
        fail(str(e))
    console.print(
        f"[green]✓[/] Added [bold]#{r.id} {r.title}[/]  [dim]{r.schedule_label}[/]\n"
        f"  first reminder {relative(r.due_at, now())} ([cyan]{r.due_at:%a %b %d, %H:%M}[/])"
    )


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
def done(ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")]):
    """Mark reminder(s) done. Recurring ones move to their next time and grow their streak."""
    s = svc()
    _ids(ids, s.complete, "Done")


@app.command()
def skip(ids: Annotated[list[int], typer.Argument(help="Reminder id(s).")]):
    """Skip the current occurrence without breaking your streak."""
    s = svc()
    _ids(ids, s.skip, "Skipped")


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

    r = Reminder(id=0, title="Nudge is working", notes="Notifications are set up correctly.", due_at=now())
    make_notifier(queue.Queue(), console).show(r, nag=False, snooze_minutes=10, sound=Settings.load().sound)
    console.print("[green]✓[/] Sent a test notification.")


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
def startup_install():
    """Run Nudge in the background every time you sign in to Windows."""
    from . import startup

    try:
        cmd = startup.install()
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
def tui():
    """Open the interactive dashboard."""
    from .tui import NudgeApp

    NudgeApp(svc()).run()
