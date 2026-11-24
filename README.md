# ⏰ Nudge

A modern terminal reminder app for Windows. Tell it what you need to do and when, in plain English,
and it pops a Windows notification with **Done**, **Snooze** and **Skip** buttons when it's time.

```powershell
nudge add "Water plants #home" every mon,thu at 9am
nudge add "Take meds !high" every day at 8am and 8pm
nudge add "Stretch every 90 minutes"
nudge add "Pay rent" monthly on the 1st at 10am -t bills
nudge add "Call mom tomorrow at 6pm"
nudge run          # or: nudge startup install
```

## Features

- **Natural schedules**: daily, weekdays, weekends, specific days, every N minutes/hours/days/weeks/months,
  monthly on a date or the last day, yearly, several times a day, `starting <date>`, and one-offs like
  `in 20 minutes` or `friday at noon`.
- **Actionable toasts**: Done, Snooze (default and 1 hour) and Skip buttons right on the Windows notification.
  High-priority reminders and nags stay on screen until you act.
- **Nagging**: if you ignore a reminder it comes back every 15 minutes (configurable, capped).
- **Streaks and history**: finishing on time grows a 🔥 streak; ignoring an occurrence until the next one
  arrives records it as missed and resets the streak. Skipping keeps it.
- **Quiet hours**: nothing between 22:00 and 07:00 except urgent reminders (configurable).
- **Tags and priorities**: `#tag` and `!low`/`!high`/`!urgent` right in the title, or `-t` / `-p` flags.
- **Dashboard**: run `nudge` with no arguments for an interactive dashboard (add, done, snooze, skip,
  pause, delete, filter, all from the keyboard).
- **Bulk import/export** in YAML or CSV (see [`examples/reminders.yaml`](examples/reminders.yaml)).
- **Starts at login** with no admin rights and no console window (`nudge startup install`).

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/tateejt/task-manager
cd task-manager
uv tool install .        # puts `nudge` on your PATH
nudge test-notify        # check that notifications show up
nudge startup install    # run in the background every time you sign in
```

For development: `uv sync`, then `uv run pytest` and `uv run nudge ...`.

## Commands

| Command | What it does |
| --- | --- |
| `nudge` / `nudge tui` | Interactive dashboard |
| `nudge add TITLE [WHEN...]` | Add a reminder (`-t tag`, `-p priority`, `-n notes`) |
| `nudge list` | Open reminders, soonest first (`--all`, `--tag`, `--search`) |
| `nudge today` | What's due now and later today |
| `nudge agenda -d 7` | Every upcoming occurrence for the next days |
| `nudge show ID` | Details and recent history |
| `nudge done ID...` | Mark done (recurring ones move to the next time) |
| `nudge snooze ID [10m\|1h\|1h30m]` | Snooze |
| `nudge skip ID...` | Skip this occurrence, keep the streak |
| `nudge pause ID...` / `resume ID...` | Turn reminders off and on |
| `nudge edit ID --when "..." --title ...` | Change a reminder |
| `nudge rm ID...` | Delete |
| `nudge history` / `nudge stats` | What you did, missed, and your streaks |
| `nudge import FILE` / `export [FILE]` | YAML or CSV |
| `nudge run` | The background notifier |
| `nudge startup install\|uninstall\|status` | Start at Windows sign-in |
| `nudge config show` / `config set KEY VALUE` | Settings |

### Schedule examples

| You type | Means |
| --- | --- |
| `every day at 9am` / `daily at 7:30 and 9pm` | Every day, one or more times |
| `weekdays at 8` / `weekends at 10am` | Mon–Fri / Sat–Sun |
| `every mon,thu at 6pm` / `every monday and friday` | Specific days (9:00 AM if no time) |
| `every 2 hours` / `every 90 minutes` | Fixed interval from now |
| `every 3 days at noon` / `every other monday` | Every N days or weeks |
| `monthly on the 1st` / `every month on the last day` / `monthly on the 15th and 30th` | Monthly |
| `every 3 months on the 1st` / `yearly on mar 14` | Quarterly, yearly |
| `every day at 9am starting dec 1` | Recurring from a later date |
| `in 20 minutes` / `tomorrow at 5pm` / `friday at noon` / `dec 3 at 2pm` | One-off |

### Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `nag_minutes` | 15 | Re-alert an ignored reminder this often (0 = never) |
| `max_nags` | 8 | Stop re-alerting after this many (0 = no limit) |
| `snooze_minutes` | 10 | Default snooze |
| `quiet_start` / `quiet_end` | 22:00 / 07:00 | Quiet hours (set both empty to disable) |
| `quiet_bypass_priority` | 3 | Priority that still alerts in quiet hours (3 = urgent) |
| `poll_seconds` | 20 | How often the runner checks |
| `sound` | true | Play a sound |

Data lives in `%LOCALAPPDATA%\nudge` (`nudge config show` prints the folder). Set `NUDGE_HOME` to use another.

## How it works

```
src/nudge/
  schedule.py   natural-language parsing -> iCalendar RRULEs (dateutil) or one-off times (dateparser)
  models.py     SQLModel tables: Reminder, Completion (history)
  service.py    add/complete/snooze/skip, missed-occurrence rollover, alert selection, quiet hours
  runner.py     the background loop; toast button clicks come back through a queue
  notify.py     Windows toasts (windows-toasts) with a console fallback elsewhere
  startup.py    HKCU Run key so the runner starts at sign-in via pythonw (no window)
  cli.py        Typer + Rich commands
  tui.py        Textual dashboard
  transfer.py   YAML / CSV import and export
```

Times are stored as local wall-clock times, so "every day at 9am" stays at 9am across daylight saving changes.
