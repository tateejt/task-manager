"""The Nudge desktop window (Flet).

It uses the same database, schedule parser and notifier as the command line. While the window
(or its tray icon) is open it also runs the reminder loop, unless a separate `nudge run` already is.
"""

from __future__ import annotations

import asyncio
import os
import queue
from collections.abc import Callable
from datetime import datetime, timedelta

import flet as ft

from ..config import Settings, db_path
from ..notify import Action, asset_path, make_notifier
from ..runner import SingleInstance, tick
from ..schedule import (
    EXAMPLES,
    PRIORITY_LABEL,
    REPEAT_CHOICES,
    ScheduleError,
    build_expression,
    explain,
    extract_inline_meta,
    schedule_in_title,
)
from ..service import AlreadyDone, NotFound, NothingToUndo, ReminderService
from .presenter import PRIORITY_COLOURS, Card, all_tags, greeting, sections, undo_message

ACCENT = "#6366F1"
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
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
DATE_HINTS = {"once": "tomorrow, friday, dec 3", "monthly": "1st, 15th, last", "yearly": "mar 14"}


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def soft(colour: str, opacity: float = 0.12) -> str:
    return ft.Colors.with_opacity(opacity, colour)


class NudgeWindow:
    def __init__(self, page: ft.Page, service: ReminderService, *, minimized: bool = False):
        self.page = page
        self.service = service
        self.minimized = minimized
        self.query = ""
        self.view = 0
        self.actions: queue.Queue[Action] = queue.Queue()
        self.lock = SingleInstance()
        self.owns_runner = False
        self.tray = None

    # ------------------------------------------------------------------ setup

    def start(self) -> None:
        p = self.page
        p.title = "Nudge"
        p.theme = ft.Theme(color_scheme_seed=ACCENT)
        p.dark_theme = ft.Theme(color_scheme_seed=ACCENT)
        p.theme_mode = ft.ThemeMode.SYSTEM
        p.padding = 0
        p.spacing = 0
        p.on_keyboard_event = self._on_key
        win = p.window
        if win is not None:
            win.width, win.height = 1080, 760
            win.min_width, win.min_height = 720, 520
            ico = asset_path("nudge.ico")
            if ico.exists():
                win.icon = str(ico)

        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=88,
            group_alignment=-0.85,
            leading=ft.Container(
                ft.Image(src="nudge.png", width=40, height=40),
                padding=ft.Padding.only(top=18, bottom=12),
            ),
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                    selected_icon=ft.Icons.NOTIFICATIONS,
                    label="Reminders",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.CALENDAR_MONTH_OUTLINED,
                    selected_icon=ft.Icons.CALENDAR_MONTH,
                    label="Upcoming",
                ),
                ft.NavigationRailDestination(icon=ft.Icons.HISTORY, label="History"),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS, label="Settings"
                ),
            ],  # fmt: skip
            on_change=self._on_nav,
        )
        self.body = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=28, vertical=20))
        p.add(ft.Row([self.rail, ft.VerticalDivider(width=1), self.body], expand=True, spacing=0))
        self.render()

        self.owns_runner = self.lock.acquire()
        p.run_task(self._loop)
        self._start_tray()
        if win is not None:
            win.prevent_close = self.tray is not None
            win.on_event = self._on_window_event
            if self.minimized and self.tray is not None:
                win.visible = False
        p.update()

    # ------------------------------------------------------------------ background loop

    async def _loop(self) -> None:
        notifier = make_notifier(self.actions) if self.owns_runner else None
        while True:
            settings = Settings.load()
            changed = False
            if notifier is not None:
                before = self.actions.qsize()
                shown = await asyncio.to_thread(tick, self.service, notifier, settings, self.actions)
                changed = bool(shown or before)
            if changed or self.view in (0, 1):
                self.render()
            await asyncio.sleep(min(settings.poll_seconds, 30))

    # ------------------------------------------------------------------ tray

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
            pystray.MenuItem("New reminder…", call(lambda: (self.show_window(), self.open_form()))),
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

    def _on_window_event(self, e) -> None:
        if e.type == ft.WindowEventType.CLOSE:
            if self.tray is not None:
                self.page.window.visible = False  # keep reminding from the tray
                self.page.update()
                self.toast("Nudge is still running in the tray")
            else:
                self.quit()

    # ------------------------------------------------------------------ navigation

    def _on_nav(self, e) -> None:
        self.view = e.control.selected_index
        self.render()

    def _on_key(self, e: ft.KeyboardEvent) -> None:
        if e.key in ("N", "n") and (e.ctrl or e.meta):
            self.open_form()
        elif e.key in ("Z", "z") and (e.ctrl or e.meta):
            self.undo()

    def render(self) -> None:
        views = [self.reminders_view, self.upcoming_view, self.history_view, self.settings_view]
        self.body.content = views[self.view]()
        self.page.update()

    # ------------------------------------------------------------------ reminders view

    def header(self, title: str, subtitle: str, *actions: ft.Control) -> ft.Control:
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(title, size=28, weight=ft.FontWeight.W_700),
                        ft.Text(subtitle, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=2,
                    expand=True,
                ),  # fmt: skip
                *actions,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def stat(self, label: str, value: str, colour: str, icon) -> ft.Control:
        return ft.Container(
            ft.Row(
                [
                    ft.Container(
                        ft.Icon(icon, color=colour, size=20),
                        bgcolor=soft(colour),
                        padding=8,
                        border_radius=10,
                    ),  # fmt: skip
                    ft.Column(
                        [
                            ft.Text(value, size=20, weight=ft.FontWeight.W_700),
                            ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=0,
                    ),  # fmt: skip
                ],
                spacing=12,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border_radius=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            expand=True,
        )

    def reminders_view(self) -> ft.Control:
        now = _now()
        reminders = self.service.list(include_inactive=True)
        st = self.service.stats(now)
        best = max((r.streak for r in reminders), default=0)
        due_now = st.overdue
        subtitle = (
            f"{due_now} reminder{'s' if due_now != 1 else ''} need{'s' if due_now == 1 else ''} you now"
            if due_now
            else "You're all caught up"
        )
        search = ft.TextField(
            value=self.query,
            hint_text="Search or #tag",
            prefix_icon=ft.Icons.SEARCH,
            dense=True,
            border_radius=12,
            width=240,
            on_change=self._on_search,
        )
        new_btn = ft.FilledButton("New reminder", icon=ft.Icons.ADD, on_click=lambda _: self.open_form(),
                                  height=44)  # fmt: skip
        tags = all_tags(reminders)
        tag_row = ft.Row(
            [
                ft.Chip(
                    label=ft.Text("All"), selected=not self.query, on_click=lambda _: self._set_query("")
                ),
                *[
                    ft.Chip(
                        label=ft.Text(f"#{t}"),
                        selected=self.query == f"#{t}",
                        on_click=lambda _, t=t: self._set_query(f"#{t}"),
                    )
                    for t in tags
                ],
            ],
            wrap=True,
            spacing=8,
            visible=bool(tags),
        )
        groups = sections(reminders, now, self.query)
        items: list[ft.Control] = []
        for name, cards in groups:
            colour = "#E11D48" if name == "Due now" else ft.Colors.ON_SURFACE_VARIANT
            items.append(
                ft.Container(
                    ft.Text(f"{name}  ·  {len(cards)}", size=13, weight=ft.FontWeight.W_600, color=colour),
                    padding=ft.Padding.only(top=14, bottom=4, left=4),
                )
            )
            items += [self.card(c) for c in cards]
        if not items:
            items.append(self.empty_state(bool(self.query)))
        return ft.Column(
            [
                self.header(f"{greeting(now)} 👋", subtitle, search, new_btn),
                ft.Row(
                    [
                        self.stat("Due now", str(due_now), "#E11D48", ft.Icons.ALARM),
                        self.stat("Today", str(st.due_today), "#F59E0B", ft.Icons.TODAY),
                        self.stat("Done this week", str(st.done_7d), "#16A34A", ft.Icons.TASK_ALT),
                        self.stat(
                            "Best streak",
                            f"{best} 🔥" if best else "0",
                            "#6366F1",
                            ft.Icons.LOCAL_FIRE_DEPARTMENT,
                        ),  # fmt: skip
                    ],
                    spacing=12,
                ),
                tag_row,
                ft.ListView(items, expand=True, spacing=8, padding=ft.Padding.only(bottom=24)),
            ],
            spacing=16,
            expand=True,
        )

    def empty_state(self, filtered: bool) -> ft.Control:
        if filtered:
            text, sub = "Nothing matches", "Try another search or tag."
        else:
            text, sub = (
                "No reminders yet",
                "Create your first one. You can type it like “Water plants every mon at 9”.",
            )
        return ft.Container(
            ft.Column(
                [
                    ft.Text("🔔", size=48),
                    ft.Text(text, size=18, weight=ft.FontWeight.W_600),
                    ft.Text(sub, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER),
                    ft.FilledButton(
                        "New reminder",
                        icon=ft.Icons.ADD,
                        on_click=lambda _: self.open_form(),
                        visible=not filtered,
                    ),  # fmt: skip
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            padding=60,
        )

    def card(self, c: Card) -> ft.Control:
        chips: list[ft.Control] = [
            ft.Container(ft.Text(f"#{t}", size=11, color=ACCENT), bgcolor=soft(ACCENT), border_radius=8,
                         padding=ft.Padding.symmetric(horizontal=8, vertical=2))
            for t in c.tags
        ]  # fmt: skip
        if c.priority >= 2:
            pc = PRIORITY_COLOURS[c.priority]
            chips.insert(0, ft.Container(ft.Text(c.priority_label.capitalize(), size=11, color=pc),
                                         bgcolor=soft(pc), border_radius=8,
                                         padding=ft.Padding.symmetric(horizontal=8, vertical=2)))  # fmt: skip
        if c.streak >= 2:
            chips.append(ft.Text(f"🔥 {c.streak}", size=12))

        snooze = ft.PopupMenuButton(
            icon=ft.Icons.SNOOZE,
            tooltip="Snooze",
            items=[
                ft.PopupMenuItem(content=label, on_click=lambda _, m=m: self.act(c.id, "snooze", m))
                for m, label in [(10, "10 minutes"), (30, "30 minutes"), (60, "1 hour"), (180, "3 hours")]
            ]
            + [
                ft.PopupMenuItem(
                    content="Tomorrow morning", on_click=lambda _: self.act(c.id, "snooze", "tomorrow")
                )
            ],
            disabled=not c.active,
        )
        more = ft.PopupMenuButton(
            icon=ft.Icons.MORE_HORIZ,
            tooltip="More",
            items=[
                ft.PopupMenuItem(
                    content="Edit", icon=ft.Icons.EDIT_OUTLINED, on_click=lambda _: self.open_form(c.id)
                ),
                ft.PopupMenuItem(
                    content="Skip this one",
                    icon=ft.Icons.SKIP_NEXT,
                    on_click=lambda _: self.act(c.id, "skip"),
                ),
                ft.PopupMenuItem(
                    content="Back on schedule",
                    icon=ft.Icons.RESTART_ALT,
                    on_click=lambda _: self.act(c.id, "reset"),
                ),
                ft.PopupMenuItem(
                    content="Resume" if not c.active else "Pause",
                    icon=ft.Icons.PLAY_ARROW if not c.active else ft.Icons.PAUSE,
                    on_click=lambda _: self.act(c.id, "resume" if not c.active else "pause"),
                ),
                ft.PopupMenuItem(),
                ft.PopupMenuItem(
                    content="Delete", icon=ft.Icons.DELETE_OUTLINE, on_click=lambda _: self.confirm_delete(c)
                ),
            ],  # fmt: skip
        )
        done = ft.IconButton(
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            selected_icon=ft.Icons.CHECK_CIRCLE,
            icon_color="#16A34A",
            icon_size=28,
            tooltip="Mark done",
            on_click=lambda _: self.act(c.id, "done"),
            disabled=not c.active,
        )
        overdue = c.state == "overdue"
        return ft.Container(
            ft.Row(
                [
                    ft.Container(width=4, height=56, bgcolor=c.colour, border_radius=4),
                    ft.Container(
                        ft.Text(c.emoji, size=22),
                        width=46,
                        height=46,
                        bgcolor=soft(c.colour),
                        border_radius=23,
                        alignment=ft.Alignment.CENTER,
                    ),  # fmt: skip
                    ft.Column(
                        [
                            ft.Text(
                                c.title,
                                size=16,
                                weight=ft.FontWeight.W_600,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                opacity=0.55 if not c.active else 1,
                            ),  # fmt: skip
                            ft.Row(
                                [
                                    ft.Icon(
                                        ft.Icons.REPEAT if c.recurring else ft.Icons.EVENT,
                                        size=14,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                    ft.Text(c.schedule, size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                    *chips,
                                ],
                                spacing=6,
                                wrap=True,
                            ),  # fmt: skip
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Column(
                        [
                            ft.Text(c.when, size=14, weight=ft.FontWeight.W_600, color=c.colour),
                            ft.Text(c.when_exact, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                    done,
                    snooze,
                    more,
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=0, right=8, top=10, bottom=10),
            border_radius=14,
            bgcolor=soft("#E11D48", 0.06) if overdue else ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, soft("#E11D48", 0.35)) if overdue else None,
            on_click=lambda _: self.open_form(c.id),
            ink=True,
        )

    def _on_search(self, e) -> None:
        self.query = e.control.value
        self.render()

    def _set_query(self, q: str) -> None:
        self.query = q
        self.render()

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
            self.toast(str(e), undo=True, warning=True)
            return
        except (NotFound, ScheduleError) as e:
            self.toast(str(e), warning=True)
            return
        self.render()
        self.toast(undo_message(action, r.title), undo=True)

    def undo(self) -> None:
        try:
            action, r = self.service.undo()
        except NothingToUndo:
            self.toast("Nothing to undo")
            return
        self.render()
        self.toast(f"Undid {action}: {r.title}")

    def toast(self, message: str, *, undo: bool = False, warning: bool = False) -> None:
        bar = ft.SnackBar(
            ft.Text(message),
            action="Undo" if undo else None,
            on_action=(lambda _: self.undo()) if undo else None,
            behavior=ft.SnackBarBehavior.FLOATING,
            width=520,
            duration=ft.Duration(seconds=5),
            bgcolor="#B45309" if warning else None,
        )
        self.page.show_dialog(bar)

    def confirm_delete(self, c: Card) -> None:
        def yes(_):
            self.page.pop_dialog()
            self.service.delete(c.id)
            self.render()
            self.toast(f"Deleted {c.title}")

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"Delete “{c.title}”?"),
                content=ft.Text("Its history goes too. This can't be undone."),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.FilledButton("Delete", on_click=yes, bgcolor="#E11D48"),
                ],
            )
        )

    # ------------------------------------------------------------------ new / edit form

    def open_form(self, rid: int | None = None) -> None:
        existing = self.service.get(rid) if rid is not None else None
        form = ReminderDialog(self, existing)
        self.page.show_dialog(form.dialog)

    def save_form(
        self, rid: int | None, title: str, when: str, tags: list[str], priority: int, notes: str
    ) -> bool:
        try:
            if rid is None:
                r = self.service.add(title, when, tags=tags, priority=priority, notes=notes)
                msg = f"Added {r.title} · {r.schedule_label}"
            else:
                old = self.service.get(rid)
                r = self.service.edit(rid, title=title, when=when if when != old.when_text else None, tags=tags,
                                      priority=priority, notes=notes)  # fmt: skip
                msg = f"Updated {r.title}"
        except ScheduleError as e:
            self.toast(str(e), warning=True)
            return False
        self.page.pop_dialog()  # close the form before showing the confirmation
        self.render()
        self.toast(msg, undo=rid is not None)
        return True

    # ------------------------------------------------------------------ other views

    def upcoming_view(self) -> ft.Control:
        now = _now()
        items = self.service.agenda(now, now + timedelta(days=14))
        rows: list[ft.Control] = []
        current = None
        counts: dict[tuple, int] = {}
        for t, r in items:
            counts[(t.date(), r.id)] = counts.get((t.date(), r.id), 0) + 1
        shown: set[tuple] = set()
        for t, r in items:
            if t.date() != current:
                current = t.date()
                label = "Today" if current == now.date() else (
                    "Tomorrow" if current == now.date() + timedelta(days=1) else t.strftime("%A"))  # fmt: skip
                rows.append(
                    ft.Container(
                        ft.Row(
                            [
                                ft.Text(label, size=15, weight=ft.FontWeight.W_700),
                                ft.Text(t.strftime("%b %d"), color=ft.Colors.ON_SURFACE_VARIANT),
                            ]
                        ),  # fmt: skip
                        padding=ft.Padding.only(top=16, bottom=4),
                    )
                )
            key = (t.date(), r.id)
            n = counts[key]
            if n > 4:
                if key in shown:
                    continue
                shown.add(key)
            extra = f"  ·  {n}× that day ({r.schedule_label.lower()})" if n > 4 else ""
            colour = "#E11D48" if t <= now else ACCENT
            rows.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Text(
                                t.strftime("%I:%M %p").lstrip("0"),
                                width=80,
                                color=colour,
                                weight=ft.FontWeight.W_600,
                            ),  # fmt: skip
                            ft.Text(f"{r.title}{extra}", expand=True),
                            ft.Text(" ".join("#" + x for x in r.tag_list), color=ACCENT, size=12),
                        ]
                    ),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                    border_radius=10,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                )
            )
        if not rows:
            rows.append(
                ft.Text("Nothing scheduled in the next two weeks.", color=ft.Colors.ON_SURFACE_VARIANT)
            )
        return ft.Column(
            [self.header("Upcoming", "Everything due in the next two weeks"),
             ft.ListView(rows, expand=True, spacing=6)],
            expand=True,
            spacing=12,
        )  # fmt: skip

    def history_view(self) -> ft.Control:
        rows: list[ft.Control] = []
        style = {"done": ("✓", "#16A34A", "Done"), "skipped": ("↷", "#F59E0B", "Skipped"),
                 "missed": ("✗", "#E11D48", "Missed")}  # fmt: skip
        for c, r in self.service.history(limit=200):
            icon, colour, label = style[c.status]
            rows.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Container(
                                ft.Text(icon, color=colour, weight=ft.FontWeight.W_700),
                                width=32,
                                height=32,
                                border_radius=16,
                                bgcolor=soft(colour),
                                alignment=ft.Alignment.CENTER,
                            ),  # fmt: skip
                            ft.Column(
                                [
                                    ft.Text(r.title, weight=ft.FontWeight.W_600),
                                    ft.Text(
                                        f"{label} · was due {c.due_at:%a %b %d %H:%M}",
                                        size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                            ft.Text(
                                c.at.strftime("%b %d, %H:%M"), color=ft.Colors.ON_SURFACE_VARIANT, size=12
                            ),
                        ],  # fmt: skip
                        spacing=12,
                    ),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                    border_radius=10,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                )
            )
        if not rows:
            rows.append(ft.Text("Nothing yet. Things you finish, skip or miss show up here.",
                                color=ft.Colors.ON_SURFACE_VARIANT))  # fmt: skip
        st = self.service.stats(_now())
        total = st.done_7d + st.missed_7d
        rate = f"{st.done_7d / total * 100:.0f}%" if total else "—"
        return ft.Column(
            [
                self.header("History", "What you did, skipped and missed"),
                ft.Row(
                    [
                        self.stat("Done (7 days)", str(st.done_7d), "#16A34A", ft.Icons.TASK_ALT),
                        self.stat("Missed (7 days)", str(st.missed_7d), "#E11D48", ft.Icons.ALARM_OFF),
                        self.stat("On-time rate", rate, ACCENT, ft.Icons.INSIGHTS),
                    ],
                    spacing=12,
                ),  # fmt: skip
                ft.ListView(rows, expand=True, spacing=6),
            ],
            expand=True,
            spacing=16,
        )

    def settings_view(self) -> ft.Control:
        st = Settings.load()

        def save(key: str, value) -> None:
            s = Settings.load()
            try:
                s.set(key, str(value))
            except (KeyError, ValueError) as e:
                self.toast(str(e), warning=True)
                return
            s.save()
            self.toast("Saved")

        def number(label: str, key: str, help_text: str) -> ft.Control:
            return ft.TextField(label=label, value=str(getattr(st, key)), width=200, helper=help_text,
                                keyboard_type=ft.KeyboardType.NUMBER,
                                on_blur=lambda e: save(key, e.control.value),
                                on_submit=lambda e: save(key, e.control.value))  # fmt: skip

        def group(title: str, *controls: ft.Control) -> ft.Control:
            return ft.Container(
                ft.Column([ft.Text(title, size=16, weight=ft.FontWeight.W_700), *controls], spacing=14),
                padding=20,
                border_radius=14,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            )

        startup_row: list[ft.Control] = []
        try:
            from .. import startup

            current = startup.status()
            startup_row.append(
                ft.Switch(
                    label="Start Nudge when I sign in to Windows",
                    value=current is not None,
                    on_change=lambda e: self._toggle_startup(e.control.value),
                )  # fmt: skip
            )
        except RuntimeError:
            startup_row.append(ft.Text("Starting at sign-in is available on Windows.",
                                       color=ft.Colors.ON_SURFACE_VARIANT))  # fmt: skip

        runner = (
            "This window is sending your notifications."
            if self.owns_runner
            else "Another Nudge (nudge run) is already sending notifications."
        )
        return ft.Column(
            [
                self.header("Settings", "Changes apply right away"),
                ft.ListView(
                    [
                        group(
                            "Notifications",
                            ft.Switch(
                                label="Play a sound",
                                value=st.sound,
                                on_change=lambda e: save("sound", e.control.value),
                            ),
                            ft.Row(
                                [
                                    number("Remind again every (min)", "nag_minutes", "0 = only once"),
                                    number("Stop after (times)", "max_nags", "0 = keep going"),
                                    number("Default snooze (min)", "snooze_minutes", ""),
                                ],
                                spacing=16,
                                wrap=True,
                            ),
                            ft.Text(runner, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ),  # fmt: skip
                        group(
                            "Quiet hours",
                            ft.Text(
                                "No notifications in this window except urgent ones. Leave blank to turn off.",
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.Row(
                                [
                                    ft.TextField(
                                        label="From",
                                        value=st.quiet_start,
                                        width=140,
                                        hint_text="22:00",
                                        on_blur=lambda e: save("quiet_start", e.control.value),
                                    ),
                                    ft.TextField(
                                        label="Until",
                                        value=st.quiet_end,
                                        width=140,
                                        hint_text="07:00",
                                        on_blur=lambda e: save("quiet_end", e.control.value),
                                    ),
                                ],
                                spacing=16,
                            ),
                        ),  # fmt: skip
                        group("Startup", *startup_row),
                    ],
                    spacing=14,
                    expand=True,
                ),
            ],
            expand=True,
            spacing=16,
        )

    def _toggle_startup(self, on: bool) -> None:
        from .. import startup

        try:
            if on:
                startup.install(app=True)
                self.toast("Nudge will open in the tray when you sign in")
            else:
                startup.uninstall()
                self.toast("Nudge won't start at sign-in")
        except Exception as e:
            self.toast(f"Couldn't change startup: {e}", warning=True)


class ReminderDialog:
    """New / edit reminder: type it, or build it with the controls. The preview updates live."""

    def __init__(self, app: NudgeWindow, existing=None):
        self.app = app
        self.rid = existing.id if existing else None
        self.when = ""
        tf = dict(border_radius=10, dense=True)
        self.title = ft.TextField(label="What do you want to be reminded about?", value=existing.title if existing
                                  else "", autofocus=True, on_change=self._title_changed, **tf)  # fmt: skip
        self.quick = ft.TextField(
            label="When (type it)",
            value=(existing.when_text if existing else ""),
            hint_text="e.g. every mon,thu at 9am · weekdays 8:30 · in 20 min",
            prefix_icon=ft.Icons.AUTO_AWESOME,
            on_change=lambda _: self.update(),
            **tf,
        )
        self.repeat = ft.Dropdown(
            label="Repeat",
            value="daily",
            options=[ft.DropdownOption(key=k, text=label) for k, label in REPEAT_CHOICES],
            on_select=lambda _: self.update(),
            width=280,
            dense=True,
            border_radius=10,
        )
        self.days = ft.SegmentedButton(
            segments=[ft.Segment(value=d, label=ft.Text(d.capitalize())) for d in DAY_KEYS],
            selected=[],
            allow_multiple_selection=True,
            allow_empty_selection=True,
            show_selected_icon=False,
            on_change=lambda _: self.update(),
        )
        self.every = ft.TextField(label="Every", value="2", width=90, keyboard_type=ft.KeyboardType.NUMBER,
                                  on_change=lambda _: self.update(), **tf)  # fmt: skip
        self.unit = ft.Dropdown(value="hours", options=[ft.DropdownOption(key="minutes", text="minutes"),
                                                       ft.DropdownOption(key="hours", text="hours")],
                                width=140, dense=True, border_radius=10,
                                on_select=lambda _: self.update())  # fmt: skip
        self.date = ft.TextField(label="Date", width=220, on_change=lambda _: self.update(), **tf)
        self.times = ft.TextField(label="At", hint_text="9am · 8am, 8pm", width=220,
                                  on_change=lambda _: self.update(), **tf)  # fmt: skip
        self.priority = ft.SegmentedButton(
            segments=[
                ft.Segment(value=str(k), label=ft.Text(v.capitalize())) for k, v in PRIORITY_LABEL.items()
            ],
            selected=[str(existing.priority if existing else 1)],
            show_selected_icon=False,
        )
        self.tags = ft.TextField(label="Tags", value=" ".join(existing.tag_list) if existing else "",
                                 hint_text="home health", prefix_icon=ft.Icons.TAG, expand=True, **tf)  # fmt: skip
        self.notes = ft.TextField(label="Notes (shown in the notification)", value=existing.notes if existing
                                  else "", multiline=True, max_lines=3, **tf)  # fmt: skip
        self.preview_title = ft.Text(weight=ft.FontWeight.W_600)
        self.preview_body = ft.Text(size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self.preview = ft.Container(
            ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color="#16A34A"),
                    ft.Column([self.preview_title, self.preview_body], spacing=2, expand=True),
                ]
            ),  # fmt: skip
            padding=14,
            border_radius=12,
        )
        self.builder_note = ft.Text("Or build it:", size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self.builder = ft.Column(
            [
                ft.Row([self.repeat, self.every, self.unit], spacing=12, wrap=True),
                self.days,
                ft.Row([self.date, self.times], spacing=12, wrap=True),
            ],
            spacing=12,
        )
        self.save_btn = ft.FilledButton("Save" if existing else "Add reminder", icon=ft.Icons.CHECK,
                                        on_click=self.save)  # fmt: skip
        self.dialog = ft.AlertDialog(
            title=ft.Text("Edit reminder" if existing else "New reminder", weight=ft.FontWeight.W_700),
            content=ft.Container(
                ft.Column(
                    [
                        self.title,
                        self.quick,
                        self.builder_note,
                        self.builder,
                        self.preview,
                        ft.Text("Priority", size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                        self.priority,
                        ft.Row([self.tags]),
                        self.notes,
                    ],
                    spacing=14,
                    scroll=ft.ScrollMode.AUTO,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                width=600,
            ),
            actions=[ft.TextButton("Cancel", on_click=lambda _: app.page.pop_dialog()), self.save_btn],
            actions_alignment=ft.MainAxisAlignment.END,
            scrollable=True,
        )
        self.update(initial=True)

    # -------------------------------------------------------------- logic

    def schedule_text(self) -> str:
        typed = (self.quick.value or "").strip()
        if typed:
            return typed
        from_title = schedule_in_title(self.title.value or "")
        if from_title:
            return from_title[1]
        return build_expression(
            self.repeat.value or "daily",
            times=self.times.value or "",
            days=list(self.days.selected or []),
            every=self.every.value or 1,
            unit=self.unit.value or "hours",
            date=self.date.value or "",
        )

    def update(self, initial: bool = False) -> None:
        repeat = self.repeat.value or "daily"
        shows = SHOWS.get(repeat, set())
        in_title = not (self.quick.value or "").strip() and bool(schedule_in_title(self.title.value or ""))
        typed = bool((self.quick.value or "").strip()) or in_title
        self.days.visible = "days" in shows
        self.every.visible = "every" in shows
        self.unit.visible = "unit" in shows
        self.date.visible = "date" in shows
        self.date.hint_text = DATE_HINTS.get(repeat, "")
        self.times.visible = "times" in shows
        self.builder.opacity = 0.4 if typed else 1
        self.builder.disabled = typed
        if in_title:
            self.builder_note.value = "Using the schedule in the title. Or set it here instead:"
        elif typed:
            self.builder_note.value = "Using what you typed. Clear it to build it instead:"
        else:
            self.builder_note.value = "Or build it:"
        try:
            when = self.schedule_text()
            sched, times = explain(when, _now(), 3)
        except (ScheduleError, ValueError) as e:
            self.when = ""
            self.preview.bgcolor = soft("#E11D48", 0.1)
            self.preview.content.controls[0] = ft.Icon(ft.Icons.ERROR_OUTLINE, color="#E11D48")
            self.preview_title.value = str(e)
            self.preview_body.value = "Try: " + " · ".join(EXAMPLES[:4])
        else:
            self.when = when
            self.preview.bgcolor = soft("#16A34A", 0.1)
            self.preview.content.controls[0] = ft.Icon(ft.Icons.CHECK_CIRCLE, color="#16A34A")
            self.preview_title.value = sched.label
            nxt = "  ·  ".join(t.strftime("%a %b %d, %I:%M %p").replace(" 0", " ") for t in times)
            understood = ""
            if sched.understood and sched.understood != " ".join(when.lower().split()):
                understood = f"\nUnderstood as “{sched.understood}”"
            self.preview_body.value = f"Next: {nxt}{understood}"
        self.save_btn.disabled = not self.when or not extract_inline_meta(self.title.value or "").title
        if not initial:
            self.app.page.update()

    def _title_changed(self, _e) -> None:
        self.update()

    def save(self, _=None) -> None:
        title = self.title.value or ""
        if not (self.quick.value or "").strip():
            from_title = schedule_in_title(title)
            if from_title:
                title = from_title[0]
        meta = extract_inline_meta(title)
        if not meta.title or not self.when:
            return
        tags = [t.lstrip("#") for t in (self.tags.value or "").replace(",", " ").split()] + meta.tags
        selected = list(self.priority.selected or ["1"])
        priority = meta.priority if meta.priority is not None else int(selected[0])
        self.app.save_form(self.rid, meta.title, self.when, tags, priority, (self.notes.value or "").strip())


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
