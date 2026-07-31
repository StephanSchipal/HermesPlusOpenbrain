# app/db.py
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from app.config import GUI_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delete_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL,
    subject_line TEXT,
    keywords TEXT,
    source_url TEXT,
    captured_at TEXT,
    deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_costs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL DEFAULT '',
    period              TEXT    NOT NULL DEFAULT 'monthly',
    amount              REAL,
    entered_currency    TEXT    NOT NULL DEFAULT 'USD',
    url                 TEXT,
    comments            TEXT,
    compare_to_estimate INTEGER NOT NULL DEFAULT 0,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fx_rate (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    usd_to_eur  REAL NOT NULL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT    NOT NULL,
    session_id    TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    -- Denormalised from sessions.source at write time. The chart must be able
    -- to group by platform without /hermes-data being mounted at read time.
    platform      TEXT    NOT NULL DEFAULT '',
    task          TEXT    NOT NULL DEFAULT '',
    d_api_calls   INTEGER NOT NULL DEFAULT 0,
    d_input       INTEGER NOT NULL DEFAULT 0,
    d_output      INTEGER NOT NULL DEFAULT 0,
    d_cache_read  INTEGER NOT NULL DEFAULT 0,
    d_cache_write INTEGER NOT NULL DEFAULT 0,
    d_reasoning   INTEGER NOT NULL DEFAULT 0,
    d_cost_usd    REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_usage_ledger_observed ON usage_ledger(observed_at);

CREATE TABLE IF NOT EXISTS usage_watermark (
    session_id         TEXT NOT NULL,
    model              TEXT NOT NULL,
    task               TEXT NOT NULL DEFAULT '',
    api_call_count     INTEGER,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens   INTEGER,
    estimated_cost_usd REAL,
    PRIMARY KEY (session_id, model, task)
);
"""

@contextmanager
def get_conn(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or GUI_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(path: str | None = None) -> None:
    with get_conn(path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
