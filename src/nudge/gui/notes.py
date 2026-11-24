"""The scratchpad tab of the desktop window, and the small always-on-top pad the hotkey opens.

Kept as a mixin so ``app.py`` stays about reminders. It relies on the window's helpers
(``text``, ``link``, ``icon_button``, ``flash``, ``section_label``, ``empty``, ``render``, ``p``...).
"""

from __future__ import annotations

from datetime import date, datetime

import flet as ft

from ..clip import set_text
from ..hotkey import GlobalHotkey, pretty
from ..models import Entry
from ..scratch import Group, ParsedLine, ScratchPad, day_label, parse_line, summary

PAD_SIZE = (440, 600)


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

    def notes_input(self) -> str:
        """Update the live result for a scratchpad line. Returns the search query (or "")."""
        p = self.note_parsed = parse_line(self.input.value or "")
        where = f"case {p.case_ref or self.case_ref}" if (p.case_ref or self.case_ref) else "today's notes"
        self.result.color = self.p.accent
        if p.kind == "entry":
            self.result.value, self.detail.value = f"{p.label} = {p.value}", f"Enter saves to {where}"
        elif p.kind == "note":
            self.result.value, self.detail.value = "note", f"Enter saves to {where}"
            self.result.color = self.p.dim
        elif p.kind == "case":
            self.result.value, self.detail.value = f"case {p.case_ref}", "Enter switches case"
        elif p.kind == "search":
            self.result.value, self.detail.value = "search", "notes and cases"
            self.result.color = self.p.dim
        else:
            self.result.value = ""
            self.detail.value = "INB exported 1,240  ·  case 48213"
        return p.query if p.kind == "search" else ""

    def notes_submit(self) -> None:
        p = self.note_parsed
        if p.kind in ("empty", "search"):
            return
        entry, case = self.pad.add(self.input.value or "", case=self.case_ref)
        if case is not None:
            self.case_ref = case.ref
        self.input.value = ""
        self._on_input(render=False)
        self.render()
        if entry is None:
            self.flash(f"Now on case {case.ref}" if case else "Saved")
        self.focus_input()

    # ------------------------------------------------------------------ views

    def current_group(self) -> Group | None:
        return self.pad.group_for(self.case_ref, date.today())

    def notes_view(self, now: datetime) -> list[ft.Control]:
        out: list[ft.Control] = []
        current = None if self.query else self.current_group()
        if not self.query:
            out.append(self.notes_header(current))
            if current:
                out += [self.entry_row(e) for e in current.entries]
            else:
                out.append(self.notes_hint())
        others = [g for g in self.pad.groups(query=self.query) if current is None or g.key != current.key]
        if others:
            out.append(self.section_label("Matches" if self.query else "Earlier", len(others)))
            for g in others[:40]:
                out += self.group_block(g, now)
        elif self.query:
            out.append(self.empty("Nothing matches", "Search looks at labels, values and case numbers."))
        return out

    def notes_header(self, g: Group | None) -> ft.Control:
        title = f"Case {self.case_ref}" if self.case_ref else "Today's notes"
        right: list[ft.Control] = []
        if g is not None:
            right.append(self.link("Copy all", lambda: self.copy(summary(g), "summary"), tone="accent"))
        if self.case_ref:
            right.append(self.link("Close case", self.close_case))
        return ft.Container(
            ft.Row(
                [
                    self.text(
                        title.upper(), size=10.5, tone="accent" if self.case_ref else "faint", weight="semi"
                    ),
                    ft.Container(expand=True),
                    *right,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),  # fmt: skip
            padding=ft.Padding.only(left=12, right=2, top=12, bottom=4),
        )

    def notes_hint(self) -> ft.Control:
        lines = [
            ("case 48213", "start or switch to a case"),
            ("INB exported 1,240", "saves  INB exported = 1,240"),
            ("returned: 42", "label: value works too"),
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
            padding=ft.Padding.only(left=12, top=8, bottom=8),
        )

    def entry_row(self, e: Entry, compact: bool = False) -> ft.Control:
        if e.label:
            left = self.text(e.label, size=12.5 if compact else 13.5, tone="dim", max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS, expand=True)  # fmt: skip
            right = self.text(e.value, size=12.5 if compact else 13.5, mono=True, weight="medium",
                              tone="accent", selectable=False)  # fmt: skip
            row = [left, right]
            copy_text, what = e.value, f"{e.label} ({e.value})"
        else:
            row = [self.text(e.value, size=12.5 if compact else 13.5, tone="text", expand=True)]
            copy_text, what = e.value, "note"
        remove = self.icon_button(ft.Icons.CLOSE, lambda: self.delete_entry(e), "Delete", size=13)
        remove.visible = False
        stamp = self.text(e.created_at.strftime("%I:%M %p").lstrip("0"), size=10.5, tone="faint", mono=True)
        stamp.visible = False
        box = ft.Container(
            ft.Row([*row, stamp, remove], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=12, vertical=4 if compact else 7),
            border_radius=8,
            tooltip="Click to copy",
            on_click=lambda _: self.copy(copy_text, what),
        )

        def hover(ev) -> None:
            on = str(getattr(ev, "data", "")).lower() == "true"
            remove.visible = stamp.visible = on
            box.bgcolor = self.p.raised if on else None
            self._refresh(box)

        from .app import _safe

        box.on_hover = _safe(hover)
        return box

    def group_block(self, g: Group, now: datetime) -> list[ft.Control]:
        when = f"{day_label(g.last_at.date(), now.date())} {g.last_at.strftime('%I:%M %p').lstrip('0')}"
        values = sum(1 for e in g.entries if e.label)
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
        rows = [self.entry_row(e, compact=True) for e in g.entries[:8]]
        if len(g.entries) > 8:
            rows.append(ft.Container(self.text(f"+ {len(g.entries) - 8} more", size=11, tone="faint"),
                                     padding=ft.Padding.only(left=12, bottom=4)))  # fmt: skip
        return [head, *rows]

    # ------------------------------------------------------------------ actions

    def open_case(self, ref: str) -> None:
        self.case_ref = ref
        self.input.value = ""
        self._on_input(render=False)
        self.render()
        self.focus_input()

    def close_case(self) -> None:
        self.case_ref = None
        self._on_input(render=False)
        self.render()

    def delete_entry(self, e: Entry) -> None:
        gone = self.pad.delete(e.id)
        self.render()

        def undo() -> None:
            self.pad.restore(gone)
            self.render()
            self.flash("Restored")

        self.flash(f"Deleted {gone.text[:40]}", undo_action=undo)

    def copy_current(self) -> None:
        g = self.current_group()
        if g is not None:
            self.copy(summary(g), "summary")

    def hotkey_label(self, combo: str) -> str:
        return pretty(combo) if combo else "off"
