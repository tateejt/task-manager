"""The scratchpad tab of the desktop window, and the small always-on-top pad the hotkey opens.

Kept as a mixin so ``app.py`` stays about reminders. It relies on the window's helpers
(``text``, ``link``, ``icon_button``, ``flash``, ``section_label``, ``empty``, ``render``, ``p``...).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft

from ..clip import set_text
from ..hotkey import GlobalHotkey, pretty
from ..models import Entry
from ..scratch import QUICK_PREFIX, Group, NotFound, ParsedLine, ScratchPad, day_label, parse_line, summary

PAD_SIZE = (440, 600)
#: Opening the pad after this long without using the current page starts a blank one.
IDLE_NEW_PAGE = timedelta(minutes=30)


class NotesMixin:
    pad: ScratchPad
    case_ref: str | None
    pad_mode: bool
    note_parsed: ParsedLine
    last_copied: str
    hotkey: GlobalHotkey | None
    _normal_size: tuple[float, float] | None

    def init_notes(self) -> None:
        self.pad = ScratchPad(self.service.engine)
        self.case_ref = None
        self.pad_mode = False
        self.note_parsed = ParsedLine("empty")
        self.last_copied = ""
        self.hotkey = None
        self.clipboard = None
        self._normal_size = None
        self.show_earlier = False

    # ------------------------------------------------------------------ clipboard

    def start_clipboard(self) -> None:
        try:
            self.clipboard = ft.Clipboard()
        except Exception:
            self.clipboard = None

    def copy(self, text: str, what: str) -> None:
        self.last_copied = text
        if set_text(text):
            self.flash(f"Copied {what}")
            return
        if self.clipboard is None:
            self.flash(f"Copied {what}")  # no clipboard to reach (tests)
            return

        async def put() -> None:
            try:
                await self.clipboard.set(text)
            except Exception:
                self.flash("Couldn't reach the clipboard. Try again.", warning=True)
            else:
                self.flash(f"Copied {what}")

        self.page.run_task(put)

    # ------------------------------------------------------------------ hotkey and pad mode

    def start_hotkey(self, combo: str) -> None:
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        if not combo:
            return

        def pressed() -> None:
            async def run() -> None:
                self.open_pad()

            self.page.run_task(run)

        self.hotkey = GlobalHotkey(combo, pressed)
        self.hotkey.start()

    def hotkey_status(self, combo: str) -> str:
        if not combo:
            return "off"
        if self.hotkey is None:
            return "not active"
        return "ready" if self.hotkey.ok else self.hotkey.error.lower()

    def open_pad(self) -> None:
        """Show a small, always-on-top window on the Notes tab, ready to type."""
        win = self.page.window
        self.tab = "notes"
        self.settings_open = False
        self.editor = None
        self.show_earlier = False
        g = self.current_group()
        if g is None or datetime.now() - g.last_at > IDLE_NEW_PAGE:
            self.case_ref = None  # been a while: start on a blank page
        if win is not None and not self.pad_mode:
            self._normal_size = (win.width or 600, win.height or 740)
            win.width, win.height = PAD_SIZE
            win.always_on_top = True
        self.pad_mode = True
        if win is not None:
            win.visible = True
            win.minimized = False
        self.render()
        if win is not None and not getattr(self.page, "web", False):
            self.page.run_task(win.to_front)
        self.focus_input()

    def exit_pad(self, hide: bool = False) -> None:
        if not self.pad_mode:
            return
        self.pad_mode = False
        win = self.page.window
        if win is not None:
            win.always_on_top = False
            if self._normal_size:
                win.width, win.height = self._normal_size
            if hide and self.tray is not None:
                win.visible = False
        self.page.update()

    # ------------------------------------------------------------------ the input line in Notes

    def _where(self, p: ParsedLine) -> str:
        ref = p.case_ref or self.case_ref
        if ref and not ref.startswith(QUICK_PREFIX):
            return f"case {ref}"
        return "this page" if ref else "a new page"

    def notes_input(self) -> str:
        """Update the live result for a scratchpad line. Returns the search query (or "")."""
        p = self.note_parsed = parse_line(self.input.value or "")
        g = self.current_group()
        nxt = g.next_empty if g else None
        where = self._where(p)
        self.result.color = self.p.accent
        if p.kind == "entry":
            empty = g and any(e.label.lower() == p.label.lower() and not e.value for e in g.entries)
            self.result.value = f"{p.label} = {p.value}"
            self.detail.value = "Enter fills this field" if empty else f"Enter saves to {where}"
        elif p.kind == "value" and nxt is not None:
            self.result.value, self.detail.value = f"{nxt.label} = {p.value}", "Enter fills the next field"
        elif p.kind in ("note", "value"):
            self.result.value, self.detail.value = "note", f"Enter saves to {where}"
            self.result.color = self.p.dim
        elif p.kind == "template":
            t = self.pad.template(p.value)
            self.result.value = f"template {t.name}" if t else "note"
            self.detail.value = (f"Enter adds {len(t.field_list)} fields" if t
                                 else f"no template called {p.value}")  # fmt: skip
            if not t:
                self.result.color = self.p.dim
        elif p.kind == "save_template":
            labels = [e.label for e in g.entries if e.label] if g else []
            self.result.value = f"save template {p.value}"
            self.detail.value = (
                f"{len(labels)} fields from this page" if labels else "add labeled values first"
            )
            if not labels:
                self.result.color = self.p.red
        elif p.kind == "case":
            self.result.value, self.detail.value = f"case {p.case_ref}", "Enter switches case"
        elif p.kind == "search":
            self.result.value, self.detail.value = "search", "all pages and cases"
            self.result.color = self.p.dim
        else:
            self.result.value = ""
            self.detail.value = (f"next: {nxt.label}" if nxt
                                 else "type anything · INB exported 1,240")  # fmt: skip
        return p.query if p.kind == "search" else ""

    def _ensure_page(self) -> str:
        if self.case_ref is None:
            self.case_ref = self.pad.new_page().ref
        return self.case_ref

    def notes_submit(self) -> None:
        p = self.note_parsed
        text = self.input.value or ""
        if p.kind in ("empty", "search"):
            return
        if p.kind == "save_template":
            g = self.current_group()
            try:
                t = self.pad.save_template(p.value, g.case.ref if g and g.case else "")
            except (ValueError, NotFound) as e:
                self.flash(str(e), warning=True)
                return
            self.flash(f"Saved template {t.name} · type “t {t.name.lower()}” to use it")
        elif p.kind == "template" and self.pad.template(p.value):
            t = self.pad.apply_template(p.value, self._ensure_page())
            self.flash(f"Started {t.name}: type the values in order")
        else:
            if p.kind == "template":
                text = p.raw  # no such template: keep it as a note
            entry, case = self.pad.add(text, case=None if p.kind == "case" else self._ensure_page()
                                       if not p.case_ref else None)  # fmt: skip
            if case is not None:
                self.case_ref = case.ref
            if entry is None and case is not None:
                self.flash(f"Now on case {case.ref}")
        self.input.value = ""
        self._on_input(render=False)
        self.render()
        self.focus_input()

    # ------------------------------------------------------------------ views

    def current_group(self) -> Group | None:
        return self.pad.group_for(self.case_ref) if self.case_ref else None

    def notes_view(self, now: datetime) -> list[ft.Control]:
        out: list[ft.Control] = []
        if self.query:
            groups = self.pad.groups(query=self.query)
            out.append(self.section_label("Matches", len(groups)))
            for g in groups[:40]:
                out += self.group_block(g, now)
            if not groups:
                out.append(self.empty("Nothing matches", "Search looks at labels, values and case numbers."))
            return out
        current = self.current_group()
        out.append(self.notes_header(current))
        if current:
            nxt = current.next_empty
            out += [self.entry_row(e, is_next=e is nxt) for e in current.entries]
        if current is None or not any(e.value for e in current.entries):
            out.append(self.template_picker())
        if current is None:
            out.append(self.notes_hint())
        earlier = [g for g in self.pad.groups() if current is None or g.key != current.key]
        if earlier:
            label = f"{'Hide' if self.show_earlier else 'Show'} earlier pages ({len(earlier)})"
            out.append(ft.Container(self.link(label, self.toggle_earlier, size=11.5),
                                    padding=ft.Padding.only(left=4, top=14)))  # fmt: skip
            if self.show_earlier:
                for g in earlier[:40]:
                    out += self.group_block(g, now)
        return out

    def page_title(self, g: Group | None) -> str:
        if self.case_ref is None:
            return "New page"
        if not self.case_ref.startswith(QUICK_PREFIX):
            return f"Case {self.case_ref}"
        stamp = g.case.created_at if g and g.case else datetime.now()
        return f"Quick note · {stamp.strftime('%I:%M %p').lstrip('0')}"

    def notes_header(self, g: Group | None) -> ft.Control:
        is_case = bool(self.case_ref) and not self.case_ref.startswith(QUICK_PREFIX)
        right: list[ft.Control] = []
        if g is not None and any(e.value for e in g.entries):
            right.append(self.link("Copy all", lambda: self.copy(summary(g), "summary"), tone="accent"))
        if g is not None and any(e.label for e in g.entries):
            right.append(self.link("Save template", self.prompt_save_template))
        if self.case_ref is not None:
            right.append(self.link("New page", self.new_page))
        title = self.page_title(g)
        return ft.Container(
            ft.Row(
                [
                    self.text(
                        title.upper(),
                        size=10.5,
                        tone="accent" if is_case else "faint",
                        weight="semi",
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    *right,
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),  # fmt: skip
            padding=ft.Padding.only(left=12, right=2, top=12, bottom=4),
        )

    def template_picker(self) -> ft.Control:
        templates = self.pad.templates()
        if not templates:
            return ft.Container()
        chips = []
        for t in templates:
            chip = ft.Container(
                ft.Row(
                    [
                        self.text(t.name, size=12.5, weight="medium"),
                        self.text(str(len(t.field_list)), size=11, tone="faint", mono=True),
                    ],
                    spacing=6,
                    tight=True,
                ),  # fmt: skip
                bgcolor=self.p.surface,
                border=ft.Border.all(1, self.p.line),
                border_radius=7,
                padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                tooltip=" · ".join(t.field_list),
                on_click=lambda _, n=t.name: self.use_template(n),
            )
            chips.append(chip)
        return ft.Container(
            ft.Column(
                [
                    self.text("START FROM A TEMPLATE", size=10, tone="faint", weight="semi"),
                    ft.Row(chips, spacing=6, wrap=True),
                ],
                spacing=8,
            ),  # fmt: skip
            padding=ft.Padding.only(left=12, top=10, bottom=6),
        )

    def notes_hint(self) -> ft.Control:
        lines = [
            ("anything at all", "a plain note"),
            ("INB exported 1,240", "a label and a value"),
            ("case 48213", "start or switch to a case"),
            ("save template INB", "reuse this page's labels"),
            ("/48213", "search everything"),
        ]
        return ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(self.text(a, size=12.5, mono=True, tone="accent"), width=190),
                            self.text(b, size=12, tone="dim"),
                        ]
                    )
                    for a, b in lines
                ],
                spacing=8,
            ),  # fmt: skip
            padding=ft.Padding.only(left=12, top=10, bottom=8),
        )

    def entry_row(self, e: Entry, compact: bool = False, is_next: bool = False) -> ft.Control:
        size = 12.5 if compact else 13.5
        if e.label and e.value:
            row = [self.text(e.label, size=size, tone="dim", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                             expand=True),
                   self.text(e.value, size=size, mono=True, weight="medium", tone="accent")]  # fmt: skip
            on_click = lambda _: self.copy(e.value, f"{e.label} ({e.value})")  # noqa: E731
            tip = "Click to copy"
        elif e.label:  # an empty template field
            row = [self.text(e.label, size=size, tone="text" if is_next else "dim", expand=True),
                   self.text("next" if is_next else "—", size=11 if is_next else size, mono=True,
                             tone="accent" if is_next else "faint")]  # fmt: skip
            on_click = lambda _: self.prefill(e.label + " ")  # noqa: E731
            tip = "Click to fill"
        else:
            row = [self.text(e.value, size=size, tone="text", expand=True)]
            on_click = lambda _: self.copy(e.value, "note")  # noqa: E731
            tip = "Click to copy"
        remove = self.icon_button(ft.Icons.CLOSE, lambda: self.delete_entry(e), "Delete", size=13)
        remove.visible = False
        box = ft.Container(
            ft.Row([*row, remove], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=12, vertical=4 if compact else 7),
            border_radius=8,
            bgcolor=self.p.surface if is_next else None,
            tooltip=tip,
            on_click=on_click,
        )

        def hover(ev) -> None:
            on = str(getattr(ev, "data", "")).lower() == "true"
            remove.visible = on
            box.bgcolor = self.p.raised if on else (self.p.surface if is_next else None)
            self._refresh(box)

        from .app import _safe

        box.on_hover = _safe(hover)
        return box

    def group_block(self, g: Group, now: datetime) -> list[ft.Control]:
        when = f"{day_label(g.last_at.date(), now.date())} {g.last_at.strftime('%I:%M %p').lstrip('0')}"
        values = sum(1 for e in g.entries if e.label and e.value)
        actions = [self.link("Copy", lambda: self.copy(summary(g), "summary"), tone="accent")]
        if g.case:
            actions.insert(0, self.link("Open", lambda: self.open_case(g.case.ref)))
        head = ft.Container(
            ft.Row(
                [
                    self.text(g.title, size=13, weight="medium"),
                    self.text(
                        f"{when} · {values} value{'s' if values != 1 else ''}",
                        size=11,
                        tone="faint",
                        mono=True,
                        expand=True,
                    ),
                    *actions,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),  # fmt: skip
            padding=ft.Padding.only(left=12, right=2, top=10, bottom=2),
        )
        shown = [e for e in g.entries if e.value][:8]
        rows = [self.entry_row(e, compact=True) for e in shown]
        extra = sum(1 for e in g.entries if e.value) - len(shown)
        if extra > 0:
            rows.append(ft.Container(self.text(f"+ {extra} more", size=11, tone="faint"),
                                     padding=ft.Padding.only(left=12, bottom=4)))  # fmt: skip
        return [head, *rows]

    # ------------------------------------------------------------------ actions

    def prefill(self, text: str) -> None:
        self.input.value = text
        self._on_input()
        self.focus_input()

    def prompt_save_template(self) -> None:
        self.prefill("save template ")

    def use_template(self, name: str) -> None:
        t = self.pad.apply_template(name, self._ensure_page())
        self._on_input(render=False)
        self.render()
        self.flash(f"Started {t.name}: type the values in order")
        self.focus_input()

    def new_page(self) -> None:
        self.case_ref = None
        self.input.value = ""
        self._on_input(render=False)
        self.render()
        self.focus_input()

    def toggle_earlier(self) -> None:
        self.show_earlier = not self.show_earlier
        self.render()

    def open_case(self, ref: str) -> None:
        self.case_ref = ref
        self.show_earlier = False
        self.input.value = ""
        self._on_input(render=False)
        self.render()
        self.focus_input()

    def close_case(self) -> None:
        self.new_page()

    def delete_entry(self, e: Entry) -> None:
        gone = self.pad.delete(e.id)
        self.render()

        def undo() -> None:
            self.pad.restore(gone)
            self.render()
            self.flash("Restored")

        self.flash(f"Deleted {gone.text[:40] or gone.label}", undo_action=undo)

    def copy_current(self) -> None:
        g = self.current_group()
        if g is not None and any(e.value for e in g.entries):
            self.copy(summary(g), "summary")

    def hotkey_label(self, combo: str) -> str:
        return pretty(combo) if combo else "off"
