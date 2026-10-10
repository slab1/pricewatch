"""API contract tests. Runs against a throwaway SQLite file.

IMPORTANT: db.py reads PRICEWATCH_DB at import time, so the env var must be
set before `import api` (which does `import db`)."""
import csv
import io
import os
import sys
import tempfile
import hashlib

_TMPDIR = tempfile.mkdtemp(prefix="pw_test_")
os.environ["PRICEWATCH_DB"] = os.path.join(_TMPDIR, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402  (must come after env var)
db.DB_PATH = os.environ["PRICEWATCH_DB"]  # belt and braces

import api  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

READINGS = [
    # item, source, value, unit, collected_date
    ("usd_ngn_official", "cbn", 1331.0, "ngn/usd", "2026-10-01"),
    ("usd_ngn_official", "cbn", 1335.0, "ngn/usd", "2026-10-02"),
    ("usd_ngn_official", "cbn", 1340.0, "ngn/usd", "2026-10-03"),
    ("petrol", "nnpc", 770.0, "ngn/litre", "2026-10-02"),
    ("petrol", "nnpc", 775.0, "ngn/litre", "2026-10-03"),
]


@pytest.fixture(autouse=True)
def seed():
    with db.connect() as c:
        c.execute("DELETE FROM prices")
        c.execute("DELETE FROM api_keys")
        for r in READINGS:
            c.execute(
                "INSERT INTO prices (item, source, value, unit, collected_date) VALUES (?,?,?,?,?)", r
            )
        # Insert hashed keys for testing
        test_key_hash = hashlib.sha256(b'pw_test_key').hexdigest()
        off_key_hash = hashlib.sha256(b'pw_off_key').hexdigest()
        c.execute("INSERT INTO api_keys (key_hash, owner) VALUES (?, ?)", (test_key_hash, 'tests'))
        c.execute("INSERT INTO api_keys (key_hash, owner) VALUES (?, ?)", (off_key_hash, 'x'))
        c.execute("UPDATE api_keys SET active=0 WHERE key_hash=?", (off_key_hash,))


# --- public endpoints: no auth ---------------------------------------------

def test_public_items_no_key():
    r = client.get("/public/items")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) == 2
    keys = set(body[0])
    assert keys == {"item", "unit", "first_date", "last_date", "readings"}
    usd = next(x for x in body if x["item"] == "usd_ngn_official")
    assert usd["first_date"] == "2026-10-01"
    assert usd["last_date"] == "2026-10-03"
    assert usd["readings"] == 3


def test_public_latest_no_key():
    r = client.get("/public/latest")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) == 2
    assert set(body[0]) == {"item", "source", "value", "unit", "collected_date"}
    usd = next(x for x in body if x["item"] == "usd_ngn_official")
    assert usd["collected_date"] == "2026-10-03"
    assert usd["value"] == 1340.0


def test_public_prices_no_key_newest_first():
    r = client.get("/public/prices/usd_ngn_official")
    assert r.status_code == 200
    body = r.json()
    assert [x["collected_date"] for x in body] == ["2026-10-03", "2026-10-02", "2026-10-01"]
    assert set(body[0]) == {"item", "source", "value", "unit", "collected_date"}


def test_public_prices_range_and_limit():
    r = client.get("/public/prices/usd_ngn_official",
                   params={"start": "2026-10-02", "end": "2026-10-02"})
    assert r.status_code == 200
    assert [x["collected_date"] for x in r.json()] == ["2026-10-02"]
    r = client.get("/public/prices/usd_ngn_official", params={"limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1
    # limit > 1000 rejected (422 by Query(le=1000))
    r = client.get("/public/prices/usd_ngn_official", params={"limit": 1001})
    assert r.status_code == 422


def test_public_prices_unknown_item_404():
    r = client.get("/public/prices/no-such-item")
    assert r.status_code == 404


# --- export: key-gated CSV --------------------------------------------------

def test_export_without_key_401():
    r = client.get("/export/usd_ngn_official.csv")
    assert r.status_code == 401


def test_export_bad_key_403():
    r = client.get("/export/usd_ngn_official.csv", headers={"X-API-Key": "pw_nope"})
    assert r.status_code == 403
    r = client.get("/export/usd_ngn_official.csv", headers={"X-API-Key": "pw_off_key"})
    assert r.status_code == 403  # disabled key


def test_export_with_key_csv_body():
    r = client.get("/export/usd_ngn_official.csv", headers={"X-API-Key": "pw_test_key"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="usd_ngn_official_all_all.csv"' in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["date", "item", "source", "value", "unit"]
    assert [row[0] for row in rows[1:]] == ["2026-10-01", "2026-10-02", "2026-10-03"]  # oldest first
    assert all(row[1] == "usd_ngn_official" for row in rows[1:])
    assert len(rows) == 4  # all rows, no limit cap


def test_export_with_dates_in_filename():
    r = client.get("/export/petrol.csv", headers={"X-API-Key": "pw_test_key"},
                   params={"start": "2026-10-02", "end": "2026-10-03"})
    assert r.status_code == 200
    assert 'filename="petrol_2026-10-02_2026-10-03.csv"' in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert [row[0] for row in rows[1:]] == ["2026-10-02", "2026-10-03"]


def test_export_unknown_item_404():
    r = client.get("/export/nope.csv", headers={"X-API-Key": "pw_test_key"})
    assert r.status_code == 404


# --- root redirect + info ---------------------------------------------------

def test_root_redirects_to_dashboard():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard"


def test_api_info_preserves_old_root_payload():
    r = client.get("/api-info")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "PriceWatch Nigeria"
    assert body["docs"] == "/docs"
    assert body["auth"] == "send header X-API-Key"
    assert "/prices/{item}" in body["endpoints"]


# --- existing endpoints keep behavior --------------------------------------

def test_health_open():
    assert client.get("/health").status_code == 200


def test_items_still_key_gated():
    assert client.get("/items").status_code == 401
    assert client.get("/items", headers={"X-API-Key": "pw_test_key"}).status_code == 200
    assert client.get("/items", headers={"X-API-Key": "bad"}).status_code == 403


def test_latest_still_key_gated():
    assert client.get("/latest").status_code == 401
    assert client.get("/latest", headers={"X-API-Key": "pw_test_key"}).status_code == 200


def test_prices_still_key_gated():
    assert client.get("/prices/usd_ngn_official").status_code == 401
    r = client.get("/prices/usd_ngn_official", headers={"X-API-Key": "pw_test_key"})
    assert r.status_code == 200
    assert [x["collected_date"] for x in r.json()] == ["2026-10-03", "2026-10-02", "2026-10-01"]
    assert client.get("/prices/nope", headers={"X-API-Key": "pw_test_key"}).status_code == 404


def test_dashboard_mount_serves_real_page():
    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "PriceWatch" in r.text
    assert "app.js" in r.text  # the page loads its JS bundle


def test_dashboard_assets_are_served():
    assert client.get("/dashboard/app.js").status_code == 200
    assert client.get("/dashboard/style.css").status_code == 200
