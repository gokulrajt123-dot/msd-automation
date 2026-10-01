"""Menu and schedule for msd_bot.py.

The user's choices are kept in settings.json (this computer only):
  order              - the order the reports are downloaded in (one by one)
  schedule           - per report: "daily", "month_end", "days:1,15" or "manual"
  autostart          - run the scheduled reports automatically after logging in to Windows
  startup_delay_min  - how many minutes after logging in

schedule_state.json remembers what was downloaded when, so a restart doesn't download a report twice.

  msd_bot.py               menu
  msd_bot.py --scheduled   download the reports due today (what auto-start runs)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import msd_bot as bot

SETTINGS_FILE = bot.BASE_DIR / "settings.json"
STATE_FILE = bot.BASE_DIR / "schedule_state.json"
STARTUP_FILE = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                / "Startup" / "MSD Automation auto-start.cmd")

# Until the user runs the setup: these download every day, the others only when chosen ("manual").
DEFAULT_DAILY = {"Booking statement", "Invoice statement", "Service claim report", "Job Card Invoice Statement"}
DEFAULT_DELAY_MIN = 10


# ---------- settings ----------

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_settings() -> dict:
    s = read_json(SETTINGS_FILE)
    known = [r.name for r in bot.REPORTS]
    order = [n for n in s.get("order", []) if n in known]
    order += [n for n in known if n not in order]  # reports added in a newer version go last
    schedule = {n: s.get("schedule", {}).get(n) or ("daily" if n in DEFAULT_DAILY else "manual") for n in known}
    return {
        "order": order,
        "schedule": schedule,
        "autostart": bool(s.get("autostart", False)),
        "startup_delay_min": int(s.get("startup_delay_min", DEFAULT_DELAY_MIN)),
        "show_browser": bool(s.get("show_browser", False)),
    }


def save_settings(s: dict) -> None:
    write_json(SETTINGS_FILE, s)


def describe(schedule: str) -> str:
    if schedule == "daily":
        return "Daily"
    if schedule == "month_end":
        return "Monthly (whole previous month)"
    if schedule.startswith("days:"):
        return "Days " + schedule[5:] + " of the month"
    return "Not automatic"


def ordered_reports(s: dict) -> list[bot.Report]:
    return [bot.REPORTS_BY_NAME[n.lower()] for n in s["order"]]


# ---------- what is due today ----------

def due_jobs(s: dict, today: date) -> list[tuple[bot.Report, date, date]]:
    """Reports to download today in the user's order, with their period. Skips what was already done."""
    last = read_json(STATE_FILE)
    jobs = []
    for report in ordered_reports(s):
        sched = s["schedule"][report.name]
        done = last.get(report.name, "")
        if sched == "daily" and done != today.isoformat():
            jobs.append((report, *bot.report_period(today, False)))
        elif sched == "month_end" and done < today.replace(day=1).isoformat():
            # once a month, on the first run from the 1st on: the whole previous month
            jobs.append((report, *bot.previous_month(today)))
        elif sched.startswith("days:") and done != today.isoformat():
            days = {int(d) for d in sched[5:].split(",") if d.strip().isdigit()}
            if today.day in days:
                jobs.append((report, *bot.report_period(today, False)))
    return jobs


def mark_done(report: bot.Report) -> None:
    last = read_json(STATE_FILE)
    last[report.name] = date.today().isoformat()
    write_json(STATE_FILE, last)


# ---------- auto-start (Windows Startup folder; no admin rights needed) ----------

def apply_autostart(s: dict) -> None:
    """Create or remove the auto-start file. Rewritten each time so it follows the folder if it moves."""
    try:
        if s["autostart"]:
            python = bot.BASE_DIR / ".venv" / "Scripts" / "python.exe"
            STARTUP_FILE.write_text(
                "@echo off\r\n"
                "rem Created by MSD Automation (menu -> Auto-start). Delete this file to stop the automatic download.\r\n"
                f'cd /d "{bot.BASE_DIR}"\r\n'
                f'start "MSD Automation" /min "{python}" msd_bot.py --scheduled\r\n',
                encoding="ascii")
        elif STARTUP_FILE.exists():
            STARTUP_FILE.unlink()
    except OSError as e:
        print(f"Could not change auto-start: {e}")


# ---------- scheduled run ----------

def run_scheduled(s: dict, wait: bool) -> int:
    if wait and s["startup_delay_min"] > 0:
        print(f"MSD Automation will download today's reports in {s['startup_delay_min']} minute(s).")
        print("(Close this window to skip it this time.)")
        for left in range(s["startup_delay_min"], 0, -1):
            print(f"  starting in {left} min...", flush=True)
            time.sleep(60)
    jobs = due_jobs(s, date.today())
    if not jobs:
        bot.log("Nothing to download today (all scheduled reports are done or not due).")
        time.sleep(15)
        return 0
    failed = bot.download_reports(jobs, on_done=mark_done)
    if failed:
        input("\nSome reports were not downloaded. Press Enter to close...")
        return 1
    print("\nThis window closes by itself in 1 minute.")
    time.sleep(60)
    return 0


# ---------- menu ----------

def show(s: dict) -> None:
    print("\n" + "=" * 70)
    print("  MSD AUTOMATION")
    print("=" * 70)
    print(f"  {'#':>2}  {'Report':<30} {'Saved in':<16} How often")
    for i, r in enumerate(ordered_reports(s), 1):
        folder = r.folder.split("/")[0]
        if folder == "Stock":
            folder = "Stock\\" + r.folder.split("/")[1]
        print(f"  {i:>2}  {r.name:<30} {folder:<16} {describe(s['schedule'][r.name])}")
    auto = (f"ON - {s['startup_delay_min']} minute(s) after you log in to Windows" if s["autostart"]
            else "OFF")
    print(f"\n  Automatic download: {auto}")
    print(f"  Browser windows while downloading: {'SHOWN' if s['show_browser'] else 'HIDDEN'}")
    due = due_jobs(s, date.today())
    print(f"  Still to download today: {', '.join(r.name for r, _, _ in due) if due else 'nothing'}")
    print("""
  S  Set up automatic downloads step by step (daily / monthly / order / start time)
  D  Download today's scheduled reports now
  C  Choose reports to download now
  O  Change the order
  H  Change how often some reports download
  A  Automatic download settings
  B  Browser windows: show / hide while downloading
  X  Exit
""")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "x"


def pick_numbers(s: dict, prompt: str) -> list[str]:
    """'3,1,2' -> report names in that order ('A' = all). Ignores numbers out of range."""
    names = s["order"]
    text = ask(prompt)
    if text.lower() == "a":
        return list(names)
    picked = []
    for part in text.replace(" ", ",").split(","):
        if part.isdigit() and 1 <= int(part) <= len(names) and names[int(part) - 1] not in picked:
            picked.append(names[int(part) - 1])
    return picked


def choose_and_download(s: dict) -> None:
    names = pick_numbers(s, "Report numbers to download (e.g. 1,4,5) or A for all: ")
    if not names:
        print("No reports chosen.")
        return
    print("""Which dates?
  1  This month till today (on the 1st: the whole previous month)
  2  The whole previous month
  3  Today only (quick test)""")
    choice = ask("Choose 1, 2 or 3 [1]: ") or "1"
    today = date.today()
    period = {"2": bot.previous_month(today), "3": (today, today)}.get(choice, bot.report_period(today, False))
    jobs = [(r, *period) for r in ordered_reports(s) if r.name in names]  # in the saved order
    on_done = mark_done if choice == "1" else None  # a normal download counts for today's schedule
    bot.download_reports(jobs, on_done=on_done)
    ask("\nPress Enter to go back to the menu...")


def change_order(s: dict) -> None:
    first = pick_numbers(s, "Type the report numbers in the order you want (e.g. 3,1,2).\n"
                            "Reports you leave out keep their order after these: ")
    if first:
        s["order"] = first + [n for n in s["order"] if n not in first]
        save_settings(s)
        print("Order saved.")


def change_schedule(s: dict) -> None:
    names = pick_numbers(s, "Report numbers to change (e.g. 2,5) or A for all: ")
    if not names:
        return
    print("""How often?
  1  Daily
  2  Monthly (once a month, from the 1st: the whole previous month)
  3  Only on some days of the month (e.g. 1,15)
  4  Not automatic (only when you choose it with C)""")
    choice = ask("Choose 1-4: ")
    if choice == "1":
        value = "daily"
    elif choice == "2":
        value = "month_end"
    elif choice == "3":
        days = sorted({int(d) for d in ask("Which days (1-31, e.g. 1,15): ").replace(" ", ",").split(",")
                       if d.isdigit() and 1 <= int(d) <= 31})
        if not days:
            print("No valid days - nothing changed.")
            return
        value = "days:" + ",".join(map(str, days))
    elif choice == "4":
        value = "manual"
    else:
        print("Nothing changed.")
        return
    for n in names:
        s["schedule"][n] = value
    save_settings(s)
    print(f"Saved: {', '.join(names)} -> {describe(value)}")


def change_autostart(s: dict) -> None:
    on = ask("Download automatically after you log in to Windows? (Y/N): ").lower()
    if on not in ("y", "n"):
        print("Nothing changed.")
        return
    s["autostart"] = on == "y"
    if s["autostart"]:
        mins = ask(f"How many minutes after logging in? [{s['startup_delay_min']}]: ")
        if mins.isdigit():
            s["startup_delay_min"] = int(mins)
    save_settings(s)
    apply_autostart(s)
    print("Automatic download is " + (f"ON ({s['startup_delay_min']} min after logging in)." if s["autostart"]
                                       else "OFF."))


SCHEDULE_CHOICES = """    1 = Daily          (every day)
    2 = Monthly        (once, at the start of each month: the whole previous month)
    3 = Some days      (only on dates you choose, e.g. 1 and 15)
    4 = Not automatic  (only when you choose it with C)"""


def ask_days() -> str:
    days = sorted({int(d) for d in ask("      Which dates of the month (e.g. 1,15): ").replace(" ", ",").split(",")
                   if d.isdigit() and 1 <= int(d) <= 31})
    return "days:" + ",".join(map(str, days)) if days else ""


def setup_wizard(s: dict) -> None:
    """Ask, report by report, how often it downloads; then the order; then the automatic start."""
    print("\n" + "=" * 70)
    print("  SET UP AUTOMATIC DOWNLOADS")
    print("=" * 70)
    print("\nStep 1 of 3 - How often should each report download by itself?\n")
    print(SCHEDULE_CHOICES)
    print("\n  Type 1, 2, 3 or 4 and press Enter. Just press Enter to keep the choice shown in [ ].\n")
    for r in ordered_reports(s):
        current = s["schedule"][r.name]
        while True:
            answer = ask(f"  {r.name:<30} [{describe(current)}]: ")
            if answer == "":
                break
            if answer in ("1", "2", "4"):
                s["schedule"][r.name] = {"1": "daily", "2": "month_end", "4": "manual"}[answer]
                break
            if answer == "3":
                days = ask_days()
                if days:
                    s["schedule"][r.name] = days
                    break
            print("      Please type 1, 2, 3 or 4 (or just Enter).")

    auto = [r.name for r in ordered_reports(s) if s["schedule"][r.name] != "manual"]
    print("\nStep 2 of 3 - In which order should they download? (one after another)\n")
    for i, name in enumerate(auto, 1):
        print(f"  {i:>2}  {name}  ({describe(s['schedule'][name])})")
    text = ask("\n  Type the numbers in the order you want (e.g. 3,1,2), or just Enter to keep this order: ")
    first = []
    for part in text.replace(" ", ",").split(","):
        if part.isdigit() and 1 <= int(part) <= len(auto) and auto[int(part) - 1] not in first:
            first.append(auto[int(part) - 1])
    if first:
        new_auto = first + [n for n in auto if n not in first]
        s["order"] = new_auto + [n for n in s["order"] if n not in new_auto]

    print("\nStep 3 of 3 - Start by itself after switching on the computer?\n")
    on = ask(f"  Download automatically? (Y/N) [{'Y' if s['autostart'] else 'N'}]: ").lower()
    if on in ("y", "n"):
        s["autostart"] = on == "y"
    if s["autostart"]:
        mins = ask(f"  How many minutes after switching on? [{s['startup_delay_min']}]: ")
        if mins.isdigit():
            s["startup_delay_min"] = int(mins)
    save_settings(s)
    apply_autostart(s)

    print("\nSaved. Your automatic downloads:")
    for r in ordered_reports(s):
        if s["schedule"][r.name] != "manual":
            print(f"  - {r.name}: {describe(s['schedule'][r.name])}")
    print("  Starts by itself: " + (f"{s['startup_delay_min']} minute(s) after switching on the computer"
                                     if s["autostart"] else "no (use D in the menu)"))
    ask("\nPress Enter to go to the menu...")


def menu() -> int:
    s = load_settings()
    if not SETTINGS_FILE.exists():  # first start: set up right away
        setup_wizard(s)
    apply_autostart(s)  # keeps the auto-start file pointing at this folder
    while True:
        show(s)
        choice = ask("Your choice: ").lower()
        if choice == "s":
            setup_wizard(s)
        elif choice == "d":
            jobs = due_jobs(s, date.today())
            if jobs:
                bot.download_reports(jobs, on_done=mark_done)
            else:
                print("Nothing to download today (all scheduled reports are done or not due).")
            ask("\nPress Enter to go back to the menu...")
        elif choice == "c":
            choose_and_download(s)
        elif choice == "o":
            change_order(s)
        elif choice == "h":
            change_schedule(s)
        elif choice == "a":
            change_autostart(s)
        elif choice == "b":
            s["show_browser"] = not s["show_browser"]
            bot.SHOW_BROWSER = s["show_browser"]
            save_settings(s)
        elif choice == "x":
            return 0


def main(args: list[str]) -> int:
    s = load_settings()
    bot.SHOW_BROWSER = s["show_browser"] or "--show" in args
    if "--scheduled" in args:
        return run_scheduled(s, wait="--now" not in args)
    return menu()
