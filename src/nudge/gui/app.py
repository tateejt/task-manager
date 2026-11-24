"""The Nudge desktop window (Flet), in a quiet, calculator-like style.

One input line at the top reads what you type ("Water plants every mon at 9am") and shows what it
understood on the right, like a calculator result. Enter adds it. Underneath is a plain list of what's
next; click a row to edit it in place. There are no pop-up dialogs.

It uses the same database, schedule parser and notifier as the command line. While the window (or its
tray icon) is open it also sends notifications, unless a separate ``nudge run`` already is.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
from collections.abc import Callable
from datetime import datetime, timedelta

import flet as ft

from .. import __version__
from ..config import Settings, data_dir, db_path
from ..models import Reminder
from ..notify import Action, asset_path, make_notifier
from ..runner import SingleInstance, tick
from ..schedule import PRIORITY_LABEL, ScheduleError, explain, extract_inline_meta
from ..service import AlreadyDone, NotFound, NothingToUndo, ReminderService
from .presenter import (
    Card,
    Parsed,
    clock,
    next_up,
    parse_input,
    sections,
    today,
    undo_message,
)
from .theme import DARK, FONTS, LIGHTS, Palette, pick

TABS = [("today", "Today"), ("upcoming", "Upcoming"), ("all", "All"), ("history", "History")]
SNOOZES = [
    (10, "10 minutes"),
    (30, "30 minutes"),
    (60, "1 hour"),
    (180, "3 hours"),
    ("tomorrow", "Tomorrow 9 AM"),
]
WEIGHTS = {"regular": "Inter", "medium": "Inter Medium", "semi": "Inter SemiBold"}


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _no_border():
    return ft.NoInputBorder() if hasattr(ft, "NoInputBorder") else ft.InputBorder.NONE


def _safe(handler: Callable) -> Callable:
    """Hover events can still arrive for rows a refresh just replaced (Flet freezes those): ignore them."""

    def run(e) -> None:
        with contextlib.suppress(RuntimeError):
            handler(e)

    return run


def _hovering(e) -> bool:
    return str(getattr(e, "data", "")).lower() == "true"


class NudgeWindow:
    def __init__(self, page: ft.Page, service: ReminderService, *, minimized: bool = False):
        self.page = page
        self.service = service
        self.minimized = minimized
        self.tab = "today"
        self.settings_open = False
        self.query = ""
        self.editor: InlineEditor | None = None
        self.parsed = Parsed("empty")
        self.flash_token = 0
        self.actions: queue.Queue[Action] = queue.Queue()
        self.lock = SingleInstance()
        self.owns_runner = False
        self.tray = None
        self.p: Palette = DARK

    # ------------------------------------------------------------------ text helpers

    def text(self, value: str, size: float = 14, tone: str = "text", weight: str = "regular",
             mono: bool = False, **kw) -> ft.Text:  # fmt: skip
        family = ("Mono Medium" if weight != "regular" else "Mono") if mono else WEIGHTS[weight]
        color = kw.pop("color", None) or self.p.tone(tone)
        return ft.Text(value, size=size, color=color, font_family=family, **kw)

    def link(self, label: str, on_click: Callable, tone: str = "dim", size: float = 12,
             mono: bool = False) -> ft.Control:  # fmt: skip
        t = self.text(label, size=size, tone=tone, mono=mono, weight="medium")
        box = ft.Container(t, padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=6,
                           on_click=lambda _: on_click())  # fmt: skip

        def hover(e) -> None:
            box.bgcolor = self.p.raised if _hovering(e) else None
            self._refresh(box)

        box.on_hover = _safe(hover)
        return box

    def icon_button(self, icon, on_click: Callable | None, tooltip: str, size: int = 16) -> ft.Control:
        box = ft.Container(ft.Icon(icon, size=size, color=self.p.dim), width=28, height=28,
                           alignment=ft.Alignment.CENTER, border_radius=6, tooltip=tooltip,
                           on_click=(lambda _: on_click()) if on_click else None)  # fmt: skip

        def hover(e) -> None:
            box.bgcolor = self.p.raised if _hovering(e) else None
            self._refresh(box)

        box.on_hover = _safe(hover)
        return box

    def _refresh(self, control: ft.Control) -> None:
        with contextlib.suppress(Exception):  # not mounted yet (or no running page, as in tests)
            control.update()

    # ------------------------------------------------------------------ setup

    def start(self) -> None:
        p = self.page
        p.title = "Nudge"
        p.fonts = dict(FONTS)
        p.padding = 0
        p.spacing = 0
        p.on_keyboard_event = self._on_key
        p.on_platform_brightness_change = lambda _: self.build()
        win = p.window
        if win is not None:
            win.width, win.height = 600, 740
            win.min_width, win.min_height = 460, 480
            win.title_bar_hidden = True
            win.title_bar_buttons_hidden = True
            ico = asset_path("nudge.ico")
            if ico.exists():
                win.icon = str(ico)
        self.build()

        self.owns_runner = self.lock.acquire()
        p.run_task(self._loop)
        self._start_tray()
        if win is not None:
            win.prevent_close = self.tray is not None
            win.on_event = self._on_window_event
            if self.minimized and self.tray is not None:
                win.visible = False
        self.render()

    def build(self) -> None:
        """(Re)build the whole window, e.g. after switching between light and dark."""
        brightness = getattr(self.page, "platform_brightness", None)
        system_dark = None if brightness is None else brightness == ft.Brightness.DARK
        self.p = pick(Settings.load().theme, system_dark)
        p = self.page
        p.bgcolor = self.p.bg
        p.theme_mode = ft.ThemeMode.DARK if self.p.name == "dark" else ft.ThemeMode.LIGHT
        theme = ft.Theme(
            font_family="Inter",
            color_scheme_seed=self.p.accent,
            scaffold_bgcolor=self.p.bg,
            splash_color=ft.Colors.TRANSPARENT,
            highlight_color=ft.Colors.TRANSPARENT,
            hover_color=self.p.raised,
            popup_menu_theme=ft.PopupMenuTheme(
                color=self.p.surface,
                elevation=6,
                label_text_style=ft.TextStyle(size=13, color=self.p.text, font_family="Inter"),
            ),  # fmt: skip
            tooltip_theme=ft.TooltipTheme(
                text_style=ft.TextStyle(size=11, color=self.p.bg),
                decoration=ft.BoxDecoration(bgcolor=self.p.text, border_radius=6),
            ),  # fmt: skip
        )
        p.theme = theme
        p.dark_theme = theme
        if p.window is not None:
            p.window.bgcolor = self.p.bg

        old = getattr(self, "input", None)
        self.input = ft.TextField(
            value=old.value if old is not None else "",
            hint_text="Remind me to…",
            border=_no_border(),
            text_style=ft.TextStyle(size=19, color=self.p.text, font_family="Inter"),
            hint_style=ft.TextStyle(size=19, color=self.p.faint, font_family="Inter"),
            cursor_color=self.p.accent,
            selection_color=ft.Colors.with_opacity(0.3, self.p.accent),
            content_padding=ft.Padding.symmetric(vertical=6),
            autofocus=True,
            expand=True,
            on_change=lambda _: self._on_input(),
            on_submit=lambda _: self.submit(),
        )
        self.result = self.text("", size=15, tone="accent", mono=True, weight="medium",
                                text_align=ft.TextAlign.RIGHT)  # fmt: skip
        self.detail = self.text("", size=11, tone="dim", mono=True, text_align=ft.TextAlign.RIGHT)
        self.tabs = ft.Row(spacing=2)
        self.filter_row = ft.Row(spacing=4)
        self.body = ft.ListView(expand=True, spacing=0, padding=ft.Padding.only(left=10, right=10, bottom=16))
        self.status = ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, expand=True)
        self.hints = self.text("Ctrl+K new · Ctrl+Z undo", size=11, tone="faint", mono=True)

        hairline = ft.Container(height=1, bgcolor=self.p.line)
        p.controls = [
            ft.Column(
                [
                    self.title_bar(),
                    ft.Container(
                        ft.Row(
                            [
                                self.input,
                                ft.Column(
                                    [self.result, self.detail],
                                    spacing=2,
                                    horizontal_alignment=ft.CrossAxisAlignment.END,
                                ),  # fmt: skip
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=16,
                        ),
                        padding=ft.Padding.only(left=22, right=22, top=6, bottom=14),
                    ),
                    hairline,
                    ft.Container(
                        ft.Row(
                            [self.tabs, ft.Container(expand=True), self.filter_row],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),  # fmt: skip
                        padding=ft.Padding.only(left=14, right=14, top=10, bottom=6),
                    ),
                    self.body,
                    hairline,
                    ft.Container(
                        ft.Row([self.status, self.hints], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        padding=ft.Padding.symmetric(horizontal=18, vertical=9),
                    ),
                ],
                spacing=0,
                expand=True,
            )
        ]
        if self.editor is not None:
            self.editor = InlineEditor(self, self.service.get(self.editor.rid)) if self._exists(
                self.editor.rid) else None  # fmt: skip
        self._on_input(render=False)
        self.render()

    def title_bar(self) -> ft.Control:
        glyphs = {"close": "×", "min": "–", "zoom": "+"}
        dots: list[ft.Container] = []
        for key, action in (("close", self._close), ("min", self._minimize), ("zoom", self._zoom)):
            dots.append(
                ft.Container(
                    ft.Text(
                        "",
                        size=9,
                        color="#4D0000",
                        font_family="Inter SemiBold",
                        text_align=ft.TextAlign.CENTER,
                    ),  # fmt: skip
                    width=12,
                    height=12,
                    border_radius=6,
                    bgcolor=LIGHTS[key],
                    alignment=ft.Alignment.CENTER,
                    on_click=lambda _, a=action: a(),
                    data=glyphs[key],
                )
            )
        lights = ft.Container(ft.Row(dots, spacing=8), padding=ft.Padding.symmetric(vertical=6))

        def hover(e) -> None:
            for d in dots:
                d.content.value = d.data if _hovering(e) else ""
            self._refresh(lights)

        lights.on_hover = _safe(hover)
        bar = ft.Row(
            [
                lights,
                ft.Container(
                    self.text("Nudge", size=12, tone="dim", weight="medium"),
                    expand=True,
                    alignment=ft.Alignment.CENTER,
                ),  # fmt: skip
                self.icon_button(ft.Icons.TUNE, self.toggle_settings, "Settings (Ctrl+,)", size=15),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        inner = ft.Container(bar, padding=ft.Padding.only(left=16, right=10, top=8, bottom=2))
        return ft.WindowDragArea(inner, maximizable=True) if hasattr(ft, "WindowDragArea") else inner

    # ------------------------------------------------------------------ background loop

    async def _loop(self) -> None:
        notifier = make_notifier(self.actions) if self.owns_runner else None
        while True:
            settings = Settings.load()
            if notifier is not None:
                before = self.actions.qsize()
                shown = await asyncio.to_thread(tick, self.service, notifier, settings, self.actions)
                if shown or before:
                    self.render()
            await asyncio.sleep(min(settings.poll_seconds, 30))
            if not self.settings_open:
                self.render()  # keep "in 5m" style times fresh

    # ------------------------------------------------------------------ tray and window buttons

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image
        except Exception:  # not installed or no desktop session: just a normal window
            return

        def call(fn: Callable[[], None]) -> Callable:
            async def run() -> None:
                fn()

            return lambda *_: self.page.run_task(run)

        menu = pystray.Menu(
            pystray.MenuItem("Open Nudge", call(self.show_window), default=True),
            pystray.MenuItem("New reminder…", call(lambda: (self.show_window(), self.focus_input()))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Nudge", call(self.quit)),
        )
        try:
            self.tray = pystray.Icon("Nudge", Image.open(asset_path("nudge.png")), "Nudge", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def show_window(self) -> None:
        win = self.page.window
        win.visible = True
        win.minimized = False
        self.page.update()
        self.page.run_task(win.to_front)

    def quit(self) -> None:
        if self.tray is not None:
            self.tray.stop()
        self.lock.release()
        self.page.run_task(self.page.window.destroy)

    def _close(self) -> None:
        if self.page.window is None:
            return
        if self.tray is not None:
            self.page.window.visible = False  # keep reminding from the tray
            self.page.update()
        else:
            self.quit()

    def _minimize(self) -> None:
        if self.page.window is not None:
            self.page.window.minimized = True
            self.page.update()

    def _zoom(self) -> None:
        if self.page.window is not None:
            self.page.window.maximized = not self.page.window.maximized
            self.page.update()

    def _on_window_event(self, e) -> None:
        if e.type == ft.WindowEventType.CLOSE:
            self._close()

    # ------------------------------------------------------------------ keyboard

    def focus_input(self) -> None:
        self.page.run_task(self.input.focus)

    def _on_key(self, e: ft.KeyboardEvent) -> None:
        ctrl = e.ctrl or e.meta
        key = (e.key or "").lower()
        if ctrl and key in ("k", "n"):
            self.focus_input()
        elif ctrl and key == "z" and not (self.input.value or ""):
            self.undo()
        elif ctrl and key == ",":
            self.toggle_settings()
        elif ctrl and key in ("1", "2", "3", "4"):
            self.set_tab(TABS[int(key) - 1][0])
        elif key == "escape":
            if self.editor is not None:
                self.close_editor()
            elif self.settings_open:
                self.toggle_settings()
            elif self.input.value:
                self.input.value = ""
                self._on_input()

    # ------------------------------------------------------------------ the input line

    def _on_input(self, render: bool = True) -> None:
        now = _now()
        self.parsed = parse_input(self.input.value or "", now)
        pr = self.parsed
        query = pr.query if pr.mode == "search" else ""
        self.result.color = self.p.accent
        if pr.mode == "add":
            bits = [pr.detail, *[f"#{t}" for t in pr.tags]]
            if pr.priority is not None and pr.priority != 1:
                bits.append(PRIORITY_LABEL[pr.priority])
            self.result.value, self.detail.value = pr.label, " · ".join(bits)
        elif pr.mode == "search":
            self.result.value, self.detail.value = "search", pr.detail
            self.result.color = self.p.dim
        elif pr.mode == "error":
            self.result.value, self.detail.value = "?", pr.detail
            self.result.color = self.p.red
        elif pr.mode == "incomplete":
            self.result.value, self.detail.value = "", pr.detail
        else:
            self.result.value, self.detail.value = "", "e.g. water plants every mon at 9am"
        self.result.visible = bool(self.result.value)
        if render and query != self.query:
            self.query = query
            self.render()
        else:
            self.query = query
            self._refresh(self.result)
            self._refresh(self.detail)

    def submit(self) -> None:
        pr = self.parsed
        if pr.mode == "search":
            return
        if not pr.ok:
            self.result.value = "?" if pr.mode != "empty" else ""
            self.result.color = self.p.red
            self.page.update()
            self.focus_input()
            return
        try:
            r = self.service.add(pr.title, pr.when, tags=pr.tags, priority=pr.priority)
        except ScheduleError as e:
            self.flash(str(e), warning=True)
            return
        self.input.value = ""
        self._on_input(render=False)
        if self.tab not in ("today", "all") or (self.tab == "today" and r.due_at.date() != _now().date()):
            self.tab = "all"
        self.render()
        self.flash(f"Added {r.title} · {r.schedule_label}", undo=False)
        self.focus_input()

    # ------------------------------------------------------------------ tabs and lists

    def set_tab(self, tab: str) -> None:
        self.tab = tab
        self.settings_open = False
        self.editor = None
        self.render()

    def toggle_settings(self) -> None:
        self.settings_open = not self.settings_open
        self.editor = None
        self.render()

    def render(self) -> None:
        now = _now()
        reminders = self.service.list(include_inactive=True)
        self._render_tabs(reminders, now)
        if self.settings_open:
            items = self.settings_view()
        elif self.tab == "today":
            items = self.today_view(reminders, now)
        elif self.tab == "upcoming":
            items = self.upcoming_view(now)
        elif self.tab == "all":
            items = self.all_view(reminders, now)
        else:
            items = self.history_view(now)
        self.body.controls = items
        self._render_status(reminders, now)
        self.page.update()

    def _render_tabs(self, reminders: list[Reminder], now: datetime) -> None:
        counts = {"today": len(today(reminders, now)), "all": sum(r.active for r in reminders)}
        tabs: list[ft.Control] = []
        for key, label in TABS:
            selected = key == self.tab and not self.settings_open
            row = [self.text(label, size=13, tone="text" if selected else "dim", weight="medium")]
            if counts.get(key):
                row.append(self.text(str(counts[key]), size=11, tone="accent" if selected else "faint",
                                     mono=True))  # fmt: skip
            tabs.append(
                ft.Container(
                    ft.Row(row, spacing=6, tight=True),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                    border_radius=7,
                    bgcolor=self.p.raised if selected else None,
                    on_click=lambda _, k=key: self.set_tab(k),
                )
            )
        if self.settings_open:
            tabs.append(ft.Container(self.text("Settings", size=13, weight="medium"), bgcolor=self.p.raised,
                                     padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                                     border_radius=7))  # fmt: skip
        self.tabs.controls = tabs
        self.filter_row.controls = (
            [self.link(f"{self.query}  ×", self._clear_search, tone="accent", mono=True)]
            if self.query
            else []
        )

    def _clear_search(self) -> None:
        self.input.value = ""
        self._on_input()

    def _render_status(self, reminders: list[Reminder], now: datetime) -> None:
        if getattr(self, "_flashing", False):
            return
        due = sum(1 for r in reminders if r.active and not r.completed_at and r.effective_due <= now)
        left = len(today(reminders, now)) - due
        bits = []
        if due:
            bits.append(self.text(f"{due} due", size=12, tone="red", weight="medium"))
        bits.append(self.text(f"{left} later today" if left else ("All caught up" if not due else ""),
                              size=12, tone="dim"))  # fmt: skip
        dot = ft.Container(width=6, height=6, border_radius=3,
                           bgcolor=self.p.green if self.owns_runner else self.p.faint,
                           tooltip="Sending notifications" if self.owns_runner
                           else "nudge run is sending notifications")  # fmt: skip
        self.status.controls = [dot, *bits]

    def section_label(self, label: str, count: int | None = None, tone: str = "faint") -> ft.Control:
        row = [self.text(label.upper(), size=10.5, tone=tone, weight="semi")]
        if count is not None:
            row.append(self.text(str(count), size=10.5, tone="faint", mono=True))
        return ft.Container(ft.Row(row, spacing=6), padding=ft.Padding.only(left=12, top=16, bottom=6))

    def empty(self, title: str, sub: str) -> ft.Control:
        return ft.Container(
            ft.Column(
                [
                    self.text(title, size=15, weight="medium"),
                    self.text(sub, size=12.5, tone="dim", text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),  # fmt: skip
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.only(top=48, bottom=24, left=24, right=24),
        )

    def today_view(self, reminders: list[Reminder], now: datetime) -> list[ft.Control]:
        cards = today(reminders, now, self.query)
        items: list[ft.Control] = []
        due = [c for c in cards if c.state == "overdue"]
        later = [c for c in cards if c.state != "overdue"]
        if due:
            items.append(self.section_label("Due now", len(due), tone="red"))
            items += self.rows(due)
        if later:
            items.append(self.section_label("Later today", len(later)))
            items += self.rows(later)
        if not cards:
            if not reminders:
                return [self.empty("Nothing here yet",
                                   "Type a reminder above and press Enter.\n"
                                   "Take meds every day at 8am  ·  Stretch every 90 minutes  ·  "
                                   "Pay rent monthly on the 1st")]  # fmt: skip
            items.append(self.empty("Nothing left today", "Enjoy it."))
            upcoming = next_up(reminders, now)
            if upcoming and not self.query:
                items.append(self.section_label("Next up"))
                items += self.rows(upcoming)
        return items

    def all_view(self, reminders: list[Reminder], now: datetime) -> list[ft.Control]:
        items: list[ft.Control] = []
        for name, cards in sections(reminders, now, self.query):
            items.append(self.section_label(name, len(cards), tone="red" if name == "Due now" else "faint"))
            items += self.rows(cards)
        if not items:
            return [self.empty("Nothing matches", "Try another word or #tag.") if self.query else
                    self.empty("No reminders yet", "Type one above and press Enter.")]  # fmt: skip
        return items

    def rows(self, cards: list[Card]) -> list[ft.Control]:
        out: list[ft.Control] = []
        for c in cards:
            out.append(self.row(c))
            if self.editor is not None and self.editor.rid == c.id:
                out.append(self.editor.control)
        return out

    def row(self, c: Card) -> ft.Control:
        tone = self.p.tone(c.tone)
        check = ft.Container(
            width=18,
            height=18,
            border_radius=9,
            border=ft.Border.all(1.5, self.p.faint if c.active else self.p.line),
            alignment=ft.Alignment.CENTER,
            tooltip="Done",
            on_click=(lambda _: self.act(c.id, "done")) if c.active else None,
        )

        def check_hover(e) -> None:
            on = _hovering(e) and c.active
            check.border = ft.Border.all(1.5, self.p.green if on else self.p.faint)
            check.content = ft.Icon(ft.Icons.CHECK, size=12, color=self.p.green) if on else None
            self._refresh(check)

        check.on_hover = _safe(check_hover)

        title: list[ft.Control] = []
        if c.priority >= 2:
            title.append(self.text("!!" if c.priority >= 3 else "!", size=14, mono=True, weight="medium",
                                   tone="red" if c.priority >= 3 else "amber"))  # fmt: skip
        title.append(self.text(c.title, size=14, weight="medium", tone="text" if c.active else "dim",
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True))  # fmt: skip
        sub = [c.schedule, *[f"#{t}" for t in c.tags]]
        if c.streak >= 2:
            sub.append(f"{c.streak} in a row")
        if c.state == "snoozed":
            sub.insert(0, "Snoozed")

        snooze = ft.PopupMenuButton(
            content=ft.Container(
                ft.Icon(ft.Icons.BEDTIME_OUTLINED, size=15, color=self.p.dim),
                width=28,
                height=28,
                alignment=ft.Alignment.CENTER,
            ),  # fmt: skip
            tooltip="Snooze",
            items=[
                ft.PopupMenuItem(content=label, on_click=lambda _, m=m: self.act(c.id, "snooze", m))
                for m, label in SNOOZES
            ],  # fmt: skip
            menu_position=ft.PopupMenuPosition.UNDER,
            border_radius=8,
        )
        actions = ft.Row(
            [
                snooze,
                self.icon_button(
                    ft.Icons.SKIP_NEXT_OUTLINED, lambda: self.act(c.id, "skip"), "Skip this one"
                ),
            ]
            if c.active
            else [self.icon_button(ft.Icons.PLAY_ARROW_OUTLINED, lambda: self.act(c.id, "resume"), "Resume")],
            spacing=0,
            visible=False,
        )
        editing = self.editor is not None and self.editor.rid == c.id
        box = ft.Container(
            ft.Row(
                [
                    check,
                    ft.Column(
                        [
                            ft.Row(title, spacing=6),
                            self.text(
                                "  ·  ".join(sub),
                                size=12,
                                tone="dim",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),  # fmt: skip
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    actions,
                    ft.Container(
                        ft.Text(c.right, size=13, color=tone, font_family="Mono Medium"), tooltip=c.when_exact
                    ),  # fmt: skip
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
            border_radius=9,
            key=ft.ScrollKey(f"r{c.id}"),
            bgcolor=self.p.raised if editing else None,
            on_click=lambda _: self.toggle_editor(c.id),
        )

        def hover(e) -> None:
            on = _hovering(e)
            actions.visible = on
            box.bgcolor = self.p.raised if on or editing else None
            self._refresh(box)

        box.on_hover = _safe(hover)
        return box

    def upcoming_view(self, now: datetime) -> list[ft.Control]:
        items = self.service.agenda(now, now + timedelta(days=14))
        counts: dict[tuple, int] = {}
        for t, r in items:
            counts[(t.date(), r.id)] = counts.get((t.date(), r.id), 0) + 1
        out: list[ft.Control] = []
        shown: set[tuple] = set()
        current = None
        for t, r in items:
            if self.query and self.query.lstrip("#") not in (r.title.lower() + " " + " ".join(r.tag_list)):
                continue
            if t.date() != current:
                current = t.date()
                days = (current - now.date()).days
                name = "Today" if days == 0 else ("Tomorrow" if days == 1 else t.strftime("%A"))
                out.append(self.section_label(f"{name}  {t:%b} {t.day}"))
            key = (t.date(), r.id)
            n = counts[key]
            if n > 4:
                if key in shown:
                    continue
                shown.add(key)
            right = f"{n}× · {r.schedule_label.lower()}" if n > 4 else " ".join("#" + x for x in r.tag_list)
            out.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Container(
                                self.text(
                                    clock(t), size=12.5, mono=True, tone="red" if t <= now else "accent"
                                ),
                                width=74,
                            ),  # fmt: skip
                            self.text(
                                r.title,
                                size=13.5,
                                expand=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),  # fmt: skip
                            self.text(right, size=11.5, tone="faint", mono=True),
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                )
            )
        return out or [self.empty("Nothing in the next two weeks", "Reminders you add show up here by day.")]

    def history_view(self, now: datetime) -> list[ft.Control]:
        st = self.service.stats(now)
        total = st.done_7d + st.missed_7d
        rate = f"{st.done_7d / total * 100:.0f}%" if total else "–"
        out: list[ft.Control] = [
            ft.Container(
                ft.Row(
                    [
                        self.stat(str(st.done_7d), "done", "green"),
                        self.stat(str(st.missed_7d), "missed", "red"),
                        self.stat(rate, "on time", "accent"),
                        self.stat(str(max((b for _, b in st.best), default=0)), "best streak", "amber"),
                    ],
                    spacing=28,
                ),  # fmt: skip
                padding=ft.Padding.only(left=12, top=14, bottom=4),
            ),
            self.section_label("Last 7 days and earlier"),
        ]
        glyph = {"done": ("✓", "green", "Done"), "skipped": ("↷", "amber", "Skipped"),
                 "missed": ("×", "red", "Missed")}  # fmt: skip
        rows = 0
        for c, r in self.service.history(limit=200):
            if self.query and self.query.lstrip("#") not in (r.title.lower() + " " + " ".join(r.tag_list)):
                continue
            g, tone, label = glyph[c.status]
            rows += 1
            out.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Container(
                                self.text(g, size=13, tone=tone, mono=True, weight="medium"), width=18
                            ),
                            self.text(
                                r.title,
                                size=13.5,
                                expand=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),  # fmt: skip
                            self.text(
                                f"{label.lower()} · due {c.due_at:%b} {c.due_at.day} {clock(c.due_at)}",
                                size=11.5,
                                tone="faint",
                                mono=True,
                            ),  # fmt: skip
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                )
            )
        if not rows:
            out.append(self.empty("No history yet", "What you finish, skip or miss shows up here."))
        return out

    def stat(self, value: str, label: str, tone: str) -> ft.Control:
        return ft.Column([self.text(value, size=22, mono=True, tone=tone, weight="medium"),
                          self.text(label, size=11, tone="dim")], spacing=0)  # fmt: skip

    # ------------------------------------------------------------------ inline editor

    def _exists(self, rid: int) -> bool:
        try:
            self.service.get(rid)
            return True
        except NotFound:
            return False

    def toggle_editor(self, rid: int) -> None:
        if self.editor is not None and self.editor.rid == rid:
            self.editor = None
        else:
            self.editor = InlineEditor(self, self.service.get(rid))
        self.render()
        if self.editor is not None:

            async def reveal() -> None:
                await asyncio.sleep(0.05)
                await self.body.scroll_to(scroll_key=f"r{rid}", duration=250)

            self.page.run_task(reveal)

    def close_editor(self) -> None:
        self.editor = None
        self.render()

    def save_edit(self, rid: int, **changes) -> bool:
        try:
            r = self.service.edit(rid, **changes)
        except ScheduleError as e:
            self.flash(str(e), warning=True)
            return False
        self.editor = None
        self.render()
        self.flash(undo_message("edit", r.title), undo=True)
        return True

    def delete(self, rid: int) -> None:
        r = self.service.delete(rid)
        self.editor = None
        self.render()
        self.flash(f"Deleted {r.title}")

    # ------------------------------------------------------------------ actions

    def act(self, rid: int, action: str, minutes=None) -> None:
        now = _now()
        try:
            if action == "done":
                r = self.service.complete(rid, now)
            elif action == "skip":
                r = self.service.skip(rid, now)
            elif action == "snooze":
                if minutes == "tomorrow":
                    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).replace(
                        hour=9
                    )
                    minutes = max(1, int((tomorrow - now).total_seconds() // 60))
                r = self.service.snooze(rid, minutes, now)
            elif action == "reset":
                r = self.service.reset(rid, now)
            elif action in ("pause", "resume"):
                r = self.service.set_active(rid, action == "resume", now)
            else:
                return
        except AlreadyDone as e:
            self.flash(str(e).split(". ")[0], undo=True, warning=True)
            return
        except (NotFound, ScheduleError) as e:
            self.flash(str(e), warning=True)
            return
        if self.editor is not None and self.editor.rid == rid:
            self.editor = None
        self.render()
        self.flash(undo_message(action, r.title), undo=True)

    def undo(self) -> None:
        try:
            action, r = self.service.undo()
        except NothingToUndo:
            self.flash("Nothing to undo")
            return
        self.editor = None
        self.render()
        self.flash(f"Undid {action}: {r.title}")

    def flash(self, message: str, *, undo: bool = False, warning: bool = False) -> None:
        """Show a short message in the status bar (with Undo) for a few seconds."""
        self.flash_token += 1
        token = self.flash_token
        self._flashing = True
        self.last_flash = message
        controls: list[ft.Control] = [
            ft.Container(
                width=6, height=6, border_radius=3, bgcolor=self.p.amber if warning else self.p.accent
            ),
            self.text(
                message,
                size=12,
                tone="amber" if warning else "text",
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            ),  # fmt: skip
        ]
        if undo:
            controls.append(self.link("Undo", self.undo, tone="accent"))
        self.status.controls = controls
        self.page.update()

        async def clear() -> None:
            await asyncio.sleep(6)
            if token == self.flash_token:
                self._flashing = False
                self.render()

        self.page.run_task(clear)

    # ------------------------------------------------------------------ settings

    def settings_view(self) -> list[ft.Control]:
        st = Settings.load()

        def save(key: str, value) -> None:
            s = Settings.load()
            try:
                s.set(key, str(value))
            except (KeyError, ValueError) as e:
                self.flash(str(e), warning=True)
                return
            s.save()
            if key == "theme":
                self.build()
            self.flash("Saved")

        def field(key: str, width: int = 56, hint: str = "") -> ft.Control:
            return ft.TextField(
                value=str(getattr(st, key)),
                hint_text=hint,
                width=width,
                text_align=ft.TextAlign.RIGHT,
                border=_no_border(),
                filled=True,
                bgcolor=self.p.surface,
                border_radius=6,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                text_style=ft.TextStyle(size=13, color=self.p.accent, font_family="Mono"),
                hint_style=ft.TextStyle(size=13, color=self.p.faint, font_family="Mono"),
                cursor_color=self.p.accent,
                on_blur=lambda e: save(key, e.control.value),
                on_submit=lambda e: save(key, e.control.value),
            )

        def switch(key: str, value: bool, on_change: Callable | None = None) -> ft.Control:
            return ft.Switch(value=value, active_color=self.p.bg, active_track_color=self.p.accent,
                             inactive_track_color=self.p.raised, inactive_thumb_color=self.p.dim,
                             track_outline_color=ft.Colors.TRANSPARENT, scale=0.8,
                             on_change=on_change or (lambda e: save(key, e.control.value)))  # fmt: skip

        def pills(key: str, current: str, options: list[tuple[str, str]]) -> ft.Control:
            return ft.Container(
                ft.Row(
                    [
                        ft.Container(
                            self.text(
                                label, size=12, weight="medium", tone="text" if value == current else "dim"
                            ),
                            bgcolor=self.p.raised if value == current else None,
                            border_radius=5,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                            on_click=lambda _, v=value: save(key, v),
                        )
                        for value, label in options
                    ],
                    spacing=2,
                    tight=True,
                ),  # fmt: skip
                bgcolor=self.p.surface,
                border_radius=7,
                padding=2,
            )

        def unit(label: str) -> ft.Control:
            return ft.Container(self.text(label, size=12, tone="dim"), width=36)

        def line(label: str, control: ft.Control, sub: str = "") -> ft.Control:
            left: list[ft.Control] = [self.text(label, size=13.5)]
            if sub:
                left.append(self.text(sub, size=11.5, tone="dim"))
            return ft.Container(
                ft.Row(
                    [ft.Column(left, spacing=1, expand=True), control],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),  # fmt: skip
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )

        try:
            from .. import startup

            startup_ctrl: ft.Control = switch("", startup.status() is not None,
                                              lambda e: self._toggle_startup(e.control.value))  # fmt: skip
        except RuntimeError:
            startup_ctrl = self.text("Windows only", size=12, tone="faint")

        return [
            self.section_label("Appearance"),
            line(
                "Theme",
                pills("theme", st.theme, [("system", "System"), ("dark", "Dark"), ("light", "Light")]),
            ),
            self.section_label("Notifications"),
            line("Sound", switch("sound", st.sound)),
            line(
                "Remind again every",
                ft.Row([field("nag_minutes"), unit("min")], tight=True),
                "0 = only once",
            ),  # fmt: skip
            line(
                "Stop after",
                ft.Row([field("max_nags"), unit("times")], tight=True),
                "0 = keep going",
            ),  # fmt: skip
            line(
                "Default snooze",
                ft.Row([field("snooze_minutes"), unit("min")], tight=True),
            ),  # fmt: skip
            line(
                "Quiet hours",
                ft.Row(
                    [
                        field("quiet_start", 70, "22:00"),
                        self.text("to", size=12, tone="dim"),
                        field("quiet_end", 70, "07:00"),
                    ],
                    tight=True,
                ),
                "Only urgent reminders get through. Clear both to turn off.",
            ),  # fmt: skip
            self.section_label("System"),
            line("Open Nudge when I sign in", startup_ctrl, "Lives in the tray and keeps reminding you"),
            line(
                "Notifications",
                self.text("this window" if self.owns_runner else "nudge run", size=12, tone="dim", mono=True),
                "Who is sending them right now",
            ),  # fmt: skip
            line(
                "Version", self.text(f"nudge {__version__}", size=12, tone="dim", mono=True), str(data_dir())
            ),
        ]

    def _toggle_startup(self, on: bool) -> None:
        from .. import startup

        try:
            if on:
                startup.install(app=True)
                self.flash("Nudge will open in the tray when you sign in")
            else:
                startup.uninstall()
                self.flash("Nudge won't start at sign-in")
        except Exception as e:
            self.flash(f"Couldn't change startup: {e}", warning=True)


class InlineEditor:
    """Edit a reminder in place, under its row. The schedule is checked as you type."""

    def __init__(self, app: NudgeWindow, r: Reminder):
        self.app = app
        self.rid = r.id
        self.original_when = r.when_text
        self.active = r.active
        self.priority = r.priority
        self.confirm_delete = False
        p = app.p

        def field(value: str, hint: str, *, size: float = 14, mono: bool = False, tone: str = "text",
                  **kw) -> ft.TextField:  # fmt: skip
            family = "Mono" if mono else "Inter"
            return ft.TextField(
                value=value,
                hint_text=hint,
                border=_no_border(),
                dense=True,
                content_padding=ft.Padding.symmetric(vertical=4),
                text_style=ft.TextStyle(size=size, color=p.tone(tone), font_family=family),
                hint_style=ft.TextStyle(size=size, color=p.faint, font_family=family),
                cursor_color=p.accent,
                **kw,
            )

        self.title = field(r.title, "Title", size=15, on_change=lambda _: self.check(), expand=True)
        self.when = field(r.when_text, "When · every day at 9am", mono=True, size=13, tone="accent",
                          on_change=lambda _: self.check(), expand=True)  # fmt: skip
        self.when_result = app.text("", size=11.5, tone="dim", mono=True)
        self.tags = field(" ".join(f"#{t}" for t in r.tag_list), "#tags", size=13, tone="dim", mono=True,
                          expand=True)  # fmt: skip
        self.notes = field(r.notes, "Notes · shown in the notification", size=13, tone="dim", multiline=True,
                           min_lines=1, max_lines=3, expand=True)  # fmt: skip
        self.priority_row = ft.Row(spacing=2, tight=True)
        self.delete_link = ft.Container()
        self.save_btn = ft.Container(app.text("Save", size=12.5, weight="semi", color=p.bg),
                                     bgcolor=p.accent, border_radius=6,
                                     padding=ft.Padding.symmetric(horizontal=14, vertical=6),
                                     on_click=lambda _: self.save())  # fmt: skip

        def label(text: str) -> ft.Control:
            return ft.Container(app.text(text, size=11, tone="faint", mono=True), width=58)

        self.control = ft.Container(
            ft.Column(
                [
                    ft.Row([label("title"), self.title], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([label("when"), self.when], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([label(""), self.when_result]),
                    ft.Row([label("tags"), self.tags], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([label("notes"), self.notes], vertical_alignment=ft.CrossAxisAlignment.START),
                    ft.Row(
                        [label("priority"), self.priority_row],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=1, bgcolor=p.line, margin=ft.Margin.symmetric(vertical=4)),
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    app.link("Skip once", lambda: app.act(self.rid, "skip")),
                                    app.link("Back on schedule", lambda: app.act(self.rid, "reset")),
                                    app.link(
                                        "Resume" if not self.active else "Pause",
                                        lambda: app.act(self.rid, "resume" if not self.active else "pause"),
                                    ),
                                    self.delete_link,
                                ],
                                spacing=0,
                                tight=True,
                            ),
                            ft.Row(
                                [app.link("Cancel", app.close_editor), self.save_btn], spacing=4, tight=True
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=p.surface,
            border=ft.Border.all(1, p.line),
            border_radius=10,
            padding=ft.Padding.only(left=16, right=12, top=10, bottom=8),
            margin=ft.Margin.only(left=8, right=8, top=2, bottom=8),
        )
        self._draw_priority()
        self._draw_delete()
        self.check(initial=True)

    def _draw_priority(self) -> None:
        app, p = self.app, self.app.p
        tones = {0: "dim", 1: "text", 2: "amber", 3: "red"}
        self.priority_row.controls = [
            ft.Container(
                app.text(name, size=12, weight="medium", tone=tones[k] if k == self.priority else "faint"),
                bgcolor=p.raised if k == self.priority else None,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=9, vertical=3),
                on_click=lambda _, k=k: self.set_priority(k),
            )
            for k, name in PRIORITY_LABEL.items()
        ]

    def set_priority(self, k: int) -> None:
        self.priority = k
        self._draw_priority()
        self.app.page.update()

    def _draw_delete(self) -> None:
        self.delete_link.content = self.app.link(
            "Click again to delete" if self.confirm_delete else "Delete", self.delete, tone="red"
        )

    def delete(self) -> None:
        if not self.confirm_delete:
            self.confirm_delete = True
            self._draw_delete()
            self.app.page.update()
            return
        self.app.delete(self.rid)

    def check(self, initial: bool = False) -> bool:
        when = (self.when.value or "").strip()
        ok = bool(extract_inline_meta(self.title.value or "").title)
        try:
            sched, times = explain(when, _now(), 1)
        except (ScheduleError, ValueError) as e:
            self.when_result.value = str(e).split(". ")[0][:48]
            self.when_result.color = self.app.p.red
            ok = False
        else:
            self.when_result.value = f"{sched.label} · next {times[0]:%a} {clock(times[0])}"
            self.when_result.color = self.app.p.dim
        self.save_btn.opacity = 1 if ok else 0.4
        self.save_btn.disabled = not ok
        if not initial:
            self.app.page.update()
        return ok

    def save(self) -> bool:
        if not self.check(initial=True):
            self.app.page.update()
            return False
        meta = extract_inline_meta(self.title.value or "")
        tags = [t.lstrip("#") for t in (self.tags.value or "").replace(",", " ").split()] + meta.tags
        when = (self.when.value or "").strip()
        return self.app.save_edit(
            self.rid,
            title=meta.title,
            when=when if when != self.original_when else None,
            tags=tags,
            priority=meta.priority if meta.priority is not None else self.priority,
            notes=(self.notes.value or "").strip(),
        )


def run(*, minimized: bool = False, web_port: int | None = None) -> None:
    """Open the Nudge window. ``web_port`` serves it in a browser instead (used for screenshots/tests)."""
    service = ReminderService(db_path())

    def main(page: ft.Page) -> None:
        NudgeWindow(page, service, minimized=minimized).start()

    assets = str(asset_path("nudge.png").parent)
    if web_port:
        no_cdn = os.environ.get("NUDGE_WEB_CDN") != "1"
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=web_port, no_cdn=no_cdn, assets_dir=assets)
    else:
        ft.run(main, assets_dir=assets)
