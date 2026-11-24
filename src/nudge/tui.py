"""Interactive dashboard (Textual)."""

from __future__ import annotations

import contextlib
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from .forms import ReminderDraft, ReminderForm
from .schedule import ScheduleError, next_occurrence
from .service import AlreadyDone, NotFound, NothingToUndo, ReminderService
from .ui import PRIORITY_ICON, PRIORITY_STYLE, due_text, priority_label, relative


class StatsBar(Static):
    pass


class NudgeApp(App):
    TITLE = "Nudge"
    SUB_TITLE = "recurring reminders"
    CSS = """
    Screen { layout: vertical; }
    StatsBar { height: 1; padding: 0 1; color: $text-muted; }
    #table { height: 1fr; }
    #detail { height: auto; min-height: 3; padding: 0 1; border-top: solid $panel-lighten-2; }
    #bar { height: auto; dock: bottom; margin-bottom: 1; }
    #bar Input { width: 1fr; }
    .hidden { display: none; }
    """
    BINDINGS = [
        Binding("a", "add", "New"),
        Binding("e", "edit", "Edit"),
        Binding("d", "done", "Done"),
        Binding("s", "snooze", "Snooze 10m"),
        Binding("k", "skip", "Skip"),
        Binding("u", "undo", "Undo"),
        Binding("r", "reset", "Reset", show=False),
        Binding("p", "toggle_pause", "Pause", show=False),
        Binding("x", "delete", "Delete"),
        Binding("slash", "filter", "Filter"),
        Binding("h", "toggle_all", "Show all"),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, service: ReminderService):
        super().__init__()
        self.service = service
        self.mode: str | None = None  # "filter" while the filter bar is open
        self.filter_text = ""
        self.show_all = False
        self._confirm_delete: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatsBar(id="stats")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="detail")
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
        if not rows:
            self.query_one("#detail", Static).update(
                "[dim]No reminders yet. Press [b]a[/b] to create one.[/]"
                if not self.filter_text
                else "[dim]Nothing matches the filter.[/]"
            )
        else:
            self.show_detail()

    def show_detail(self) -> None:
        rid = self.selected_id()
        detail = self.query_one("#detail", Static)
        if rid is None:
            return
        try:
            r = self.service.get(rid)
        except NotFound:
            return
        now = datetime.now().replace(microsecond=0)
        upcoming = [r.effective_due]
        while r.rule and len(upcoming) < 3:
            nxt = next_occurrence(r.rule, upcoming[-1] if len(upcoming) > 1 else r.due_at)
            if nxt is None:
                break
            upcoming.append(nxt)
        nxt = "  ·  ".join(t.strftime("%a %b %d %H:%M") for t in upcoming)
        state = (
            "paused" if not r.active else ("finished" if r.completed_at else relative(r.effective_due, now))
        )
        notes = f"\n[dim]{r.notes}[/]" if r.notes else ""
        detail.update(
            f"[b]{r.title}[/]  [dim]{r.schedule_label} · {priority_label(r.priority)} priority · "
            f"streak {r.streak} (best {r.best_streak})[/]\n"
            f"[dim]Next:[/] {nxt}   [dim]({state})[/]{notes}"
        )

    def on_data_table_row_highlighted(self) -> None:
        self.show_detail()

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
        try:
            r = fn(rid)
        except AlreadyDone as e:
            self.notify(f"{e}\nPress u to undo the last change.", severity="warning", timeout=6)
            return
        except (NotFound, ScheduleError) as e:
            self.notify(str(e), severity="error", timeout=5)
            return
        when = ""
        if r.is_open:
            when = f" · next {relative(r.effective_due, datetime.now().replace(microsecond=0))}"
        self.notify(f"{verb}: {r.title}{when}   (u to undo)", timeout=4)
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
        def created(draft: ReminderDraft | None) -> None:
            if draft is None:
                return
            try:
                r = self.service.add(draft.title, draft.when, tags=draft.tags, priority=draft.priority,
                                     notes=draft.notes)  # fmt: skip
            except ScheduleError as e:
                self.notify(str(e), severity="error", timeout=6)
                return
            self.notify(f"Added {r.title} · {r.schedule_label}", timeout=4)
            self.refresh_table()
            with contextlib.suppress(Exception):
                table = self.query_one(DataTable)
                table.move_cursor(row=table.get_row_index(str(r.id)))

        self.push_screen(ReminderForm(), created)

    def action_edit(self) -> None:
        rid = self.selected_id()
        if rid is None:
            return
        r = self.service.get(rid)
        draft = ReminderDraft(title=r.title, when=r.when_text or "", tags=r.tag_list, priority=r.priority,
                              notes=r.notes)  # fmt: skip

        def saved(new: ReminderDraft | None) -> None:
            if new is None:
                return
            when = new.when if new.when != (r.when_text or "") else None
            try:
                self.service.edit(rid, title=new.title, when=when, tags=new.tags, priority=new.priority,
                                  notes=new.notes)  # fmt: skip
            except ScheduleError as e:
                self.notify(str(e), severity="error", timeout=6)
                return
            self.notify(f"Updated {new.title}   (u to undo)", timeout=4)
            self.refresh_table()

        self.push_screen(ReminderForm(draft, heading=f"Edit “{r.title}”"), saved)

    def action_undo(self) -> None:
        try:
            action, r = self.service.undo()
        except NothingToUndo:
            self.notify("Nothing to undo.", timeout=3)
            return
        now = datetime.now().replace(microsecond=0)
        self.notify(f"Undid {action} on {r.title} · next {relative(r.effective_due, now)}", timeout=4)
        self.refresh_table()

    def action_reset(self) -> None:
        self._act(self.service.reset, "Back on schedule")

    def action_filter(self) -> None:
        self._open_bar("filter", "Filter by text or #tag (empty to clear)", self.filter_text)

    def action_cancel(self) -> None:
        self.mode = None
        self.query_one("#bar").add_class("hidden")
        self.query_one(DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if self.mode == "filter":
            self.filter_text = value
        self.action_cancel()
        self.refresh_table()
