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


def test_snapshot_leaves_the_source_directory_untouched(hermes_dir):
    """The production mount is :ro. Reading a WAL database normally makes
    SQLite create a `-shm` file beside it, which would fail on that mount --
    working from a copy is what avoids it. Assert on the whole directory, not
    just state.db's mtime: a stray `-shm` is precisely the failure mode."""
    import os
    src = f"{hermes_dir}/state.db"
    before_files = set(os.listdir(hermes_dir))
    before_mtime = os.stat(src).st_mtime_ns
    with hermes_usage.snapshot(hermes_dir) as conn:
        conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    assert set(os.listdir(hermes_dir)) == before_files
    assert os.stat(src).st_mtime_ns == before_mtime


def test_snapshot_reads_a_real_wal_database(tmp_path):
    """The other fixtures use SQLite's default journal mode, so they never
    exercise the constraint this module exists for. This one is genuinely
    WAL, with committed rows still sitting in the -wal file."""
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir()
    db = data_dir / "state.db"
    writer = sqlite3.connect(str(db))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    writer.execute("INSERT INTO sessions VALUES ('s1', 'whatsapp')")
    writer.commit()
    assert (data_dir / "state.db-wal").exists()
    try:
        # Writer still open, exactly as Hermes would be.
        with hermes_usage.snapshot(str(data_dir)) as conn:
            assert conn.execute("SELECT source FROM sessions").fetchone()["source"] == "whatsapp"
    finally:
        writer.close()


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


def test_window_uses_epoch_floats_not_iso_strings(hermes_dir):
    """The regression this guards: comparing last_seen against
    datetime('now','-30 days') matches nothing and returns an empty report
    that looks like 'no activity' rather than an error."""
    recent = hermes_usage.by_model(hermes_dir, days=30, now=NOW)
    assert {r["model"] for r in recent} == {"claude-sonnet-5", "claude-opus-4-8"}
    everything = hermes_usage.by_model(hermes_dir, days=365, now=NOW)
    assert "claude-sonnet-4-6" in {r["model"] for r in everything}


def test_by_model_aggregates_counters_and_cost(hermes_dir):
    rows = {r["model"]: r for r in hermes_usage.by_model(hermes_dir, days=30, now=NOW)}
    wa = rows["claude-sonnet-5"]
    assert wa["sessions"] == 1
    assert wa["api_calls"] == 193
    assert wa["cache_read_tokens"] == 30_000_000
    assert wa["cache_write_tokens"] == 4_000_000
    assert wa["cost_usd"] == pytest.approx(18.39)


def test_by_model_sorted_by_cost_descending(hermes_dir):
    rows = hermes_usage.by_model(hermes_dir, days=30, now=NOW)
    assert [r["cost_usd"] for r in rows] == sorted(
        [r["cost_usd"] for r in rows], reverse=True
    )


def test_by_platform_joins_sessions_source(hermes_dir):
    rows = {r["platform"]: r for r in hermes_usage.by_platform(hermes_dir, days=30, now=NOW)}
    assert set(rows) == {"whatsapp", "cli"}
    assert rows["whatsapp"]["cost_usd"] == pytest.approx(18.39)
    assert rows["cli"]["api_calls"] == 184


def test_summary_computes_cache_hit_rate(hermes_dir):
    summary = hermes_usage.summary(hermes_dir, days=30, now=NOW)
    reads = 42_000_000
    writes = 4_900_000
    inputs = 150_000
    assert summary["cache_read_tokens"] == reads
    assert summary["cache_hit_rate"] == pytest.approx(reads / (reads + writes + inputs))
    assert summary["cost_usd"] == pytest.approx(34.72)
    assert summary["api_calls"] == 377


def test_summary_on_empty_window_does_not_divide_by_zero(hermes_dir):
    summary = hermes_usage.summary(hermes_dir, days=1, now=NOW - 5000 * DAY)
    assert summary["api_calls"] == 0
    assert summary["cache_hit_rate"] is None


def test_efficiency_per_platform(hermes_dir):
    """The numbers that actually move the bill. WhatsApp writes far more cache
    per call than CLI, which is the whole reason this panel exists."""
    rows = {r["platform"]: r for r in hermes_usage.efficiency(hermes_dir, days=30, now=NOW)}
    wa = rows["whatsapp"]
    assert wa["api_calls"] == 193
    # (100_000 + 20_000 + 30_000_000 + 4_000_000) / 193
    assert wa["tokens_per_call"] == pytest.approx(34_120_000 / 193)
    assert wa["cache_write_per_call"] == pytest.approx(4_000_000 / 193)
    assert wa["cost_per_call"] == pytest.approx(18.39 / 193)
    assert wa["avg_messages_per_session"] == pytest.approx(347)

    cli = rows["cli"]
    assert cli["cache_write_per_call"] == pytest.approx(900_000 / 184)
    assert wa["cache_write_per_call"] > cli["cache_write_per_call"]


def test_efficiency_with_zero_calls_does_not_divide_by_zero(hermes_dir):
    rows = hermes_usage.efficiency(hermes_dir, days=1, now=NOW - 5000 * DAY)
    assert rows == []
