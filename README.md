# PriceWatch Nigeria

Logs prices daily, stores the history, serves it over an API-key-protected API,
and renders a free public dashboard.

## Setup
Use a virtual environment. Debian/Ubuntu/Termux-proot block global `pip install`
("externally-managed-environment"), so do not use `--break-system-packages`.
```
sudo apt install python3-venv     # only if `python3 -m venv` complains
python3 -m venv .venv
source .venv/bin/activate         # run this in every new terminal
pip install -r requirements.txt
python collect.py                 # run once now; pulls the official USD/NGN rate
python api.py newkey "My Test"    # prints an API key (stored as salted hash)
uvicorn api:app --port 8000       # dashboard at /dashboard, docs at /docs
```

## Run daily (cron)
Cron does not activate the venv, so call the venv's Python directly:
```
0 7 * * * cd /path/to/pricewatch && .venv/bin/python collect.py >> collect.log 2>&1
```
Exit code is 1 if any source failed, so a monitor (e.g. healthchecks.io) can alert you.

## Dashboard (free, no key)
Open `/` — it redirects to `/dashboard`, a static page that reads only the public
endpoints below. Shows the latest value per item, 7-day / 30-day change, a 30-day
sparkline, a per-item line chart (30d/90d/1y/all, Chart.js with a table fallback),
and refreshes every 60 seconds. Series whose `source` is `demo` are flagged with a
"includes demo data" badge — demo rows are always labeled and never mixed with real ones.

## Public API (no key)
```
GET /public/items                         # items, units, first/last date, reading counts
GET /public/latest                        # latest row per item+source
GET /public/prices/{item}?start&end&limit # history, newest first (limit <= 1000)
```
The key-gated endpoints are unchanged:
```
GET /health
GET /latest                               # requires X-API-Key
GET /items                                # requires X-API-Key
GET /prices/{item}?start&end              # requires X-API-Key
GET /export/{item}.csv?start&end          # requires X-API-Key; CSV download
GET /api-info                             # old root JSON (endpoint list)
```

## Query
```
curl -H "X-API-Key: pw_..." localhost:8000/latest
curl -H "X-API-Key: pw_..." "localhost:8000/prices/usd_ngn_official?start=2026-10-01"
curl -H "X-API-Key: pw_..." -OJ "localhost:8000/export/rice.csv?start=2025-01-01"
```

## Webhook alerts on failures
Set `PRICEWATCH_ALERT_WEBHOOK` to any URL that accepts a JSON POST. On every source
failure `collect.py` posts `{"service":"pricewatch","item","source","reason","kind","time"}`
(`kind` is `failure`, `empty_layout` when a selector matched nothing, or `run_summary`).
Alerting is best-effort and never raises: a broken webhook cannot make collection fail,
and the exit-code contract (1 iff any source failed) is unchanged. With no webhook set,
alerts are printed to stdout/cron log instead.

## NBS monthly data (Excel importer)
The National Bureau of Statistics publishes petrol, diesel, LPG, food and transport
prices as **monthly Excel workbooks**, not scrapeable HTML. Import each new report:
```
.venv/bin/python import_nbs.py --file ~/Downloads/selected_food_june_2026.xlsx --dry-run
.venv/bin/python import_nbs.py --file ~/Downloads/petrol_may_2026.xlsx
```
The importer finds the header row, prefers the "National Average" column (else the last
price column), matches rows to items by keyword, parses the report month (or the
filename), and stores readings on the 15th of that month under `source="nbs"`.
Out-of-range values and unreadable layouts are rejected with a report, never guessed.

## Demo data (opt-in)
To show the dashboard/API before real history accumulates:
```
.venv/bin/python seed_demo.py --days 730     # clearly-labeled source="demo" rows
```
Re-running is idempotent. Filter `source='demo'` out before showing paying customers.

## Adding a price source (no code)
Edit `sources.json`. For a web page, set `type: html_css`, the page `url`, and a CSS
`selector` pointing at the element holding the price. For a JSON API, set `type: json_api`,
`url`, and `path` (the key path to the number). Use `min`/`max` as a sanity range so a
layout/API change can't silently store garbage. Set `enabled: true` — but only after
verifying the source responds with a plausible value. Document unverified candidates with
`enabled: false` plus a `_note` explaining what to check. Only use public sources and
respect each site's terms; keep to one request per source per day.

## Test
```
.venv/bin/python -m pytest
```

## Next
- Sources for petrol, diesel, rice, tomatoes, bus fares and the parallel rate: see `SOURCES.md` for what was researched and why. Only the two official USD/NGN sources are enabled so far; the rest are documented candidates pending verification.
- Move to Postgres/Supabase when you pass a few customers (replace db.py).
- Put it behind a domain, then take subscriptions via Paystack/Flutterwave invoices.
