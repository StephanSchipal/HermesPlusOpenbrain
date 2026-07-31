# tests/test_hermes_usage.py
"""Tests build a fixture state.db with the real column names taken from the
live Hermes database. Two details are load-bearing and easy to get wrong:

  * `first_seen`/`last_seen` are EPOCH FLOATS, not ISO strings. A
    `datetime("now","-30 days")` comparison silently returns zero rows.
  * platform comes from `sessions.source`, joined on session_id -- it is not
    a column on session_model_usage.
"""
import sqlite3
import pytest
from app import hermes_usage

NOW = 1785500000.0  # fixed clock so windows are deterministic
DAY = 86400.0


@pytest.fixture
def hermes_dir(tmp_path):
    """A minimal state.db shaped like the real one."""
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir()
    conn = sqlite3.connect(str(data_dir / "state.db"))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT, model TEXT, system_prompt TEXT,
            message_count INTEGER, tool_call_count INTEGER, title TEXT,
            cwd TEXT, git_branch TEXT, profile_name TEXT,
            compression_fallback_streak INTEGER, compression_failure_error TEXT,
            compression_failure_cooldown_until REAL
        );
        CREATE TABLE session_model_usage (
            session_id TEXT, model TEXT, task TEXT, api_call_count INTEGER,
            input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER, estimated_cost_usd REAL,
            cost_status TEXT, first_seen REAL, last_seen REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            tool_name TEXT, timestamp REAL, token_count INTEGER
        );
    """)
    conn.executemany(
        "INSERT INTO sessions (id, source, model, system_prompt, message_count,"
        " tool_call_count, title, cwd, git_branch, profile_name,"
        " compression_fallback_streak, compression_failure_error,"
        " compression_failure_cooldown_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("s-wa", "whatsapp", "claude-sonnet-5", "x" * 10000, 347, 40,
             "Context Engineering", "/opt/data", None, "default", 0, None, None),
            ("s-cli", "cli", "claude-opus-4-8", "y" * 17000, 379, 120,
             "Twilio Voice", "/root/proj", "main", "default", 2, "boom", None),
            ("s-old", "cli", "claude-sonnet-4-6", "z" * 5000, 10, 1,
             "Ancient", "/root", None, "default", 0, None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO session_model_usage (session_id, model, task, api_call_count,"
        " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
        " reasoning_tokens, estimated_cost_usd, cost_status, first_seen, last_seen)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("s-wa", "claude-sonnet-5", "", 193, 100_000, 20_000,
             30_000_000, 4_000_000, 0, 18.39, "estimated", NOW - 6 * DAY, NOW - 1 * DAY),
            ("s-cli", "claude-opus-4-8", "", 184, 50_000, 10_000,
             12_000_000, 900_000, 0, 16.33, "estimated", NOW - 3 * DAY, NOW - 2 * DAY),
            ("s-old", "claude-sonnet-4-6", "", 5, 1_000, 500,
             100_000, 10_000, 0, 0.42, "estimated", NOW - 200 * DAY, NOW - 180 * DAY),
        ],
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, tool_name, timestamp, token_count)"
        " VALUES (?,?,?,?,?)",
        [
            ("s-wa", "tool", "terminal", NOW - 2 * DAY, None),
            ("s-wa", "tool", "terminal", NOW - 2 * DAY, None),
            ("s-cli", "tool", "read_file", NOW - 2 * DAY, None),
            ("s-old", "tool", "terminal", NOW - 190 * DAY, None),
        ],
    )
    conn.commit()
    conn.close()
    return str(data_dir)


def test_snapshot_opens_a_copy_not_the_original(hermes_dir):
    with hermes_usage.snapshot(hermes_dir) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        assert rows["n"] == 3
        db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert hermes_dir not in db_file


def test_snapshot_raises_when_data_dir_missing(tmp_path):
    with pytest.raises(hermes_usage.HermesDataUnavailable):
        with hermes_usage.snapshot(str(tmp_path / "nope")):
            pass


def test_snapshot_copies_wal_when_present(hermes_dir):
    open(f"{hermes_dir}/state.db-wal", "wb").close()
    with hermes_usage.snapshot(hermes_dir) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 3


def test_snapshot_leaves_the_original_untouched(hermes_dir):
    """The production mount is :ro -- prove we never write through to it."""
    import os
    src = f"{hermes_dir}/state.db"
    before = os.stat(src).st_mtime_ns
    with hermes_usage.snapshot(hermes_dir) as conn:
        conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert os.stat(src).st_mtime_ns == before


def test_read_usage_rows_returns_raw_counters_with_platform(hermes_dir):
    rows = hermes_usage.read_usage_rows(hermes_dir)
    assert len(rows) == 3
    by_session = {r["session_id"]: r for r in rows}
    assert by_session["s-wa"]["cache_read_tokens"] == 30_000_000
    assert by_session["s-wa"]["api_call_count"] == 193
    # Platform is denormalised into the ledger later, so it must come out here.
    assert by_session["s-wa"]["platform"] == "whatsapp"
    assert by_session["s-cli"]["platform"] == "cli"


def test_read_usage_rows_keeps_orphaned_usage(hermes_dir):
    """Hermes prunes old sessions. A usage row whose session is gone still
    carries real spend and must not vanish -- hence LEFT JOIN, not JOIN."""
    conn = sqlite3.connect(f"{hermes_dir}/state.db")
    conn.execute("DELETE FROM sessions WHERE id = 's-old'")
    conn.commit()
    conn.close()
    rows = hermes_usage.read_usage_rows(hermes_dir)
    assert len(rows) == 3
    orphan = next(r for r in rows if r["session_id"] == "s-old")
    assert orphan["platform"] == ""
    assert orphan["estimated_cost_usd"] == 0.42
