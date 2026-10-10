"""OPT-IN synthetic history so the dashboard/API have data before real sources
accumulate. Rows are clearly labeled source="demo" — never mixed into "real"
series. Usage:  .venv/bin/python seed_demo.py --days 730

Values are smooth trends + seasonality + noise around plausible 2024-26
Nigerian levels. They are NOT measurements. Do not ship demo rows to paying
customers without filtering them out (they are identifiable by source='demo')."""
import argparse
import math
import random
import sys
import zlib
from datetime import date, timedelta

import db

# slug: (unit, start_value_2024, end_value_2026, seasonal_amplitude, volatility)
ITEMS = {
    "usd_ngn_official": ("NGN per USD", 900.0, 1450.0, 40.0, 8.0),
    "usd_ngn_parallel": ("NGN per USD", 1050.0, 1580.0, 60.0, 15.0),
    "petrol_pms": ("NGN per litre", 650.0, 900.0, 25.0, 8.0),
    "diesel_ago": ("NGN per litre", 1100.0, 1400.0, 50.0, 20.0),
    "lpg_12_5kg": ("NGN per 12.5kg cylinder", 9500.0, 14500.0, 500.0, 150.0),
    "rice": ("NGN per kg", 1300.0, 2200.0, 120.0, 30.0),
    "beans": ("NGN per kg", 1200.0, 2000.0, 150.0, 30.0),
    "tomatoes": ("NGN per kg", 900.0, 1800.0, 400.0, 120.0),
    "garri": ("NGN per kg", 700.0, 1100.0, 60.0, 15.0),
    "bus_fare": ("NGN per trip", 350.0, 700.0, 10.0, 10.0),
    "bread": ("NGN per loaf", 1200.0, 2000.0, 80.0, 20.0),
}


def value_on(slug, day: date) -> float:
    unit, start, end, amp, vol = ITEMS[slug]
    t0, t1 = date(2024, 1, 1), date(2026, 10, 1)
    frac = (day - t0).days / (t1 - t0).days
    base = start + (end - start) * min(max(frac, 0.0), 1.1)
    phase = (zlib.crc32(slug.encode()) % 628) / 100.0
    season = amp * math.sin(2 * math.pi * (day.timetuple().tm_yday / 365.0) + phase)
    # deterministic per (slug, day) so re-running is stable/idempotent
    noise = random.Random(f"{slug}:{day.isoformat()}").gauss(0, vol)
    return round(max(base + season + noise, 1.0), 2)


def seed(days: int, today: date | None = None) -> int:
    today = today or date.today()
    rows = 0
    with db.connect() as c:
        for slug, (unit, *_rest) in ITEMS.items():
            for i in range(days - 1, -1, -1):
                day = (today - timedelta(days=i)).isoformat()
                # upsert so re-running is safe and idempotent per day
                c.execute(
                    """INSERT INTO prices (item, source, value, unit, collected_date)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(item, source, collected_date)
                       DO UPDATE SET value=excluded.value""",
                    (slug, "demo", value_on(slug, today - timedelta(days=i)), unit, day),
                )
                rows += 1
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Insert clearly-labeled DEMO history (source='demo').")
    p.add_argument("--days", type=int, required=True, help="how many days back to generate")
    args = p.parse_args()
    n = seed(args.days)
    print(f"seeded {n} demo rows ({len(ITEMS)} items x {args.days} days); source='demo'")
    sys.exit(0)
