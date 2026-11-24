"""The guided "new / edit reminder" form for the dashboard, with a live preview."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup as Horizontal
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from .schedule import (
    EXAMPLES,
    PRIORITY_LABEL,
    REPEAT_CHOICES,
    ScheduleError,
    build_expression,
    explain,
    extract_inline_meta,
    schedule_in_title,
)

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
#: Which optional rows each repeat choice shows.
SHOWS = {
    "once": {"date", "times"},
    "daily": {"times"},
    "weekdays": {"times"},
    "weekends": {"times"},
    "days": {"days", "times"},
    "interval": {"every", "unit"},
    "ndays": {"every", "times"},
    "nweeks": {"every", "days", "times"},
    "monthly": {"every", "date", "times"},
    "yearly": {"date", "times"},
}
DATE_HINT = {
    "once": "Date, e.g. tomorrow, friday, dec 3 (blank = today)",
    "monthly": "Day of the month, e.g. 1st, 15th, last (blank = today's date)",
    "yearly": "Date, e.g. mar 14",
}
EVERY_HINT = {
    "interval": "Every",
    "ndays": "Every … days",
    "nweeks": "Every … weeks",
    "monthly": "Every … months",
}


@dataclass
class ReminderDraft:
    """What the form returns."""

    title: str
    when: str
    tags: list[str] = field(default_factory=list)
    priority: int = 1
    notes: str = ""


class ReminderForm(ModalScreen[ReminderDraft | None]):
    """Fill in a reminder step by step, or just type the schedule; the preview updates as you go."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]
    DEFAULT_CSS = """
    ReminderForm { align: center middle; }
    #form { width: 84; max-width: 100%; height: auto; max-height: 95%; padding: 1 2;
            border: thick $accent; background: $surface; }
    #form-title { text-style: bold; color: $accent; }
    .row { height: auto; }
    .lbl { width: 12; padding-top: 1; color: $text-muted; }
    #row-days .lbl { padding-top: 0; }
    .row Input, .row Select { width: 1fr; }
    #every { width: 12; }
    #unit { width: 20; }
    #days { width: 1fr; height: auto; }
    #days Checkbox { width: auto; margin-right: 1; padding: 0; border: none; }
    .muted { opacity: 50%; }
    #or { color: $text-muted; text-align: center; }
    #preview { height: auto; min-height: 3; padding: 0 1; border: round $panel-lighten-2; margin: 1 0; }
    #preview.ok { border: round $success; }
    #preview.bad { border: round $error; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    .hidden { display: none; }
    """

    def __init__(self, draft: ReminderDraft | None = None, heading: str = "New reminder"):
        super().__init__()
        self.draft = draft
        self.heading = heading
        self._when = ""

    def compose(self) -> ComposeResult:
        d = self.draft
        with VerticalScroll(id="form"):
            yield Label(f"⏰ {self.heading}", id="form-title")
            with Horizontal(classes="row"):
                yield Label("What", classes="lbl")
                yield Input(value=d.title if d else "", placeholder="e.g. Water the plants", id="title")
            with Horizontal(classes="row"):
                yield Label("Type it", classes="lbl")
                yield Input(
                    value=d.when if d else "",
                    placeholder="Quickest: every mon,thu at 9am · weekdays 8:30 · in 20 min",
                    id="quick",
                )
            yield Static("── or build it step by step ──", id="or")
            with Horizontal(classes="row"):
                yield Label("Repeat", classes="lbl")
                yield Select(
                    [(label, key) for key, label in REPEAT_CHOICES],
                    value="daily",
                    allow_blank=False,
                    id="repeat",
                )
            with Horizontal(classes="row", id="row-days"):
                yield Label("On", classes="lbl")
                with Horizontal(id="days"):
                    for key in DAY_KEYS:
                        yield Checkbox(key.capitalize(), id=f"day-{key}")
            with Horizontal(classes="row", id="row-every"):
                yield Label("Every", classes="lbl", id="every-lbl")
                yield Input("2", id="every", type="integer")
                yield Select(
                    [("minutes", "minutes"), ("hours", "hours")], value="hours", allow_blank=False, id="unit"
                )
            with Horizontal(classes="row", id="row-date"):
                yield Label("Date", classes="lbl")
                yield Input(placeholder="", id="date")
            with Horizontal(classes="row", id="row-times"):
                yield Label("At", classes="lbl")
                yield Input(placeholder="9am  ·  several: 8am, 8pm  ·  blank = 9am", id="times")
            yield Static(id="preview")
            with Horizontal(classes="row"):
                yield Label("Priority", classes="lbl")
                yield Select(
                    [(name.capitalize(), level) for level, name in PRIORITY_LABEL.items()],
                    value=d.priority if d else 1,
                    allow_blank=False,
                    id="priority",
                )
            with Horizontal(classes="row"):
                yield Label("Tags", classes="lbl")
                yield Input(value=" ".join(d.tags) if d else "", placeholder="home, health", id="tags")
            with Horizontal(classes="row"):
                yield Label("Notes", classes="lbl")
                yield Input(value=d.notes if d else "", placeholder="Shown in the notification", id="notes")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save  (Ctrl+S)", variant="success", id="save")

    def on_mount(self) -> None:
        self._sync_rows()
        self._update_preview()
        self.query_one("#title" if not self.draft else "#quick", Input).focus()

    # ------------------------------------------------------------------ form logic

    def _sync_rows(self) -> None:
        repeat = self.query_one("#repeat", Select).value
        shows = SHOWS.get(str(repeat), set())
        self.query_one("#row-days").set_class("days" not in shows, "hidden")
        self.query_one("#row-every").set_class("every" not in shows, "hidden")
        self.query_one("#unit").set_class("unit" not in shows, "hidden")
        self.query_one("#row-date").set_class("date" not in shows, "hidden")
        self.query_one("#row-times").set_class("times" not in shows, "hidden")
        self.query_one("#every-lbl", Label).update(EVERY_HINT.get(str(repeat), "Every"))
        self.query_one("#date", Input).placeholder = DATE_HINT.get(str(repeat), "")

    def _sync_builder_state(self) -> None:
        typed = bool(self.query_one("#quick", Input).value.strip()) or bool(
            schedule_in_title(self.query_one("#title", Input).value)
        )
        for row in ("#repeat", "#row-days", "#row-every", "#row-date", "#row-times"):
            self.query_one(row).set_class(typed, "muted")
        self.query_one("#or", Static).update(
            "── using the schedule you typed above (clear it to use the builder) ──"
            if typed
            else "── or build it step by step ──"
        )

    def schedule_text(self) -> str:
        """The expression the form currently describes (typed text wins over the builder)."""
        quick = self.query_one("#quick", Input).value.strip()
        if quick:
            return quick
        from_title = schedule_in_title(self.query_one("#title", Input).value)
        if from_title:
            return from_title[1]
        return build_expression(
            str(self.query_one("#repeat", Select).value),
            times=self.query_one("#times", Input).value,
            days=[k for k in DAY_KEYS if self.query_one(f"#day-{k}", Checkbox).value],
            every=self.query_one("#every", Input).value or 1,
            unit=str(self.query_one("#unit", Select).value),
            date=self.query_one("#date", Input).value,
        )

    def _update_preview(self) -> None:
        self._sync_builder_state()
        preview = self.query_one("#preview", Static)
        title = self.query_one("#title", Input).value.strip()
        if not self.query_one("#quick", Input).value.strip():
            from_title = schedule_in_title(title)
            title = from_title[0] if from_title else title
        try:
            when = self.schedule_text()
            sched, times = explain(when, datetime.now(), 3)
        except (ScheduleError, ValueError) as e:
            self._when = ""
            hint = " · ".join(EXAMPLES[:4])
            preview.update(f"[b red]✗ {e}[/]\n[dim]Try: {hint}[/]")
            preview.set_classes("bad")
            return
        self._when = when
        nxt = "  ·  ".join(t.strftime("%a %b %d %H:%M") for t in times)
        fixed = ""
        if sched.understood and sched.understood != " ".join(when.lower().split()):
            fixed = f"\n[dim]Understood as “{sched.understood}”[/]"
        name = f"[b]{title}[/] — " if title else ""
        preview.update(f"[b green]✓[/] {name}{sched.label}\n[dim]Next:[/] {nxt}{fixed}")
        preview.set_classes("ok")

    @on(Select.Changed, "#repeat")
    def _repeat_changed(self) -> None:
        self._sync_rows()
        self._update_preview()

    @on(Input.Changed)
    @on(Select.Changed)
    @on(Checkbox.Changed)
    def _changed(self) -> None:
        self._update_preview()

    @on(Input.Submitted)
    def _submitted(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        self._update_preview()
        title_raw = self.query_one("#title", Input).value.strip()
        if not self.query_one("#quick", Input).value.strip():
            from_title = schedule_in_title(title_raw)
            if from_title:
                title_raw = from_title[0]
        meta = extract_inline_meta(title_raw)
        if not meta.title:
            self.notify("Give the reminder a name first.", severity="warning")
            self.query_one("#title", Input).focus()
            return
        if not self._when:
            self.notify("The schedule isn't valid yet. See the red box.", severity="error")
            return
        tags = [t.strip().lstrip("#") for t in self.query_one("#tags", Input).value.replace(",", " ").split()]
        priority = (
            meta.priority if meta.priority is not None else int(self.query_one("#priority", Select).value)
        )
        self.dismiss(
            ReminderDraft(
                title=meta.title,
                when=self._when,
                tags=[t for t in [*tags, *meta.tags] if t],
                priority=priority,
                notes=self.query_one("#notes", Input).value.strip(),
            )
        )
