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
    profile       TEXT    NOT NULL DEFAULT 'default',
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
    profile            TEXT NOT NULL DEFAULT 'default',
    session_id         TEXT NOT NULL,
    model              TEXT NOT NULL,
    task               TEXT NOT NULL DEFAULT '',
    api_call_count     INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (profile, session_id, model, task)
);

-- A saved point-in-time copy of the Part 1 dashboard (summary tiles, by-model/
-- by-platform/top-spenders tables, efficiency, top tools, prompt budget) for a
-- given range, so two ranges/days can be compared by opening the GUI in two
-- windows -- one live, one loaded from here. `name` is the user-facing,
-- deterministically-generated identifier (e.g. "CostReport_07_...") and the
-- natural key: saving again under the same name overwrites it.
CREATE TABLE IF NOT EXISTS cost_reports (
    name        TEXT PRIMARY KEY,
    days        INTEGER NOT NULL,
    range_label TEXT    NOT NULL,
    saved_at    TEXT    NOT NULL,
    payload     TEXT    NOT NULL
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


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a pre-per-profile gui.db up to the current schema. Idempotent:
    each branch is guarded by a column-existence check, so it is a no-op on a
    db that `_SCHEMA` already built with the final shape."""
    if "profile" not in _table_columns(conn, "usage_ledger"):
        # ADD COLUMN appends `profile` as the last column on a migrated db,
        # whereas `_SCHEMA` lists it 5th on a fresh one. Deliberate, accepted
        # divergence: every read of usage_ledger is by column name (no
        # `SELECT *` / positional access), so physical order does not matter.
        conn.execute(
            "ALTER TABLE usage_ledger ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"
        )
    if "profile" not in _table_columns(conn, "usage_watermark"):
        # SQLite cannot alter a PRIMARY KEY in place, so the table is rebuilt.
        # `executescript` runs with no enclosing transaction, so the whole
        # rebuild is wrapped in one BEGIN/COMMIT: a crash mid-migration must not
        # leave a half-built `usage_watermark_new` (next boot crash-loops on
        # "table already exists") or an empty `usage_watermark` (silent
        # watermark loss). `DROP ... IF EXISTS` clears any orphan left by a
        # prior aborted run. `COALESCE` covers a gui.db written before these
        # counters were made NOT NULL, whose NULLs would fail the new table.
        conn.executescript("""
            BEGIN;
            DROP TABLE IF EXISTS usage_watermark_new;
            CREATE TABLE usage_watermark_new (
                profile TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL, model TEXT NOT NULL,
                task TEXT NOT NULL DEFAULT '',
                api_call_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (profile, session_id, model, task)
            );
            INSERT INTO usage_watermark_new
                (profile, session_id, model, task, api_call_count, input_tokens,
                 output_tokens, cache_read_tokens, cache_write_tokens,
                 reasoning_tokens, estimated_cost_usd)
            SELECT 'default', session_id, model, task,
                   COALESCE(api_call_count, 0), COALESCE(input_tokens, 0),
                   COALESCE(output_tokens, 0), COALESCE(cache_read_tokens, 0),
                   COALESCE(cache_write_tokens, 0), COALESCE(reasoning_tokens, 0),
                   COALESCE(estimated_cost_usd, 0)
            FROM usage_watermark;
            DROP TABLE usage_watermark;
            ALTER TABLE usage_watermark_new RENAME TO usage_watermark;
            COMMIT;
        """)


def init_db(path: str | None = None) -> None:
    with get_conn(path) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
