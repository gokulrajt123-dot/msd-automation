"""MSD (Dynamics 365 Finance and Operations) automation.

Logs in, then downloads reports into downloads/<today>/<report name>/.
Period: 1st of this month to today; when run on the 1st, the whole previous month.
Run with --today to download only today's data (quick test).
Run with --report "Sales register" to download just that one report (see SEPARATE_REPORTS).

Login flow:
  1. Microsoft login   -> enter email, Next
  2. Royal Enfield SSO -> enter username + password, Submit
  3. "Stay signed in?" -> Yes
  4. D365 F&O home page (Workspaces)

The script checks which screen is showing and handles it, so it still works
if a step is skipped (e.g. the saved login is still valid).

Logs in once, then downloads all reports at the same time, each in its own browser window.
Each browser is a fresh temporary one; only the login cookies are kept (sessions/<user>.json).
A reused browser profile made Chrome crash every time an automated download finished.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from playwright.sync_api import Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "sessions"  # saved login cookies, one file per MSD user
DOWNLOAD_DIR = BASE_DIR / "downloads"
LOG_DIR = BASE_DIR / "logs"

# Dynamics 365 Finance & Operations home page. (NOT royalenfieldpos... - that is Store Commerce/POS.)
DEFAULT_MSD_URL = "https://royalenfield.operations.dynamics.com/"

# Reports to download, in order: (name as shown on the tile in the Reports workspace, kind).
# Each one is saved to downloads/<run date>/<report name>/. To add a report, add a line here.
# Kinds:
#   "ssrs" - dates dialog -> OK -> report shown on screen -> Export -> Excel
#   "grid" - page with From/To date -> Office icon -> Export to Excel: <report> -> Download
#   "grid+generate" - same as "grid", but press Generate after the dates
#   "list:<home tile>" - home tile (e.g. Part) -> tile with the report's name -> Office icon -> Export to Excel
#              -> Download (no dates: the whole list is exported). "list:<home tile>><tile>" when the tile's
#              name differs from the report's (e.g. three workspaces each have a "Stock Report" tile).
#   "tilldate" - dialog with only a Till date (= end of the period) -> OK -> Download
REPORTS = [
    ("Job Card Invoice Statement", "ssrs"),
    ("Booking statement", "grid"),
    ("Invoice statement", "grid"),
]

# Reports that run on their own (not with the ones above): msd_bot.py --report "Sales register"
SEPARATE_REPORTS = [
    ("Sales register", "grid"),
    ("Purchase register", "grid"),
    ("Return Invoice Statement", "grid"),
    ("Vehicle Stock Ageing", "tilldate"),
    ("Cancelled booking statement", "grid+generate"),
    ("REAssure Incentive Claims", "list:Part>Claims"),  # all claims (~5 years) - a very big download
    ("Parts stock", "list:Part>Stock Report"),
    ("GMA stock", "list:GMA>Stock Report"),
    ("Gear stock", "list:Gear>Stock Report"),
    ("Service claim report", "grid+generate"),
]

# Reports that log in with a different MSD account (.env: <ACCOUNT>_EMAIL, _USERNAME, _PASSWORD).
REPORT_ACCOUNT = {
    "REAssure Incentive Claims": "PARTS",
    "Parts stock": "PARTS",
    "Service claim report": "PARTS",
}

# Very big whole-list exports: count the rows afterwards and ask the user to check the file is complete.
CHECK_COMPLETE = {"REAssure Incentive Claims"}

LOGIN_TIMEOUT_SEC = 300  # per attempt; leaves time for manual approvals
LOGIN_ROUNDS = 3
EXPORT_TRIES = 3  # start a report again when MSD's download link expired before the file was ready
REPORT_TIMEOUT_SEC = 4 * 60 * 60  # a month of data can take 2+ hours


_log_lock = threading.Lock()


def worker_tag() -> str:
    """Name of the report this thread works on ('' for the main thread)."""
    t = threading.current_thread()
    return "" if t is threading.main_thread() else t.name


def file_tag() -> str:
    return re.sub(r"[^\w-]+", "_", worker_tag()).strip("_")


def log(msg: str) -> None:
    tag = worker_tag()
    line = f"[{datetime.now():%H:%M:%S}] " + (f"[{tag}] " if tag else "") + msg
    with _log_lock:  # the report browsers log at the same time
        print(line, flush=True)
        try:  # also keep a log file, so a run can be checked afterwards
            LOG_DIR.mkdir(exist_ok=True)
            with open(LOG_DIR / f"run_{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def load_config(account: str = "") -> dict:
    """Login details from .env. account="PARTS" reads PARTS_EMAIL / PARTS_USERNAME / PARTS_PASSWORD."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists() or env_file.stat().st_size == 0:
        sys.exit("ERROR: .env file is missing or empty. See .env.example for what to put in it.")

    # Read the file directly (not os.environ) so Windows' own USERNAME variable can't interfere.
    raw = {k.upper(): (v or "").strip() for k, v in dotenv_values(env_file).items()}

    def pick(*keys: str) -> str:
        return next((raw[k] for k in keys if raw.get(k)), "")

    if account:
        pre = account.upper() + "_"
        cfg = {
            "email": pick(pre + "EMAIL", pre + "USER_ID", pre + "USERNAME"),
            "password": pick(pre + "PASSWORD"),
        }
        sso_user = pick(pre + "SSO_USERNAME", pre + "USERNAME")
    else:
        cfg = {
            "email": pick("MSD_EMAIL", "EMAIL", "USER_ID", "MSD_USERNAME", "USERNAME", "USER"),
            "password": pick("MSD_PASSWORD", "PASSWORD", "PASS"),
        }
        sso_user = pick("SSO_USERNAME", "MSD_USERNAME", "USERNAME")
    missing = [f"{account.upper() + '_' if account else ''}{k.upper()}" for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"ERROR: missing in .env: {', '.join(missing)}. See .env.example.")

    # MSD site: use .env value only if it is a real F&O address (not a login link or the POS site).
    url = pick("MSD_URL", "URL", "D365_URL", "ADDRESS")
    host = urlparse(url).netloc.lower()
    if not host.endswith("dynamics.com") or host.startswith("royalenfieldpos."):
        if url:
            log(f"Ignoring address in .env ({host or url[:40]}) - using {DEFAULT_MSD_URL}")
        url = DEFAULT_MSD_URL
    cfg["url"] = url

    cfg["sso_user"] = sso_user or cfg["email"].split("@")[0]
    # Separate saved login per MSD user, so switching ids never reuses someone else's session.
    cfg["session_file"] = SESSION_DIR / (re.sub(r"[^\w.-]", "_", cfg["sso_user"].lower()) + ".json")
    if "@" not in cfg["email"]:  # Microsoft login needs the full email
        cfg["email"] = f"{cfg['email']}@{pick('EMAIL_DOMAIN') or 'rebridge.co.in'}"
    return cfg


def visible(page: Page, selector: str) -> bool:
    try:
        return page.locator(selector).first.is_visible()
    except Exception:
        return False


def on_landing_page(page: Page) -> bool:
    if "dynamics.com" not in page.url:
        return False
    try:
        return page.get_by_text(re.compile(r"^\s*workspaces\s*$", re.I)).first.is_visible()
    except Exception:
        return False


class LoginSteps:
    """Remembers how often / how recently each login step was done, so a step can be
    repeated when MSD asks to log in again, without hammering it (e.g. wrong password)."""

    COOLDOWN_SEC = 8
    MAX_TRIES = 5

    def __init__(self) -> None:
        self.tries: dict[str, int] = {}
        self.last: dict[str, float] = {}

    def ready(self, step: str) -> bool:
        if self.tries.get(step, 0) >= self.MAX_TRIES:
            raise RuntimeError(f"Login step '{step}' repeated {self.MAX_TRIES} times without success.")
        return time.time() - self.last.get(step, 0) > self.COOLDOWN_SEC

    def done(self, step: str) -> None:
        self.tries[step] = self.tries.get(step, 0) + 1
        self.last[step] = time.time()


def login_attempt(page: Page, cfg: dict, timeout_sec: int) -> bool:
    """One pass through the login screens. Returns True once the landing page shows."""
    steps = LoginSteps()
    deadline = time.time() + timeout_sec
    waiting_msg_shown = False

    while time.time() < deadline:
        if on_landing_page(page):
            return True
        url = page.url

        # Microsoft: "Pick an account"
        if visible(page, "#tilesHolder") and steps.ready("pick account"):
            log("Choosing account")
            tile = page.locator("#tilesHolder").get_by_text(cfg["email"], exact=False)
            (tile.first if tile.count() else page.locator("#otherTile")).click()
            steps.done("pick account")
            continue

        # Microsoft: email screen
        if visible(page, 'input[name="loginfmt"]') and steps.ready("email"):
            log("Entering Microsoft email")
            page.fill('input[name="loginfmt"]', cfg["email"])
            page.click("#idSIButton9")
            steps.done("email")
            page.wait_for_timeout(2000)
            continue

        # Royal Enfield SSO (the page also has hidden decoy fields, so target the real ones by id)
        if "sso.rebridge.co.in" in url and visible(page, "#password") and steps.ready("sso"):
            if visible(page, "text=/invalid username or password/i") and steps.tries.get("sso"):
                raise RuntimeError("SSO says: Invalid username or password. Check .env.")
            log("Entering SSO username and password")
            for sel, value in (("#username", cfg["sso_user"]), ("#password", cfg["password"])):
                box = page.locator(sel)
                box.click()
                box.fill("")
                box.press_sequentially(value, delay=40)  # real keystrokes so the Submit button enables
            page.click("#login-button")
            steps.done("sso")
            page.wait_for_timeout(3000)
            continue

        # Microsoft: "Stay signed in?"
        if visible(page, "text=Stay signed in?") and steps.ready("stay signed in"):
            log("Answering 'Stay signed in?' -> Yes")
            if visible(page, "#KmsiCheckboxField"):
                page.check("#KmsiCheckboxField")
            page.click("#idSIButton9")
            steps.done("stay signed in")
            page.wait_for_timeout(2000)
            continue

        # Microsoft: other confirm screens (permissions "Accept", "Continue", ...)
        if "login.microsoftonline.com" in url and visible(page, "#idSIButton9") and steps.ready("confirm"):
            label = page.locator("#idSIButton9").first
            text = (label.get_attribute("value") or label.inner_text() or "").strip()
            log(f"Confirming Microsoft screen -> '{text}'")
            label.click()
            steps.done("confirm")
            page.wait_for_timeout(2000)
            continue

        # Anything else (loading, approval on phone, OTP, ...): wait so the user can finish it.
        if "dynamics.com" not in url and not waiting_msg_shown:
            log(">> If an approval / OTP is shown, please complete it in the browser. Waiting...")
            waiting_msg_shown = True
        page.wait_for_timeout(1000)

    return False


def launch_browser(p, cfg: dict):
    """Start a fresh browser (Playwright's Chromium; else installed Chrome, then Edge) with the
    saved login cookies of this user, if any."""
    state = cfg["session_file"] if cfg["session_file"].exists() else None
    for channel in (None, "chrome", "msedge"):  # None = Playwright's bundled Chromium
        try:
            browser = p.chromium.launch(
                channel=channel,
                headless=False,
                chromium_sandbox=True,  # avoids the "--no-sandbox" warning bar
                downloads_path=str(DOWNLOAD_DIR),
                args=["--start-maximized"],
            )
            context = browser.new_context(storage_state=state, accept_downloads=True, no_viewport=True)
            context.new_page()
            log(f"Browser started ({channel or 'chromium'}{', saved login loaded' if state else ''})")
            return context
        except Exception as e:
            log(f"Could not start {channel or 'chromium'}: {str(e).splitlines()[0][:200]}")
    raise RuntimeError("No browser could be started (Chromium, Chrome or Edge).")


def save_session(context, cfg: dict) -> None:
    """Keep the login cookies so the next run can skip the login screens."""
    try:
        SESSION_DIR.mkdir(exist_ok=True)
        context.storage_state(path=str(cfg["session_file"]))
    except Exception as e:
        log(f"Could not save login for next time: {str(e).splitlines()[0][:150]}")


def close_browser(context) -> None:
    try:
        context.close()
        context.browser.close()
    except Exception:
        pass


def browser_alive(context) -> bool:
    return context is not None and context.browser is not None and context.browser.is_connected()


def current_page(context) -> Page:
    """The page to work on: the most recent open tab."""
    pages = [pg for pg in context.pages if not pg.is_closed()]
    return pages[-1] if pages else context.new_page()


def any_open_page(context) -> Page:
    """An open tab to wait on (MSD may close its own tab after the Excel export)."""
    if not browser_alive(context):
        raise RuntimeError("The browser was closed.")
    return current_page(context)


def save_error_screenshot(context) -> None:
    shot = LOG_DIR / f"error_{datetime.now():%Y%m%d_%H%M%S}{'_' + file_tag() if file_tag() else ''}.png"
    try:
        current_page(context).screenshot(path=str(shot))
        log(f"Screenshot saved: {shot}")
    except Exception:
        pass


def reconnect_if_needed(page: Page) -> bool:
    """Answer MSD's pop-ups that stop a long export:
    'It appears you lost network connectivity' -> Reconnect,
    'The operation is taking a long time to process, click Wait ...' -> Wait."""
    reconnected = False
    for name, msg in (("Reconnect", "MSD lost its connection - clicking Reconnect"),
                      ("Wait", "MSD says the export is taking long - clicking Wait")):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I)).locator("visible=true")
            if btn.count():
                log(msg)
                btn.first.click(timeout=10_000)
                page.wait_for_timeout(3000)
                reconnected = reconnected or name == "Reconnect"
        except Exception:
            pass
    return reconnected


def wait_until_idle(page: Page, timeout_ms: int = 120_000) -> None:
    """D365 shows a transparent 'please wait' layer while busy; clicks during that time are lost."""
    page.locator("#ShellBlockingDiv").wait_for(state="hidden", timeout=timeout_ms)
    page.wait_for_timeout(500)


def click_when_ready(page: Page, locator, what: str, tries: int = 5) -> None:
    for attempt in range(1, tries + 1):
        reconnect_if_needed(page)
        try:
            wait_until_idle(page, 30_000)
            locator.scroll_into_view_if_needed(timeout=30_000)
            locator.click(timeout=30_000, force=attempt == tries)  # last try: click even if covered
            return
        except Exception as e:
            if attempt == tries:
                raise RuntimeError(f"Could not click '{what}': {str(e).splitlines()[0][:150]}")
            log(f"'{what}' not clickable yet - retrying ({attempt}/{tries})")
            page.wait_for_timeout(3000)


def open_list_page(page: Page, home_tile: str, tile_name: str) -> None:
    """Home page tile (workspace, e.g. 'Part') -> tile of a list page (e.g. 'Claims')."""
    log(f"Opening '{home_tile}' workspace")
    tile = page.locator(".tile-text").get_by_text(home_tile, exact=True).locator("visible=true").first
    click_when_ready(page, tile, home_tile)
    wait_until_idle(page)
    log(f"Opening '{tile_name}'")
    tiles = page.locator(".tile-text").locator("visible=true")
    tiles.first.wait_for(state="visible", timeout=120_000)
    exact = tiles.filter(has_text=re.compile(rf"^\s*{re.escape(tile_name)}\s*$", re.I))
    if not exact.count():
        raise RuntimeError(f"No tile named '{tile_name}' in the '{home_tile}' workspace.")
    # Some list pages (e.g. GMA / Gear stock) take a minute to open; the workspace stays on screen
    # meanwhile, so wait until its tiles are gone (click again if the click was lost).
    for attempt in range(1, 4):
        click_when_ready(page, exact.first, tile_name)
        try:
            exact.first.wait_for(state="hidden", timeout=300_000)
            break
        except Exception:
            if attempt == 3:
                raise RuntimeError(f"'{tile_name}' did not open.")
            log(f"'{tile_name}' did not open yet - clicking again")
    page.locator("[role='grid']").locator("visible=true").first.wait_for(state="visible", timeout=300_000)
    wait_until_idle(page, 300_000)


def open_report_dialog(page: Page, report_name: str, first_field: str = "From Date") -> None:
    log("Opening Reports workspace")
    if "PwCReportWorkspaceMenuItem" not in page.url:
        tile = page.locator(".tile-text, [class*='tile']").get_by_text("Reports", exact=True).locator("visible=true").first
        click_when_ready(page, tile, "Reports")
        page.wait_for_url(re.compile("PwCReportWorkspaceMenuItem"), timeout=120_000)
    wait_until_idle(page)

    log(f"Opening '{report_name}'")
    # visible=true: MSD keeps hidden copies of earlier opened pages with the same tiles
    tiles = page.locator(".tile-text").locator("visible=true")
    tiles.first.wait_for(state="visible", timeout=120_000)
    # Exact name first, so "Booking statement" doesn't open "Cancelled booking statement" etc.
    exact = tiles.filter(has_text=re.compile(rf"^\s*{re.escape(report_name)}\s*\.?\s*$", re.I))
    tile = exact.first if exact.count() else tiles.filter(has_text=re.compile(re.escape(report_name), re.I)).first
    if not tile.count():
        raise RuntimeError(f"No tile named '{report_name}' in the Reports workspace.")
    click_when_ready(page, tile, report_name)
    date_field(page, first_field).wait_for(state="visible", timeout=120_000)
    wait_until_idle(page)


def date_field(page: Page, label: str):
    """Input for a label, e.g. 'From Date' / 'From date' (inputs point to their label via aria-labelledby)."""
    label_el = page.locator("[id$='_label']", has_text=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)).first
    label_el.wait_for(state="attached", timeout=120_000)
    return page.locator(f"input[aria-labelledby='{label_el.get_attribute('id')}']")


def set_date(page: Page, label: str, value: date) -> None:
    text = f"{value.month}/{value.day}/{value.year}"  # D365 shows dates as M/D/YYYY
    box = date_field(page, label)
    box.click()
    box.press("Control+a")
    box.press_sequentially(text, delay=30)
    box.press("Tab")
    wait_until_idle(page)
    shown = box.input_value()
    if shown != text:
        raise RuntimeError(f"{label}: typed {text} but MSD shows '{shown}' (date format differs?)")
    log(f"{label} = {text}")


def run_report(context, page: Page, report_name: str, kind: str, from_date: date, to_date: date,
               run_dir: Path) -> Path:
    """Open the report, fill the dates, export to Excel and save it in run_dir/<report name>/."""
    if kind.startswith("list:"):
        home_tile, _, tile = kind.split(":", 1)[1].partition(">")
        open_list_page(page, home_tile, tile or report_name)
    elif kind == "tilldate":
        open_report_dialog(page, report_name, "Till date")
        to_date = date.today()  # stock as on today, also on the 1st of the month
        set_date(page, "Till date", to_date)
    else:
        open_report_dialog(page, report_name)
        set_date(page, "From Date", from_date)
        set_date(page, "To Date", to_date)

    downloads = []
    for pg in context.pages:
        pg.on("download", lambda d: downloads.append(d))
    context.on("page", lambda pg: pg.on("download", lambda d: downloads.append(d)))

    started = time.time()
    if kind.startswith("list:"):
        export_grid(context, page, report_name, started)
    elif kind.startswith("grid"):
        if kind == "grid+generate":
            log("Pressing Generate")
            generate = page.get_by_role("button", name=re.compile(r"^\s*Generate\s*$", re.I)).locator("visible=true").first
            click_when_ready(page, generate, "Generate")
            wait_until_idle(page, REPORT_TIMEOUT_SEC * 1000)
        export_grid(context, page, report_name, started)
    elif kind == "tilldate":
        export_tilldate(context, page, started, downloads)
    else:
        export_ssrs(context, page, from_date, to_date, started)

    # Wait for the Excel file.
    log("Excel export requested - waiting for the download")
    def downloaded() -> bool:
        if downloads:
            return True
        if export_link_expired(context):
            raise ExportLinkExpired("MSD's download link expired before the Excel file was ready "
                                    "(AuthenticationFailed / 'Signature not valid in the specified key time frame').")
        return False

    wait_long(context, started, "Excel download", downloaded, restart_on_reconnect=True)

    target_dir = run_dir / report_name.strip(" .")
    target_dir.mkdir(parents=True, exist_ok=True)
    dl = downloads[0]
    ext = Path(dl.suggested_filename).suffix or ".xlsx"
    period = f"till {to_date:%d-%m-%Y}" if kind == "tilldate" else f"all till {date.today():%d-%m-%Y}" if kind.startswith("list:") else f"{from_date:%d-%m-%Y} to {to_date:%d-%m-%Y}"
    name = f"{report_name.strip(' .')} {period}{ext}"
    target = unique_path(target_dir / name)  # never overwrite an earlier download
    log(f"Download started ({dl.suggested_filename}) - saving...")
    dl.save_as(str(target))  # waits for the download to finish, however long it takes
    any_open_page(context).wait_for_timeout(3000)  # small pause before moving on / closing
    log(f"Downloaded in {minutes(started)} min: {target}")
    if report_name in CHECK_COMPLETE:
        check_full_export(target, report_name)
    return target


EXCEL_MAX_ROWS = 1_048_576


def check_full_export(path: Path, report_name: str) -> None:
    """Count the data rows in a whole-list export, show the oldest/newest date, and ask the user to
    check it is complete (MSD can stop an Excel export at a row limit without saying so)."""
    import zipfile
    from xml.etree.ElementTree import fromstring, iterparse
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            sheet = next(n for n in sorted(names) if re.match(r"xl/worksheets/sheet\d*\.xml$", n))
            strings = []
            if "xl/sharedStrings.xml" in names:
                strings = ["".join(t.text or "" for t in si.iter() if t.tag.endswith("}t"))
                           for si in fromstring(z.read("xl/sharedStrings.xml")) if si.tag.endswith("}si")]
            rows, date_col, dates = 0, None, []
            with z.open(sheet) as f:
                for _, el in iterparse(f):
                    if not el.tag.endswith("}row"):
                        continue
                    rows += 1
                    for i, c in enumerate(x for x in el if x.tag.endswith("}c")):
                        v = next((x.text for x in c if x.tag.endswith("}v")), None)
                        if v is None:
                            continue
                        if rows == 1 and c.get("t") == "s" and date_col is None                                 and "date" in strings[int(v)].lower():
                            date_col = i  # first column with "date" in its heading
                        elif rows > 1 and i == date_col:
                            try:
                                dates.append(float(v))
                            except ValueError:
                                pass
                    el.clear()
        data_rows = max(rows - 1, 0)  # minus the header row
    except Exception as e:
        log(f"PLEASE CHECK '{report_name}': could not count the rows ({type(e).__name__} {str(e)[:100]}). "
            "Open the file and check it.")
        return
    size_mb = path.stat().st_size / 1_048_576
    span = ""
    if dates:  # Excel stores dates as days since 30-Dec-1899
        oldest, newest = (date(1899, 12, 30) + timedelta(days=int(d)) for d in (min(dates), max(dates)))
        span = f", dates {oldest:%d-%b-%Y} to {newest:%d-%b-%Y}"
    log(f"'{report_name}': {data_rows:,} rows{span}, {size_mb:.1f} MB")
    if rows >= EXCEL_MAX_ROWS:
        log(f"WARNING '{report_name}': the file is at Excel's maximum of {EXCEL_MAX_ROWS:,} rows - data is MISSING.")
    elif data_rows and data_rows % 10_000 == 0:
        log(f"WARNING '{report_name}': exactly {data_rows:,} rows looks like an export limit - data may be MISSING.")
    log(f">> PLEASE CHECK '{report_name}' is complete: open {path.name} and check the dates cover all the "
        f"years and the row count ({data_rows:,}) matches MSD.")


def export_grid(context, page: Page, report_name: str, started: float) -> None:
    """Office icon -> EXPORT TO EXCEL: <report> -> Download (right after the dates; no Generate)."""
    # Only the dates are changed; Zone/Region/Dealer code stay empty (= all).
    wait_until_idle(page)
    log("Exporting to Excel")

    # The Office icon in the page toolbar ("Open in Microsoft Office").
    office = page.locator(
        "button[id$='SystemDefinedOfficeButton'], button[name='SystemDefinedOfficeButton'], "
        "button[aria-label*='Microsoft Office' i], button[title*='Microsoft Office' i]"
    ).locator("visible=true").first
    # The menu item right below the "EXPORT TO EXCEL" heading with the report's name
    # (the page title has the same text, so search only after the heading).
    heading = page.get_by_text(re.compile(r"^\s*Export to Excel\s*$", re.I)).locator("visible=true").first
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    wanted = report_name.strip(" .").lower()
    named = heading.locator(
        f"xpath=following::*[starts-with(translate(normalize-space(.), '{upper}', '{upper.lower()}'), '{wanted}')]"
    ).locator("visible=true").first
    # If no item has the report's name, take the first menu item right below the heading.
    below = heading.locator(
        "xpath=following::*[self::button or @role='menuitem' or self::a][normalize-space(.)]"
    ).locator("visible=true").first

    for attempt in range(1, 6):
        if not heading.is_visible():  # the icon opens/closes the menu, so only click when it's closed
            click_when_ready(page, office, "Open in Microsoft Office")
        try:
            heading.wait_for(state="visible", timeout=60_000)  # slow on big pages (e.g. GMA stock)
            item = named if named.count() else below
            log(f"Clicking 'Export to Excel' -> '{item.inner_text(timeout=15_000).strip()}'")
            item.click(timeout=15_000)
            break
        except Exception:
            if attempt == 5:
                raise RuntimeError(f"The Office menu did not show 'Export to Excel: {report_name}'.")
            log("Office menu not ready - trying again")
            # No Escape here: in MSD it closes the whole page, not just the menu.
            page.wait_for_timeout(5000)

    # Side panel "Export to Excel" -> Download
    # Matched by its text; the button's accessible name isn't just "Download".
    download_re = re.compile(r"^\W*Download\s*$", re.I)
    download_btn = (
        page.locator("button, [role='button'], a").filter(has_text=download_re)
        .or_(page.get_by_text(download_re))
    ).locator("visible=true").first
    # Big lists (e.g. Parts stock) show "Please wait. We're processing your request" for many minutes first.
    wait_long(context, started, "'Export to Excel' panel with the Download button", lambda: download_btn.count() > 0,
              restart_on_reconnect=True)
    click_when_ready(page, download_btn, "Download")


def export_tilldate(context, page: Page, started: float, downloads: list) -> None:
    """Press OK; MSD then downloads the file, or shows a Download button to click."""
    # Only the Till date is changed; Dealer/Zone/Region stay empty (= all).
    ok = page.get_by_role("button", name=re.compile(r"^\s*OK\s*$")).locator("visible=true").last
    log("Pressing OK")
    click_when_ready(page, ok, "OK")

    download_re = re.compile(r"^\W*Download\s*$", re.I)

    def download_or_button() -> bool:
        if downloads:
            return True
        btn = current_page(context).locator("button, [role='button'], a").filter(
            has_text=download_re).locator("visible=true")
        if btn.count():
            log("Clicking 'Download'")
            click_when_ready(current_page(context), btn.first, "Download")
            return True
        return False

    wait_long(context, started, "Download button", download_or_button)


def export_ssrs(context, page: Page, from_date: date, to_date: date, started: float) -> None:
    """Press OK, wait for the report on screen, then Export -> Excel."""
    # Only the dates are changed; Zone/Region/ASM/Dealer stay as MSD fills them.
    ok = page.locator("button[id$='_CommandButton']:visible").first  # the dialog's OK button
    log("Pressing OK")
    click_when_ready(page, ok, "OK")
    log(f"OK pressed - MSD is building the report ({from_date:%d-%b-%Y} to {to_date:%d-%b-%Y}). "
        "A full month can take 2+ hours; keep this window open.")

    # 1) Wait until the report is shown on screen (its toolbar has an "Export" button).
    export_btn = page.locator("button:visible, [role='button']:visible, [role='menuitem']:visible").filter(
        has_text=re.compile(r"^\s*Export\s*$")).first
    wait_long(context, started, "report to appear on screen", lambda: export_btn.count() > 0)
    log(f"Report is on screen after {minutes(started)} min - exporting to Excel")

    # 2) Export -> Excel (retry if the menu doesn't open yet).
    excel_item = page.get_by_text("Excel", exact=True).locator("visible=true").first
    for attempt in range(1, 6):
        click_when_ready(page, export_btn, "Export")
        try:
            excel_item.wait_for(state="visible", timeout=15_000)
            excel_item.click()
            break
        except Exception:
            if attempt == 5:
                raise RuntimeError("The Export menu did not show the 'Excel' option.")
            log("Export menu not ready - trying again")
            page.keyboard.press("Escape")
            page.wait_for_timeout(5000)


class ExportLinkExpired(RuntimeError):
    """MSD's Excel download link is only valid for a short time; a very slow export can outlast it."""


def export_link_expired(context) -> bool:
    """True if a tab shows the Azure storage 'AuthenticationFailed' page instead of downloading the file."""
    for pg in context.pages:
        try:
            if not pg.is_closed() and "blob.core.windows.net" in pg.url                     and pg.get_by_text("AuthenticationFailed").count():  # count() never waits
                return True
        except Exception:
            pass
    return False


def unique_path(path: Path) -> Path:
    """'file.xlsx' -> 'file (2).xlsx', 'file (3).xlsx', ... if the name is already taken."""
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def minutes(since: float) -> int:
    return int((time.time() - since) // 60)


def wait_long(context, started: float, what: str, done, restart_on_reconnect: bool = False) -> None:
    """Wait (up to REPORT_TIMEOUT_SEC since `started`) until done() is true, logging every 5 minutes.
    Waits on any open tab, because MSD closes its own tab after the Excel export."""
    next_note = time.time() + 300
    while not done():
        if browser_alive(context) and reconnect_if_needed(current_page(context)) and restart_on_reconnect:
            raise ExportLinkExpired("MSD lost its connection during the export (the export is lost).")
        if time.time() - started > REPORT_TIMEOUT_SEC:
            raise TimeoutError(f"Gave up waiting for the {what} after {REPORT_TIMEOUT_SEC // 3600} hours.")
        if time.time() >= next_note:
            log(f"Still waiting for the {what}... {minutes(started)} min so far")
            next_note += 300
            try:
                current_page(context).screenshot(path=str(LOG_DIR / f"progress_{file_tag() or 'main'}.png"))
            except Exception:
                pass
        any_open_page(context).wait_for_timeout(5000)


def report_period(today: date, today_only: bool) -> tuple[date, date]:
    """1st of this month to today. On the 1st of a month: the whole previous month instead.
    --today (test mode): only today."""
    if today_only:
        return today, today
    if today.day == 1:
        last_of_prev = today - timedelta(days=1)
        return last_of_prev.replace(day=1), last_of_prev
    return today.replace(day=1), today


def main() -> int:
    today_only = "--today" in sys.argv  # test mode: only today's data (a full month takes 1+ hour)
    reports = REPORTS
    if "--report" in sys.argv:  # one named report only, e.g. --report "Sales register"
        i = sys.argv.index("--report")
        wanted = sys.argv[i + 1].strip().lower() if i + 1 < len(sys.argv) else ""
        reports = [r for r in REPORTS + SEPARATE_REPORTS if r[0].strip(" .").lower() == wanted]
        if not reports:
            names = ", ".join(r for r, _ in REPORTS + SEPARATE_REPORTS)
            sys.exit(f"ERROR: unknown report '{wanted}'. Choose one of: {names}")
    accounts = {REPORT_ACCOUNT.get(r, "") for r, _ in reports}
    if len(accounts) > 1:
        sys.exit("ERROR: these reports use different MSD logins - run them separately.")
    cfg = load_config(accounts.pop())
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    log(f"User: {cfg['email']}")
    for leftover in DOWNLOAD_DIR.glob("*"):  # unfinished downloads from an interrupted run
        if leftover.is_file():
            leftover.unlink(missing_ok=True)

    # 1) Log in once in one browser (handles SSO / OTP) and save the login cookies.
    try:
        with sync_playwright() as p:
            context = None
            try:
                context = login(p, cfg)
                current_page(context).screenshot(path=str(LOG_DIR / "landing_page.png"))
                log("Logged in to MSD.")
                save_session(context, cfg)
            except Exception:
                if context is not None:
                    save_error_screenshot(context)
                raise
            finally:
                if context is not None:
                    close_browser(context)
    except Exception as e:
        log(f"FAILED: {e}")
        input("\nPress Enter to close...")
        return 1

    today = date.today()
    from_date, to_date = report_period(today, today_only)
    log(f"Report period: {from_date:%d-%b-%Y} to {to_date:%d-%b-%Y}")
    run_dir = DOWNLOAD_DIR / f"{today:%Y-%m-%d}"  # one folder per day, one subfolder per report
    run_dir.mkdir(parents=True, exist_ok=True)
    log(f"Saving to {run_dir}")

    # 2) All reports at the same time, each in its own browser (uses the saved login).
    failed: list[str] = []
    threads = []
    for report, kind in reports:
        t = threading.Thread(target=report_worker, name=report.strip(" ."),
                             args=(cfg, report, kind, from_date, to_date, run_dir, failed))
        t.start()
        threads.append(t)
        time.sleep(5)  # don't open all browsers in the same second
    log(f"Started {len(reports)} browser(s): {', '.join(r for r, _ in reports)}")
    for t in threads:
        t.join()

    if failed:
        log(f"Finished with problems. Not downloaded: {', '.join(failed)}")
        input("\nPress Enter to close...")
        return 1
    log(f"SUCCESS: all {len(reports)} report(s) downloaded to {run_dir}")
    input("\nPress Enter to close...")
    return 0


def login(p, cfg: dict):
    """Open a browser and get to the MSD landing page. Returns the browser context."""
    context = None
    for round_no in range(1, LOGIN_ROUNDS + 1):
        try:
            # (Re)open the browser if this is the first try or it was closed/crashed.
            if not browser_alive(context):
                if context is not None:
                    log("Browser was closed - opening it again.")
                    close_browser(context)
                context = launch_browser(p, cfg)

            page = current_page(context)
            log(f"Opening {cfg['url']} (attempt {round_no} of {LOGIN_ROUNDS})")
            page.goto(cfg["url"], wait_until="commit", timeout=120_000)
            if login_attempt(page, cfg, LOGIN_TIMEOUT_SEC):
                log("Landing page reached.")
                return context
            log("Landing page not reached yet - starting login again.")
            save_error_screenshot(context)
        except RuntimeError:
            if context is not None:
                close_browser(context)
            raise  # wrong password etc.: retrying won't help
        except Exception as e:
            log(f"Login problem ({str(e).splitlines()[0][:150]}) - starting login again.")
    if context is not None:
        close_browser(context)
    raise TimeoutError(f"Could not reach the MSD landing page after {LOGIN_ROUNDS} attempts.")


def report_worker(cfg: dict, report: str, kind: str, from_date: date, to_date: date, run_dir: Path,
                  failed: list[str]) -> None:
    """Runs in its own thread: own Playwright + own browser (Playwright isn't shared across threads)."""
    try:
        with sync_playwright() as p:
            for attempt in range(1, EXPORT_TRIES + 1):
                context = None
                try:
                    context = login(p, cfg)
                    run_report(context, current_page(context), report, kind, from_date, to_date, run_dir)
                    return
                except ExportLinkExpired as e:
                    log(f"{e} Attempt {attempt} of {EXPORT_TRIES}.")
                    if context is not None:
                        save_error_screenshot(context)
                    if attempt == EXPORT_TRIES:
                        log("FAILED: the export is too slow for MSD's download link - try again later, "
                            "when MSD is less busy, and run this report on its own.")
                        failed.append(report)
                    else:
                        log("Starting the export again")
                except Exception as e:
                    log(f"FAILED: {str(e).splitlines()[0][:200]}")
                    if context is not None:
                        save_error_screenshot(context)
                    failed.append(report)
                    return
                finally:
                    if context is not None:
                        close_browser(context)  # this report is done; the others keep running
    except Exception as e:
        log(f"FAILED: could not start Playwright ({str(e).splitlines()[0][:150]})")
        failed.append(report)


if __name__ == "__main__":
    sys.exit(main())
