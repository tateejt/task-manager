import asyncio

from nudge.tui import NudgeApp


def test_tui_add_done_and_filter(svc):
    async def scenario():
        app = NudgeApp(svc)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("a")
            app.query_one("Input").value = "Drink water #health every 2 hours"
            await pilot.press("enter")
            await pilot.pause()
            assert [r.title for r in svc.list()] == ["Drink water"]
            await pilot.press("d")
            await pilot.pause()
            assert svc.list()[0].streak == 1
            await pilot.press("slash")
            app.query_one("Input").value = "#nomatch"
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one("DataTable").row_count == 0

    asyncio.run(scenario())
