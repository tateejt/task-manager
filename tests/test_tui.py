import asyncio

from textual.widgets import Checkbox, DataTable, Input, Select

from nudge.forms import ReminderForm
from nudge.tui import NudgeApp


def run(svc, scenario):
    async def main():
        app = NudgeApp(svc)
        async with app.run_test(size=(120, 50)) as pilot:
            await scenario(app, pilot)

    asyncio.run(main())


def test_quick_typed_title_splits_into_schedule(svc):
    async def scenario(app, pilot):
        await pilot.press("a")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, ReminderForm)
        form.query_one("#title", Input).value = "Drink water #health every m"
        await pilot.pause()
        assert "Every minute" not in str(form.query_one("#preview").render())  # half-typed, nothing guessed
        form.query_one("#title", Input).value = "Drink water #health every 2 hours"
        await pilot.pause()
        assert form.query_one("#title", Input).value == "Drink water #health every 2 hours"  # never rewritten
        preview = str(form.query_one("#preview").render())
        assert "Every 2 hours" in preview
        assert "Drink water #health —" in preview
        await pilot.press("ctrl+s")
        await pilot.pause()
        r = svc.list()[0]
        assert (r.title, r.tag_list, r.schedule_label) == ("Drink water", ["health"], "Every 2 hours")

    run(svc, scenario)


def test_guided_builder(svc):
    async def scenario(app, pilot):
        await pilot.press("a")
        await pilot.pause()
        form = app.screen
        form.query_one("#title", Input).value = "Water plants"
        form.query_one("#repeat", Select).value = "days"
        await pilot.pause()
        assert not form.query_one("#row-days").has_class("hidden")
        assert form.query_one("#row-every").has_class("hidden")
        form.query_one("#day-mon", Checkbox).value = True
        form.query_one("#day-thu", Checkbox).value = True
        form.query_one("#times", Input).value = "9am"
        form.query_one("#priority", Select).value = 2
        await pilot.pause()
        assert "Every Mon, Thu at 9:00 AM" in str(form.query_one("#preview").render())
        await pilot.press("ctrl+s")
        await pilot.pause()
        r = svc.list()[0]
        assert (r.title, r.schedule_label, r.priority) == ("Water plants", "Every Mon, Thu at 9:00 AM", 2)

    run(svc, scenario)


def test_invalid_schedule_blocks_save(svc):
    async def scenario(app, pilot):
        await pilot.press("a")
        await pilot.pause()
        form = app.screen
        form.query_one("#title", Input).value = "Thing"
        form.query_one("#quick", Input).value = "every blursday"
        await pilot.pause()
        assert form.query_one("#preview").has_class("bad")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen is form
        await pilot.press("escape")
        await pilot.pause()
        assert svc.list() == []

    run(svc, scenario)


def test_double_done_then_undo(svc):
    r = svc.add("Walk", "every 2 hours")
    first_due = r.due_at

    async def scenario(app, pilot):
        await pilot.press("d")
        await pilot.press("d")  # refused, doesn't eat another occurrence
        await pilot.pause()
        assert svc.get(r.id).streak == 1
        await pilot.press("u")
        await pilot.pause()
        got = svc.get(r.id)
        assert got.streak == 0 and got.due_at == first_due

    run(svc, scenario)


def test_edit_and_filter(svc):
    r = svc.add("Walk", "every day at 9am")

    async def scenario(app, pilot):
        await pilot.press("e")
        await pilot.pause()
        form = app.screen
        assert form.query_one("#quick", Input).value == "every day at 9am"
        form.query_one("#quick", Input).value = "weekdays at 7"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert svc.get(r.id).schedule_label == "Weekdays at 7:00 AM"
        await pilot.press("slash")
        app.query_one("#input", Input).value = "#nomatch"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 0

    run(svc, scenario)
