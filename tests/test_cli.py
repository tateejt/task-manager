from typer.testing import CliRunner

from nudge.cli import app, parse_duration

runner = CliRunner()


def invoke(*args):
    result = runner.invoke(app, list(args), env={"COLUMNS": "140"})
    assert result.exit_code == 0, result.output
    return result.output


def test_quick_add_list_done_flow():
    out = invoke("add", "Water plants #home", "every", "mon,thu", "at", "9am")
    assert "Added #1 Water plants" in out
    invoke("add", "Stretch every 2 hours")
    out = invoke("list")
    assert "Water plants" in out and "Stretch" in out and "#home" in out
    out = invoke("done", "1")
    assert "Done: Water plants" in out
    out = invoke("show", "1")
    assert "done" in out and "Streak" in out
    assert "Stretch" in invoke("agenda", "--days", "1")
    assert "Active reminders" in invoke("stats")


def test_add_bad_schedule_fails_nicely():
    result = runner.invoke(app, ["add", "Thing", "every", "blursday"])
    assert result.exit_code == 1
    assert "isn't a day of the week" in result.output


def test_unknown_id():
    result = runner.invoke(app, ["done", "42"])
    assert "No reminder with id 42" in result.output


def test_snooze_pause_resume_rm():
    invoke("add", "Thing", "in", "5", "minutes")
    assert "Snoozed" in invoke("snooze", "1", "1h30m")
    assert "Paused" in invoke("pause", "1")
    assert "No reminders" in invoke("list")
    assert "paused" in invoke("list", "--all")
    assert "Resumed" in invoke("resume", "1")
    assert "Deleted" in invoke("rm", "1", "-y")


def test_import_export(tmp_path):
    f = tmp_path / "in.yaml"
    f.write_text(
        "reminders:\n"
        "  - {title: Meds, when: every day at 8am, tags: [health], priority: high}\n"
        "  - {title: Bad, when: every blursday}\n"
    )
    out = invoke("import", str(f))
    assert "Imported 1" in out
    dest = tmp_path / "out.csv"
    invoke("export", str(dest))
    assert "Meds,every day at 8am,health,high" in dest.read_text()


def test_config_set_and_show():
    assert "quiet_start = 23:30" in invoke("config", "set", "quiet_start", "23:30")
    assert "23:30" in invoke("config", "show")
    bad = runner.invoke(app, ["config", "set", "nope", "1"])
    assert bad.exit_code == 1


def test_parse_duration():
    assert parse_duration("15") == 15
    assert parse_duration("15m") == 15
    assert parse_duration("2h") == 120
    assert parse_duration("1h30m") == 90
