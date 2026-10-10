"""Read-only data API. Customers send header:  X-API-Key: <key>
Run:  uvicorn api:app --host 0.0.0.0 --port 8000"""
import csv
import io
import os
import secrets
import sys
import time
import hashlib
from typing import Dict, List

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db

# Rate limiting storage
_public_requests: Dict[str, List[float]] = {}
_key_requests: Dict[str, List[float]] = {}

RATE_LIMIT_PUBLIC = 10  # per minute
RATE_LIMIT_KEY = 100    # per minute
RATE_LIMIT_WINDOW = 60  # seconds

def _cleanup_old(timestamps: List[float], now: float) -> List[float]:
    cutoff = now - RATE_LIMIT_WINDOW
    return [ts for ts in timestamps if ts >= cutoff]

app = FastAPI(title="PriceWatch Nigeria", version="0.1")

# --- dashboard static mount -------------------------------------------------
# Another agent owns the dashboard/ directory; make sure it exists so the
# mount never crashes, but NEVER overwrite a real index.html.
if not os.path.isdir("dashboard"):
    os.makedirs("dashboard", exist_ok=True)
if not os.path.exists(os.path.join("dashboard", "index.html")):
    with open(os.path.join("dashboard", "index.html"), "w") as f:
        f.write("dashboard loading\n")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    now = time.time()
    path = request.url.path

    # Skip rate limiting for certain paths
    if path in ["/docs", "/redoc", "/openapi.json", "/favicon.ico"]:
        return await call_next(request)

    # Determine if the endpoint is public or key-gated
    if path.startswith("/public/") or path in ["/", "/health"]:
        key = request.client.host if request.client else "unknown"
        storage = _public_requests
        limit = RATE_LIMIT_PUBLIC
    else:
        # For key-gated, we try to get the API key from the header
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # If no key, we'll let the require_key dependency handle it (return 401)
            return await call_next(request)
        key = api_key
        storage = _key_requests
        limit = RATE_LIMIT_KEY

    # Cleanup old entries for this key
    if key in storage:
        storage[key] = _cleanup_old(storage[key], now)
    else:
        storage[key] = []

    # Check if over limit
    if len(storage[key]) >= limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"}
        )

    # Add current timestamp
    storage[key].append(now)

    # Proceed
    response = await call_next(request)
    return response


@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/public/") or request.url.path in ["/health"]:
        response.headers["Cache-Control"] = "public, max-age=60"
    return response


def require_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key header")
    # Hash the provided key for comparison with stored hash
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    with db.connect() as c:
        row = c.execute("SELECT 1 FROM api_keys WHERE key_hash=? AND active=1", (key_hash,)).fetchone()
    if not row:
        raise HTTPException(403, "Invalid or disabled API key")


@app.get("/")
def root():
    return RedirectResponse("/dashboard", status_code=307)


@app.get("/api-info")
def api_info():
    return {"service": "PriceWatch Nigeria", "docs": "/docs", "auth": "send header X-API-Key",
            "endpoints": ["/latest", "/items", "/prices/{item}", "/health",
                          "/public/items", "/public/latest", "/public/prices/{item}",
                          "/export/{item}.csv"]}


@app.get("/health")
def health():
    return {"status": "ok"}


def _items_rows(c, exclude_demo=False):
    if exclude_demo:
        return c.execute(
            "SELECT item, unit, MIN(collected_date) AS first_date, MAX(collected_date) AS last_date, "
            "COUNT(*) AS readings FROM prices WHERE source != 'demo' GROUP BY item, unit ORDER BY item"
        ).fetchall()
    else:
        return c.execute(
            "SELECT item, unit, MIN(collected_date) AS first_date, MAX(collected_date) AS last_date, "
            "COUNT(*) AS readings FROM prices GROUP BY item, unit ORDER BY item"
        ).fetchall()


@app.get("/items", dependencies=[Depends(require_key)])
def items():
    with db.connect() as c:
        rows = _items_rows(c, exclude_demo=True)
    return [dict(r) for r in rows]


@app.get("/public/items")
def public_items():
    with db.connect() as c:
        rows = _items_rows(c)
    return [dict(r) for r in rows]


def _latest_rows(c, exclude_demo=False):
    if exclude_demo:
        return c.execute(
            """SELECT p.item, p.source, p.value, p.unit, p.collected_date FROM prices p
               JOIN (SELECT item, source, MAX(collected_date) d FROM prices GROUP BY item, source) m
               ON p.item=m.item AND p.source=m.source AND p.collected_date=m.d
               WHERE p.source != 'demo'
               ORDER BY p.item"""
        ).fetchall()
    else:
        return c.execute(
            """SELECT p.item, p.source, p.value, p.unit, p.collected_date FROM prices p
               JOIN (SELECT item, source, MAX(collected_date) d FROM prices GROUP BY item, source) m
               ON p.item=m.item AND p.source=m.source AND p.collected_date=m.d
               ORDER BY p.item"""
        ).fetchall()


@app.get("/latest", dependencies=[Depends(require_key)])
def latest():
    with db.connect() as c:
        rows = _latest_rows(c, exclude_demo=True)
    return [dict(r) for r in rows]


@app.get("/public/latest")
def public_latest():
    with db.connect() as c:
        rows = _latest_rows(c, exclude_demo=True)
    return [dict(r) for r in rows]


def _prices_query(c, item, start, end, limit, exclude_demo=False):
    sql = "SELECT item, source, value, unit, collected_date FROM prices WHERE item=?"
    args = [item]
    if start:
        sql += " AND collected_date >= ?"; args.append(start)
    if end:
        sql += " AND collected_date <= ?"; args.append(end)
    if exclude_demo:
        sql += " AND source != 'demo'"
    sql += " ORDER BY collected_date DESC"
    if limit is not None:
        sql += " LIMIT ?"; args.append(limit)
    return c.execute(sql, args).fetchall()


@app.get("/prices/{item}", dependencies=[Depends(require_key)])
def prices(item: str, start: str = Query(None, description="YYYY-MM-DD"),
             end: str = Query(None, description="YYYY-MM-DD"), limit: int = Query(365, ge=1, le=1000)):
    with db.connect() as c:
        rows = _prices_query(c, item, start, end, limit, exclude_demo=True)
    if not rows:
        raise HTTPException(404, f"No data for item {item!r}")
    return [dict(r) for r in rows]


@app.get("/public/prices/{item}")
def public_prices(item: str, start: str = Query(None, description="YYYY-MM-DD"),
                    end: str = Query(None, description="YYYY-MM-DD"),
                    limit: int = Query(365, ge=1, le=1000)):
    with db.connect() as c:
        rows = _prices_query(c, item, start, end, limit, exclude_demo=True)
    if not rows:
        raise HTTPException(404, f"No data for item {item!r}")
    return [dict(r) for r in rows]


@app.get("/export/{item}.csv", dependencies=[Depends(require_key)])
def export_csv(item: str, start: str = Query(None, description="YYYY-MM-DD"),
                end: str = Query(None, description="YYYY-MM-DD")):
    with db.connect() as c:
        rows = _prices_query(c, item, start, end, limit=None, exclude_demo=True)
    if not rows:
        raise HTTPException(404, f"No data for item {item!r}")
    # paid export: every matching row, oldest first
    rows = sorted((dict(r) for r in rows), key=lambda r: r["collected_date"])

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "item", "source", "value", "unit"])
        for r in rows:
            w.writerow([r["collected_date"], r["item"], r["source"], r["value"], r["unit"]])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"{item}_{start or 'all'}_{end or 'all'}.csv"
    return StreamingResponse(gen(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")


def make_key(owner):
    key = "pw_" + secrets.token_urlsafe(24)
    with db.connect() as c:
        c.execute("INSERT INTO api_keys (key, owner) VALUES (?,?)", (key, owner))
    return key


if __name__ == "__main__":
    # python api.py newkey "Customer Name"
    if len(sys.argv) == 3 and sys.argv[1] == "newkey":
        print(make_key(sys.argv[2]))
    else:
        print('usage: python api.py newkey "Customer Name"')
