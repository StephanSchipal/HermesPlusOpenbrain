# tests/test_db_migration.py
import sqlite3
from app.db import init_db, get_conn

_OLD_SCHEMA = """
CREATE TABLE usage_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
    session_id TEXT NOT NULL, model TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '', d_api_calls INTEGER NOT NULL DEFAULT 0,
    d_input INTEGER NOT NULL DEFAULT 0, d_output INTEGER NOT NULL DEFAULT 0,
    d_cache_read INTEGER NOT NULL DEFAULT 0, d_cache_write INTEGER NOT NULL DEFAULT 0,
    d_reasoning INTEGER NOT NULL DEFAULT 0, d_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE usage_watermark (
    session_id TEXT NOT NULL, model TEXT NOT NULL, task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0, reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, model, task)
);
"""


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_migration_adds_profile_and_preserves_watermark_rows(tmp_path):
    db = str(tmp_path / "gui.db")
    con = sqlite3.connect(db)
    con.executescript(_OLD_SCHEMA)
    con.execute("INSERT INTO usage_ledger (observed_at, session_id, model) "
                "VALUES ('2026-08-01T00:00:00+00:00', 's1', 'm1')")
    con.execute("INSERT INTO usage_watermark (session_id, model, task, api_call_count) "
                "VALUES ('s1', 'm1', '', 42)")
    con.commit(); con.close()

    init_db(db)

    with get_conn(db) as conn:
        assert "profile" in _cols(conn, "usage_ledger")
        assert "profile" in _cols(conn, "usage_watermark")
        assert conn.execute("SELECT profile FROM usage_ledger").fetchone()[0] == "default"
        # get_conn sets row_factory = sqlite3.Row, which does not compare equal
        # to a plain tuple -- coerce before asserting the values.
        wm = tuple(conn.execute(
            "SELECT profile, api_call_count FROM usage_watermark").fetchone())
        assert wm == ("default", 42)
        # PRAGMA table_info: col 1 = name, col 5 = pk (1-based position in the
        # composite PK, 0 if not part of it). Assert the exact PK column set/order.
        pk_cols = sorted((r[5], r[1]) for r in conn.execute(
            "PRAGMA table_info(usage_watermark)") if r[5])
        assert [name for _, name in pk_cols] == ["profile", "session_id", "model", "task"]


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "gui.db")
    init_db(db)  # fresh, new schema
    init_db(db)  # second run must not raise or duplicate
    with get_conn(db) as conn:
        assert _cols(conn, "usage_ledger").count("profile") == 1


def test_migration_is_idempotent_on_an_already_migrated_old_schema_db(tmp_path):
    """The fresh-schema path is a pure no-op; this exercises the REAL migration
    branch, then a second init_db over the now-upgraded db."""
    db = str(tmp_path / "gui.db")
    con = sqlite3.connect(db)
    con.executescript(_OLD_SCHEMA)
    con.execute("INSERT INTO usage_watermark (session_id, model, task, api_call_count) "
                "VALUES ('s1', 'm1', '', 42)")
    con.commit(); con.close()

    init_db(db)   # performs the rebuild
    init_db(db)   # second run must be a clean no-op, not raise

    with get_conn(db) as conn:
        assert _cols(conn, "usage_ledger").count("profile") == 1
        wm = tuple(conn.execute(
            "SELECT profile, api_call_count FROM usage_watermark").fetchone())
        assert wm == ("default", 42)
        # the rebuild's scratch table must never survive
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='usage_watermark_new'"
        ).fetchall() == []


def test_migration_clears_an_orphan_watermark_new_table(tmp_path):
    """A migration that crashed mid-rebuild can leave usage_watermark_new behind.
    The next init_db must drop it and migrate cleanly, not crash-loop on
    'table usage_watermark_new already exists'."""
    db = str(tmp_path / "gui.db")
    con = sqlite3.connect(db)
    con.executescript(_OLD_SCHEMA)
    con.execute("INSERT INTO usage_watermark (session_id, model, task, api_call_count) "
                "VALUES ('s1', 'm1', '', 42)")
    con.execute("CREATE TABLE usage_watermark_new (bogus INTEGER)")
    con.commit(); con.close()

    init_db(db)  # must not raise

    with get_conn(db) as conn:
        assert "profile" in _cols(conn, "usage_watermark")
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='usage_watermark_new'"
        ).fetchall() == []
        wm = tuple(conn.execute(
            "SELECT profile, api_call_count FROM usage_watermark").fetchone())
        assert wm == ("default", 42)


def test_migration_coalesces_null_counters_from_a_pre_not_null_db(tmp_path):
    """A gui.db created before the watermark counters were made NOT NULL can hold
    NULLs; without COALESCE the rebuilt table's NOT NULL constraint rejects them."""
    db = str(tmp_path / "gui.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE usage_watermark (
            session_id TEXT NOT NULL, model TEXT NOT NULL, task TEXT NOT NULL DEFAULT '',
            api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER, estimated_cost_usd REAL,
            PRIMARY KEY (session_id, model, task)
        );
    """)
    con.execute("INSERT INTO usage_watermark (session_id, model, task) "
                "VALUES ('s1', 'm1', '')")  # every counter NULL
    con.commit(); con.close()

    init_db(db)  # must not raise on the NOT NULL rebuild

    with get_conn(db) as conn:
        row = tuple(conn.execute(
            "SELECT profile, api_call_count, estimated_cost_usd FROM usage_watermark"
        ).fetchone())
        assert row == ("default", 0, 0)


def test_watermark_new_pk_allows_same_session_across_profiles(tmp_path):
    db = str(tmp_path / "gui.db")
    init_db(db)
    with get_conn(db) as conn:
        conn.execute("INSERT INTO usage_watermark (profile, session_id, model, task) "
                     "VALUES ('default', 's1', 'm1', '')")
        conn.execute("INSERT INTO usage_watermark (profile, session_id, model, task) "
                     "VALUES ('coder', 's1', 'm1', '')")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM usage_watermark").fetchone()[0] == 2
