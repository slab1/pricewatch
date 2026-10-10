"""Tiny SQLite store. Zero setup, runs on a phone (Termux) or a $5 server.
Swap for Postgres/Supabase later by replacing this one file."""
import os
import sqlite3
import hashlib
from contextlib import contextmanager

DB_PATH = os.environ.get("PRICEWATCH_DB", "pricewatch.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item TEXT NOT NULL,
    source TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    collected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    collected_date TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (item, source, collected_date)
);
CREATE INDEX IF NOT EXISTS idx_prices_item_date ON prices (item, collected_date);

CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    happened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    active INTEGER NOT NULL DEFAULT 1
);
"""

_initialized_dbs = set()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Initialize schema once per database path
    if DB_PATH not in _initialized_dbs:
        conn.executescript(SCHEMA)
        _initialized_dbs.add(DB_PATH)
    # Set WAL journal mode and busy timeout
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # in milliseconds
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_price(item, source, value, unit, when=None):
    """One reading per item+source per day; re-running the same day updates it.
    `when` (YYYY-MM-DD) backfills a historical date — used by import_nbs.py."""
    with connect() as c:
        c.execute(
            """INSERT INTO prices (item, source, value, unit, collected_date)
               VALUES (?,?,?,?,COALESCE(?, date('now')))
               ON CONFLICT(item, source, collected_date)
               DO UPDATE SET value=excluded.value, collected_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (item, source, value, unit, when),
        )


def log_failure(item, source, reason):
    with connect() as c:
        c.execute("INSERT INTO failures (item, source, reason) VALUES (?,?,?)", (item, source, reason))
