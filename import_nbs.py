"""Import NBS (National Bureau of Statistics) monthly Excel price watches.

NBS publishes monthly Excel workbooks (Petrol Price Watch, Diesel Price Watch,
Selected Food Prices Watch, Transport Fare Watch). There is no daily HTML table
to scrape, so history is imported here instead — one command per new report.

Usage:
    .venv/bin/python import_nbs.py --file ~/Downloads/selected_food_june_2026.xlsx
    .venv/bin/python import_nbs.py --file petrol_may_2026.xlsx --dry-run

How it works (defensive — NBS layouts drift):
  1. every sheet is scanned for a header row (a row containing 'price' cells)
  2. a 'National Average' column is preferred; otherwise the LAST numeric
     column (NBS convention) is used; if neither can be identified the file is
     rejected loudly rather than guessing
  3. rows are matched to items by keyword (ITEM_KEYWORDS); unmatched rows are
     skipped and reported
  4. the report month is parsed from a month column or the filename; readings
     are stored on the 15th of that month (mid-month marks a monthly average,
     not a daily point); source='nbs'
  5. sanity ranges reject implausible values

Real NBS files vary; if a layout defeats this importer it fails with the
headers it saw, so the keyword/column logic can be fixed against reality.
"""
import argparse
import re
import sys
from pathlib import Path

import openpyxl

import db

SOURCE = "nbs"

# keyword -> (item slug, unit, min, max); first keyword hit wins per row
ITEM_KEYWORDS = [
    ("petrol", "petrol_pms", "NGN per litre", 50, 5000),
    ("pms", "petrol_pms", "NGN per litre", 50, 5000),
    ("diesel", "diesel_ago", "NGN per litre", 50, 10000),
    ("ago", "diesel_ago", "NGN per litre", 50, 10000),
    ("cooking gas", "lpg_12_5kg", "NGN per 12.5kg cylinder", 1000, 100000),
    ("rice", "rice", "NGN per kg", 50, 30000),
    ("beans", "beans", "NGN per kg", 50, 30000),
    ("tomato", "tomatoes", "NGN per kg", 50, 30000),
    ("garri", "garri", "NGN per kg", 50, 30000),
    ("bread", "bread", "NGN per loaf", 100, 20000),
    ("bus", "bus_fare", "NGN per trip", 50, 10000),
    ("transport", "bus_fare", "NGN per trip", 50, 10000),
]

_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
# matches: "April 2026", "Apr-2026", "April, 2026", "april_2026", and numeric "2026-06"
MONTH_RE = re.compile(
    rf"{_MON}[a-z]*[\s,_\-/.]+(\d{{4}})"
    rf"|(\d{{4}})[\s,_\-/.]+{_MON}[a-z]*"
    rf"|(\d{{4}})[\s,_\-/.]+(\d{{1,2}})(?!\d)",
    re.I,
)
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _as_text(v):
    return "" if v is None else str(v).strip()


HEADER_TOKENS = ("price", "fare", "average", "month", "date", "item",
                 "unit", "commodity", "state", "year", "route", "transport")


def find_header(rows, max_scan=15):
    """First row that looks like a table header: it mentions a price/fare column
    *and* at least one other column word. A title row like 'Petrol Price Watch'
    has only one token and is correctly skipped."""
    for i, cells in enumerate(rows[:max_scan]):
        joined = " ".join(_as_text(c).lower() for c in cells)
        if not any(t in joined for t in ("price", "fare")):
            continue
        if sum(1 for t in HEADER_TOKENS if t in joined) >= 2:
            return i, [_as_text(c).lower() for c in cells]
    return None, None


def pick_price_col(header):
    """Prefer a 'national average' column, else the last column whose header
    mentions price/fare/average. Returns index or None."""
    for want in ("national average", "national"):
        for j, h in enumerate(header):
            if want in h:
                return j
    candidates = [j for j, h in enumerate(header)
                  if ("price" in h or "fare" in h or "average" in h)]
    return candidates[-1] if candidates else None


def parse_month(text):
    """'April, 2024' / '2024-06' / 'Jun-2026' / 'april_2026' -> 'YYYY-MM-15'."""
    m = MONTH_RE.search(text)
    if not m:
        return None
    if m.group(1):
        month, year = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
    elif m.group(3):
        year, month = int(m.group(3)), MONTHS[m.group(4)[:3].lower()]
    else:
        year, month = int(m.group(5)), int(m.group(6))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}-15"


def month_from_filename(path):
    return parse_month(Path(path).name)


def match_item(row_text):
    """Leading word-boundary match. Guards the classic trap where 'rice' is a
    substring of 'price' (and 'ago' of 'Lagos'), while still matching plurals
    like 'tomatoes'/'beans' that a trailing \\b would reject."""
    t = row_text.lower()
    for kw, slug, unit, lo, hi in ITEM_KEYWORDS:
        if re.search(rf"(?<![a-z]){re.escape(kw)}", t):
            return slug, unit, lo, hi
    return None


def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", _as_text(v).replace(",", ""))
    return float(m.group()) if m else None


def import_file(path, item_filter=None, dry_run=False):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    found = []
    rejected = []
    for sheet in wb.worksheets:
        rows = [[c for c in row] for row in sheet.iter_rows(values_only=True)]
        hi, header = find_header(rows)
        if header is None:
            continue
        pcol = pick_price_col(header)
        if pcol is None:
            rejected.append(f"sheet {sheet.title!r}: header found but no price/fare column among {header}")
            continue
        # month column: any header cell mentioning month/date
        mcol = next((j for j, h in enumerate(header) if "month" in h or "date" in h), None)
        fallback_month = month_from_filename(path)
        # single-commodity reports (petrol/diesel/LPG/transport) name the item in
        # the sheet title or header, not in each data row
        sheet_ctx = f"{sheet.title} {' '.join(header)} {Path(path).name}"
        for row in rows[hi + 1:]:
            cells = list(row) + [None] * (len(header) - len(row))
            row_text = " ".join(_as_text(c) for c in cells)
            hit = match_item(row_text) or match_item(sheet_ctx)
            if not hit:
                continue
            slug, unit, lo, hi_ = hit
            if item_filter and slug != item_filter:
                continue
            when = parse_month(_as_text(cells[mcol])) if mcol is not None else None
            when = when or fallback_month
            if not when:
                rejected.append(f"could not determine month for row {row_text[:80]!r} (no month column, filename has no month)")
                continue
            value = to_number(cells[pcol]) if pcol < len(cells) else None
            if value is None:
                rejected.append(f"no number in price column for {slug} at {when}")
                continue
            if not (lo <= value <= hi_):
                rejected.append(f"{slug} value {value} outside sanity [{lo}, {hi_}] at {when} — skipped")
                continue
            found.append((slug, value, unit, when))
    wb.close()

    if not dry_run:
        for slug, value, unit, when in found:
            db.save_price(slug, SOURCE, value, unit, when=when)
    return found, rejected


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Import an NBS monthly Excel price watch.")
    p.add_argument("--file", required=True, help="path to the NBS .xlsx file")
    p.add_argument("--item", help="only import this item slug (e.g. petrol_pms)")
    p.add_argument("--dry-run", action="store_true", help="report what would be imported, write nothing")
    args = p.parse_args()
    found, rejected = import_file(args.file, item_filter=args.item, dry_run=args.dry_run)
    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {len(found)} readings:")
    for slug, value, unit, when in found:
        print(f"  {when}  {slug:<16} {value:>10.2f}  {unit}")
    if rejected:
        print(f"skipped {len(rejected)}:", file=sys.stderr)
        for r in rejected:
            print(f"  - {r}", file=sys.stderr)
    sys.exit(0 if found else 1)
