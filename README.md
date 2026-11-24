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

- **No exact wording needed**: typos and casual phrasing work (`evry thrusday 9.30am`, `mondays and fridays at 6`,
  `twice a day`, `everyday at 8`). Nudge always shows what it understood and the next few times before saving.
- **Guided form**: press `a` in the dashboard to pick the repeat pattern, days and times from a form, with a live
  preview. Or run `nudge add` with no arguments to be asked step by step.
- **Undo anything**: Done, Skip, Snooze, Pause, Edit and Reset can all be undone (`u` in the dashboard, `nudge undo`).
  Pressing Done twice won't eat the next occurrence, and `nudge reset` puts a reminder back on its schedule.
- **Natural schedules**: daily, weekdays, weekends, specific days, every N minutes/hours/days/weeks/months,
  monthly on a date or the last day, yearly, several times a day, `starting <date>`, and one-offs like
  `in 20 minutes` or `friday at noon`.
- **Clear notifications**: branded Nudge toasts with a colour-coded icon (blue normal, amber still waiting,
  red urgent), a status line ("Due now", "Overdue 25 min", "Snoozed"), notes or schedule, your streak, and
  **Done**, a **Snooze** dropdown (10 minutes to tomorrow morning) and **Skip** buttons. Important reminders
  and nags stay on screen until you act.
- **Nagging**: if you ignore a reminder it comes back every 15 minutes (configurable, capped).
- **Streaks and history**: finishing on time grows a 🔥 streak; ignoring an occurrence until the next one
  arrives records it as missed and resets the streak. Skipping keeps it.
- **Quiet hours**: nothing between 22:00 and 07:00 except urgent reminders (configurable).
- **Tags and priorities**: `#tag` and `!low`/`!high`/`!urgent` right in the title, or `-t` / `-p` flags.
- **Dashboard**: run `nudge` with no arguments for an interactive dashboard. Keys: `a` new, `e` edit,
  `d` done, `s` snooze, `k` skip, `u` undo, `r` reset, `p` pause, `x` delete, `/` filter, `h` show all.
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
| `nudge add [TITLE] [WHEN...]` | Add a reminder (`-t tag`, `-p priority`, `-n notes`); no args = step by step |
| `nudge preview WHEN...` | Check what a schedule means and its next times |
| `nudge list` | Open reminders, soonest first (`--all`, `--tag`, `--search`) |
| `nudge today` | What's due now and later today |
| `nudge agenda -d 7` | Every upcoming occurrence for the next days |
| `nudge show ID` | Details and recent history |
| `nudge done ID...` | Mark done (recurring ones move to the next time; `--early` to do the next one ahead) |
| `nudge undo [ID]` | Undo the last done / skip / snooze / pause / edit / reset |
| `nudge reset ID...` | Put a reminder back on its schedule (next time from now) |
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
  notify.py     notification content + Windows toasts (windows-toasts), console fallback elsewhere
  forms.py      the guided new/edit form with live preview (Textual)
  assets/       notification icons (regenerate with tools/make_icons.py)
  startup.py    HKCU Run key so the runner starts at sign-in via pythonw (no window)
  cli.py        Typer + Rich commands
  tui.py        Textual dashboard
  transfer.py   YAML / CSV import and export
```

Times are stored as local wall-clock times, so "every day at 9am" stays at 9am across daylight saving changes.
