from datetime import timedelta

import pytest

from nudge.config import Settings
from nudge.hotkey import parse, pretty
from nudge.scratch import ScratchPad, parse_line, split_label_value, summary

from .conftest import NOW


@pytest.mark.parametrize(
    "text,label,value",
    [
        ("INB exported 1,240", "INB exported", "1,240"),
        ("INB imported: 1,198", "INB imported", "1,198"),
        ("returned 42 units", "returned", "42 units"),
        ("owner = jsmith", "owner", "jsmith"),
        ("disk 85%", "disk", "85%"),
        ("refund $1,200.50", "refund", "$1,200.50"),
        ("error code: 0x80070005", "error code", "0x80070005"),
        ("meeting at 10:30", "", "meeting at 10:30"),
        ("called customer, waiting on logs", "", "called customer, waiting on logs"),
    ],
)
def test_split_label_value(text, label, value):
    assert split_label_value(text) == (label, value)


def test_parse_line_cases_and_search():
    assert parse_line("case 48213").kind == "case" and parse_line("case 48213").case_ref == "48213"
    p = parse_line("Ticket INC-0042 returned 42")
    assert (p.kind, p.case_ref, p.label, p.value) == ("entry", "INC-0042", "returned", "42")
    assert parse_line("Case notes look fine").kind == "note"  # "case" as a plain word
    assert parse_line("/482").kind == "search" and parse_line("/482").query == "482"
    assert parse_line("   ").kind == "empty"


def test_scratchpad_groups_and_summary(svc):
    pad = ScratchPad(svc.engine)
    entry, case = pad.add("case 48213", now=NOW)
    assert entry is None and case.ref == "48213"
    pad.add("INB exported 1,240", case="48213", now=NOW)
    pad.add("INB imported 1,198", case="48213", now=NOW + timedelta(minutes=1))
    pad.add("INB returned: 42", case="48213", now=NOW + timedelta(minutes=2))
    pad.add("waiting on customer logs", case="48213", now=NOW + timedelta(minutes=3))
    pad.add("VPN reset done", now=NOW + timedelta(minutes=5))  # no case: today's notes
    groups = pad.groups()
    assert [g.title for g in groups] == ["Notes", "Case 48213"]
    text = summary(groups[1])
    assert text == (
        "Case 48213 (Nov 24, 2026)\n"
        "INB exported: 1,240\n"
        "INB imported: 1,198\n"
        "INB returned: 42\n"
        "- waiting on customer logs"
    )
    assert [g.title for g in pad.groups(query="48213")] == ["Case 48213"]
    assert [e.value for e in pad.entries(query="vpn")] == ["VPN reset done"]
    assert pad.group_for("48213").case.ref == "48213"
    assert pad.group_for(None, NOW.date()).entries[0].value == "VPN reset done"

    e = pad.entries(case="48213")[0]
    gone = pad.delete(e.id)
    assert len(pad.entries(case="48213")) == 3
    pad.restore(gone)
    assert len(pad.entries(case="48213")) == 4
    pad.update(pad.entries(case="48213")[0].id, "INB returned 40")
    assert any(x.value == "40" for x in pad.entries(case="48213"))
    pad.delete_case("48213")
    assert pad.entries(case="48213") == []


def test_hotkey_parse_and_setting():
    assert parse("win+shift+n") == (0x0008 | 0x0004, ord("N"))
    assert parse("ctrl+alt+f9") == (0x0002 | 0x0001, 0x78)
    assert pretty("win+shift+n") == "Win+Shift+N"
    with pytest.raises(ValueError):
        parse("n")
    with pytest.raises(ValueError):
        parse("hyper+n")
    s = Settings()
    s.set("pad_hotkey", "Ctrl + Alt + N")
    assert s.pad_hotkey == "ctrl+alt+n"
    with pytest.raises(ValueError):
        s.set("pad_hotkey", "banana")


def test_cli_note_and_notes(isolated_home):
    from typer.testing import CliRunner

    from nudge.cli import app

    r = CliRunner()
    assert "case 48213" in r.invoke(app, ["note", "INB", "exported", "1,240", "-c", "48213"]).output
    r.invoke(app, ["note", "INB returned 42", "-c", "48213"])
    out = r.invoke(app, ["notes", "--copy"]).output
    assert "INB exported: 1,240" in out and "INB returned: 42" in out
    assert "No notes" in r.invoke(app, ["notes", "-s", "nothing-here"]).output
