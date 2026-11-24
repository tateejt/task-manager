"""Interactive dashboard (Textual)."""

from __future__ import annotations

import contextlib
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from .schedule import ScheduleError
from .service import ReminderService
from .ui import PRIORITY_ICON, PRIORITY_STYLE, due_text


class StatsBar(Static):
    pass


class NudgeApp(App):
    TITLE = "Nudge"
    SUB_TITLE = "recurring reminders"
    CSS = """
    Screen { layout: vertical; }
    StatsBar { height: 1; padding: 0 1; color: $text-muted; }
    #table { height: 1fr; }
    #bar { height: auto; dock: bottom; margin-bottom: 1; }
    #bar Input { width: 1fr; }
    .hidden { display: none; }
    """
    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("d", "done", "Done"),
        Binding("s", "snooze", "Snooze 10m"),
        Binding("k", "skip", "Skip"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("x", "delete", "Delete"),
        Binding("slash", "filter", "Filter"),
        Binding("h", "toggle_all", "Show all"),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, service: ReminderService):
        super().__init__()
        self.service = service
        self.mode: str | None = None  # "add" | "filter"
        self.filter_text = ""
        self.show_all = False
        self._confirm_delete: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatsBar(id="stats")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="bar", classes="hidden"):
            yield Input(id="input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "", "Reminder", "Next", "Schedule", "Tags", "🔥")
        self.refresh_table()
        self.set_interval(30, self.refresh_table)

    # ------------------------------------------------------------------ data

    def refresh_table(self) -> None:
        now = datetime.now().replace(microsecond=0)
        table = self.query_one(DataTable)
        selected = self.selected_id()
        table.clear()
        rows = self.service.list(include_inactive=self.show_all, include_done=self.show_all)
        if self.filter_text:
            f = self.filter_text.lower().lstrip("#")
            rows = [r for r in rows if f in r.title.lower() or f in r.tag_list]
        for r in rows:
            table.add_row(
                str(r.id),
                Text(PRIORITY_ICON.get(r.priority, " "), style=PRIORITY_STYLE.get(r.priority, "")),
                Text(r.title, style=PRIORITY_STYLE.get(r.priority, "")),
                due_text(r, now),
                Text(r.schedule_label, style="dim"),
                Text(" ".join("#" + t for t in r.tag_list), style="magenta"),
                str(r.streak or ""),
                key=str(r.id),
            )
        if selected is not None:
            with contextlib.suppress(Exception):
                table.move_cursor(row=table.get_row_index(str(selected)))
        st = self.service.stats(now)
        flt = f"  ·  filter: {self.filter_text}" if self.filter_text else ""
        self.query_one(StatsBar).update(
            f"{st.active} active  ·  [red]{st.overdue} due now[/]  ·  {st.due_today} today  ·  "
            f"[green]{st.done_7d} done this week[/]{flt}"
        )

    def selected_id(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return int(key.value) if key and key.value else None

    def _act(self, fn, verb: str) -> None:
        rid = self.selected_id()
        if rid is None:
            return
        r = fn(rid)
        self.notify(f"{verb}: {r.title}", timeout=3)
        self.refresh_table()

    # ------------------------------------------------------------------ actions

    def action_done(self) -> None:
        self._act(self.service.complete, "Done ✓")

    def action_skip(self) -> None:
        self._act(self.service.skip, "Skipped")

    def action_snooze(self) -> None:
        self._act(lambda i: self.service.snooze(i, 10), "Snoozed 10m")

    def action_toggle_pause(self) -> None:
        rid = self.selected_id()
        if rid is None:
            return
        r = self.service.get(rid)
        self._act(lambda i: self.service.set_active(i, not r.active), "Resumed" if not r.active else "Paused")

    def action_delete(self) -> None:
        rid = self.selected_id()
        if rid is None:
            return
        if self._confirm_delete != rid:
            self._confirm_delete = rid
            self.notify("Press x again to delete", severity="warning", timeout=3)
            return
        self._confirm_delete = None
        self._act(self.service.delete, "Deleted")

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.refresh_table()

    def _open_bar(self, mode: str, placeholder: str, value: str = "") -> None:
        self.mode = mode
        self.query_one("#bar").remove_class("hidden")
        inp = self.query_one(Input)
        inp.placeholder = placeholder
        inp.value = value
        inp.focus()

    def action_add(self) -> None:
        self._open_bar("add", "e.g.  Water plants #home every mon,thu at 9am   (Enter to add, Esc to cancel)")

    def action_filter(self) -> None:
        self._open_bar("filter", "Filter by text or #tag (empty to clear)", self.filter_text)

    def action_cancel(self) -> None:
        self.mode = None
        self.query_one("#bar").add_class("hidden")
        self.query_one(DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if self.mode == "add" and value:
            try:
                r = self.service.add(value)
            except ScheduleError as e:
                self.notify(str(e), severity="error", timeout=6)
                return
            self.notify(f"Added #{r.id} {r.title} · {r.schedule_label}", timeout=4)
        elif self.mode == "filter":
            self.filter_text = value
        self.action_cancel()
        self.refresh_table()
