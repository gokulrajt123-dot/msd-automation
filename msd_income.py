"""Income for Paisa (the Money Management app) from the downloaded MSD reports.

Reads every downloaded Job Card Invoice Statement and Invoice statement, works out the
income per day per type, and hands those totals to Paisa, which files them in a book.

  msd_income.py             send the income to Paisa now
  msd_income.py --dry-run   only show what would be sent

Runs by itself after a download that included one of these reports, if PAISA_DIR is set in .env:
  PAISA_DIR=D:\\Claude\\VS Code Projects\\Money Management
  PAISA_BOOK=bharat-motors-udt

Income is without GST: the taxable value minus the dealer's own discount, i.e. what the
customer (or Royal Enfield, for free service / warranty / AMC coupons) pays the dealer, less GST.
  Job card line:  Basic Amount - Discount(Dealer Value)
  Vehicle:        Basic price (or Amount - Tax when that is empty); cancelled invoices skipped.
                  Paisa files vehicles as "Bike sales" = invoices (one bike each) x the per-bike
                  margin set in Paisa for that date, so only the invoice count matters there.

MSD hands out the same day several times as it fills up (and the files are never deleted),
so all files are read every time and each invoice is counted once, from the newest file
that has it. Paisa updates a day's total instead of adding it twice.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import msd_bot as bot

JOB_CARD_REPORT = "Job Card Invoice Statement"
VEHICLE_REPORT = "Invoice statement"
INCOME_REPORTS = {JOB_CARD_REPORT, VEHICLE_REPORT}

# Job card "Item Group" -> Paisa type. NPO (non-programmed order) parts count as spares.
ITEM_GROUP_TYPES = {"labour": "labour", "spares": "spares", "npo": "spares", "oil": "oil"}

EXCEL_EPOCH = date(1899, 12, 30)


# ---------- reading .xlsx (MSD writes them with an "x:" namespace prefix, so read the XML directly) ----------

def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_rows(path: Path) -> list[list[str]]:
    """All rows of the first sheet, as text. Empty cells are '' (also cells MSD left out)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8-sig")
            for si in re.findall(r"<(?:x:)?si>(.*?)</(?:x:)?si>", xml, re.S):
                strings.append(html.unescape("".join(re.findall(r"<(?:x:)?t[^>]*>(.*?)</(?:x:)?t>", si, re.S))))
        sheet = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]
        xml = z.read(sheet).decode("utf-8-sig")
    rows = []
    for row in re.findall(r"<(?:x:)?row[^>]*>(.*?)</(?:x:)?row>", xml, re.S):
        cells: dict[int, str] = {}
        for attrs, body in re.findall(r"<(?:x:)?c\b([^>]*?)(?:/>|>(.*?)</(?:x:)?c>)", row, re.S):
            ref = re.search(r'\br="([A-Z]+)\d+"', attrs)
            col = _col_index(ref.group(1)) if ref else len(cells)
            v = re.search(r"<(?:x:)?v>(.*?)</(?:x:)?v>", body or "", re.S)
            if 't="s"' in attrs and v:
                value = strings[int(v.group(1))]
            elif v:
                value = html.unescape(v.group(1))
            else:  # inline string
                value = html.unescape("".join(re.findall(r"<(?:x:)?t[^>]*>(.*?)</(?:x:)?t>", body or "", re.S)))
            cells[col] = value
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def records(path: Path, first_header: str) -> list[dict[str, str]]:
    """Rows under the header row (the one starting with first_header), as {column: value}."""
    rows = read_rows(path)
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == first_header), None)
    if start is None:
        return []
    header = [h.strip() for h in rows[start]]
    return [dict(zip(header, r)) for r in rows[start + 1:] if r and r[0].strip()]


def to_decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


def to_date(text: str) -> date | None:
    """Excel day number (46290) or a written date (25-09-2026, 25/09/2026, 2026-09-25, 25-Sep-2026)."""
    text = text.strip()
    if not text:
        return None
    try:
        return EXCEL_EPOCH + timedelta(days=int(float(text)))
    except ValueError:
        pass
    text = text.split(" ")[0].split("T")[0]
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


# ---------- income ----------

def report_files(report: str) -> list[Path]:
    """Every downloaded file of this report, oldest first (so a newer copy of an invoice wins)."""
    files = [p for p in bot.DOWNLOAD_DIR.glob(f"**/{report}/*.xlsx") if not p.name.startswith("~$")]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def branch_of(path: Path) -> str:
    """The branch a downloaded file belongs to: downloads/<branch>/Reports/... ('' without BRANCHES=),
    or downloads/<day>/<branch>/... in older downloads.
    Files from before BRANCHES= was set have no branch folder; they count for the first branch."""
    names = [b for b in bot.branches() if b]
    parts = path.relative_to(bot.DOWNLOAD_DIR).parts
    for part in parts[:2]:
        if part in names:
            return part
    return names[0] if names else ""


def newest_per_invoice(report: str, first_header: str) -> dict[tuple[str, str], list[dict[str, str]]]:
    """(branch, invoice number) -> its lines, from the newest file that has that invoice."""
    invoices: dict[tuple[str, str], list[dict[str, str]]] = {}
    for path in report_files(report):
        try:
            branch = branch_of(path)
            found: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
            for rec in records(path, first_header):
                found[(branch, rec[first_header].strip())].append(rec)
            invoices.update(found)
        except (OSError, zipfile.BadZipFile, KeyError, IndexError) as e:
            bot.log(f"Paisa: skipped unreadable file {path.name} ({e})")
    return invoices


def daily_income() -> list[dict]:
    """[{date, type, amountMinor, invoices}] - income without GST per day per type,
    plus "branch" on each when BRANCHES= is set in .env (one row per branch per day per type)."""
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    counts: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for (branch, invoice), lines in newest_per_invoice(JOB_CARD_REPORT, "Invoice number").items():
        for line in lines:
            day = to_date(line.get("Invoice date", ""))
            kind = ITEM_GROUP_TYPES.get(line.get("Item Group", "").strip().lower())
            if day is None:
                bot.log(f"Paisa: job card {invoice} has no readable date - skipped")
                break
            if kind is None:
                bot.log(f"Paisa: job card {invoice}: unknown item group '{line.get('Item Group')}' - counted as spares")
                kind = "spares"
            key = (branch, day.isoformat(), kind)
            totals[key] += to_decimal(line.get("Basic Amount", "")) - to_decimal(line.get("Discount(Dealer Value)", ""))
            counts[key].add(invoice)

    for (branch, invoice), lines in newest_per_invoice(VEHICLE_REPORT, "Invoice number").items():
        for line in lines:
            if "cancel" in line.get("Status", "").lower():
                continue
            day = to_date(line.get("Date", ""))
            if day is None:
                bot.log(f"Paisa: vehicle invoice {invoice} has no readable date - skipped")
                continue
            amount = to_decimal(line.get("Basic price", ""))
            if not amount:
                amount = to_decimal(line.get("Amount", "")) - to_decimal(line.get("Tax", ""))
            key = (branch, day.isoformat(), "vehicle")
            totals[key] += amount
            counts[key].add(invoice)

    return [
        {**({"branch": b} if b else {}), "date": d, "type": t,
         "amountMinor": int((totals[(b, d, t)] * 100).quantize(Decimal(1))), "invoices": len(counts[(b, d, t)])}
        for b, d, t in sorted(totals)
    ]


# ---------- sending to Paisa ----------

def paisa_settings() -> tuple[Path | None, str, str]:
    """(Paisa folder or None when not set up, book slug, email) from .env."""
    from dotenv import dotenv_values
    raw = {k.upper(): (v or "").strip() for k, v in dotenv_values(bot.BASE_DIR / ".env").items()}
    folder = raw.get("PAISA_DIR", "")
    return (Path(folder) if folder else None), raw.get("PAISA_BOOK") or "bharat-motors-udt", raw.get("PAISA_EMAIL", "")


def send_to_paisa(dry_run: bool = False) -> bool:
    """Work out the income and file it in Paisa. Never raises: a problem here must not spoil a download."""
    try:
        folder, book, email = paisa_settings()
        if folder is None:
            return True  # not set up on this computer
        if not (folder / "package.json").exists():
            bot.log(f"Paisa: PAISA_DIR in .env is not the Money Management folder: {folder}")
            return False
        npm = shutil.which("npm")
        if not npm:
            bot.log("Paisa: Node.js (npm) is not installed - income not sent.")
            return False

        days = daily_income()
        payload: dict = {"days": days}
        names = [b for b in bot.branches() if b]
        if names:  # what Paisa got before BRANCHES= was set is the first branch's (see branch_of)
            payload["legacyBranch"] = names[0]
        bot.log(f"Paisa: sending income for {len({d['date'] for d in days})} day(s) to book '{book}'"
                + (" (dry run)" if dry_run else ""))
        cmd = [npm, "run", "--silent", "msd:income", "--", "--book", book]
        if email:
            cmd += ["--email", email]
        if dry_run:
            cmd.append("--dry-run")
        result = subprocess.run(cmd, cwd=folder, input=json.dumps(payload), text=True,
                                encoding="utf-8", capture_output=True, timeout=300)
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                bot.log(f"Paisa: {line.strip().replace('₹', 'Rs ')}")  # the console can't show ₹
        if result.returncode != 0:
            bot.log("Paisa: FAILED to file the income (see above). The downloads themselves are fine.")
            return False
        return True
    except Exception as e:
        bot.log(f"Paisa: FAILED to send the income: {str(e).splitlines()[0][:200]}")
        return False


if __name__ == "__main__":
    if "--show" in sys.argv:  # just print the totals, without Paisa
        for d in daily_income():
            print(f"{d['branch'] + '  ' if 'branch' in d else ''}{d['date']}  {d['type']:<8} Rs {d['amountMinor'] / 100:>12,.2f}  ({d['invoices']} invoices)")
        sys.exit(0)
    if paisa_settings()[0] is None:
        sys.exit("PAISA_DIR is not set in .env - see the top of msd_income.py.")
    sys.exit(0 if send_to_paisa(dry_run="--dry-run" in sys.argv) else 1)
