# tests/test_cost_merge.py
import sqlite3
import pytest
from app import cost_merge, profiles

NOW = 1785500000.0
DAY = 86400.0

_SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, system_prompt TEXT,
    message_count INTEGER, tool_call_count INTEGER, title TEXT, cwd TEXT, git_branch TEXT,
    profile_name TEXT, compression_fallback_streak INTEGER, compression_failure_error TEXT,
    compression_failure_cooldown_until REAL);
CREATE TABLE session_model_usage (session_id TEXT, model TEXT, task TEXT, api_call_count INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
    cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL,
    cost_status TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, tool_name TEXT,
    timestamp REAL, token_count INTEGER);
"""


def _statedb(data_dir, *, source, model, cost, api_calls, cache_read, cache_write,
             inp, msgs, prompt_chars, tool):
    """Write a fixture state.db into data_dir/state.db."""
    data_dir.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(data_dir / "state.db"))
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("s", source, model, "x" * prompt_chars, msgs, 1, "t", "/x", None,
               None, 0, None, None))
    c.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("s", model, "", api_calls, inp, 0, cache_read, cache_write, 0, cost,
               "estimated", NOW - 2 * DAY, NOW - DAY))
    for i in range(3):
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                  (i, "s", "assistant", tool, NOW - DAY, None))
    c.commit(); c.close()


def _statedb_sessions(data_dir, *, source, n_sessions, message_count, prompt_chars,
                      model="claude-x", cost_each=1.0):
    """state.db with N sessions on ONE platform -- for exercising the
    session-weighted pooled means when two bots share a platform."""
    data_dir.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(data_dir / "state.db"))
    c.executescript(_SCHEMA)
    for i in range(n_sessions):
        sid = f"{source}-{i}"
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, source, model, "x" * prompt_chars, message_count, 1, "t", "/x",
                   None, None, 0, None, None))
        c.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, model, "", 10, 1000, 0, 0, 0, 0, cost_each,
                   "estimated", NOW - 2 * DAY, NOW - DAY))
    c.commit(); c.close()


@pytest.fixture
def two_bots(tmp_path, monkeypatch):
    root = tmp_path / "hermes-data"
    _statedb(root, source="cli", model="claude-opus-4-8", cost=10.0, api_calls=100,
             cache_read=1_000_000, cache_write=100_000, inp=50_000, msgs=200,
             prompt_chars=17000, tool="terminal")
    _statedb(root / "profiles" / "openbrain", source="whatsapp", model="claude-sonnet-5",
             cost=4.0, api_calls=40, cache_read=2_000_000, cache_write=500_000,
             inp=10_000, msgs=50, prompt_chars=8000, tool="terminal")
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(root))
    return root


@pytest.fixture
def two_bots_same_platform(tmp_path, monkeypatch):
    """Both bots run on 'whatsapp' with different session counts, message
    counts and prompt sizes -- so a naive mean of the per-bot averages and the
    true session-weighted pooled mean disagree."""
    root = tmp_path / "hermes-data"
    _statedb_sessions(root, source="whatsapp", n_sessions=1,
                      message_count=10, prompt_chars=1000)
    _statedb_sessions(root / "profiles" / "openbrain", source="whatsapp", n_sessions=3,
                      message_count=100, prompt_chars=9000)
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(root))
    return root


def test_dashboard_all_sums_and_recomputes_hit_rate(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50, now=NOW)
    s = d["summary"]
    assert s["cost_usd"] == pytest.approx(14.0)
    assert s["api_calls"] == 140
    denom = s["cache_read_tokens"] + s["cache_write_tokens"] + s["input_tokens"]
    assert s["cache_hit_rate"] == pytest.approx(s["cache_read_tokens"] / denom)
    assert d["skipped_profiles"] == []


def test_summary_all_is_the_merged_summary(two_bots):
    s = cost_merge.summary_all(days=30, now=NOW)
    assert s["cost_usd"] == pytest.approx(14.0)
    assert s["api_calls"] == 140
    assert "cache_hit_rate" in s and "unpriced" in s
    assert s["skipped_profiles"] == []


def test_by_session_tags_profile_and_respects_limit(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=1, now=NOW)
    assert len(d["by_session"]) == 1
    assert d["by_session"][0]["profile"] == "default"


def test_by_model_and_platform_merge(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50, now=NOW)
    assert {r["model"] for r in d["by_model"]} == {"claude-opus-4-8", "claude-sonnet-5"}
    assert {r["platform"] for r in d["by_platform"]} == {"cli", "whatsapp"}


def test_top_tools_sums_calls(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50, now=NOW)
    terminal = next(t for t in d["top_tools"]["tools"] if t["tool_name"] == "terminal")
    assert terminal["calls"] == 6
    assert d["top_tools"]["token_attribution_available"] is False


def test_per_bot_breakdown_percentages(two_bots):
    rows = cost_merge.per_bot_breakdown(days=30, now=NOW)
    assert [r["key"] for r in rows] == ["default", "openbrain"]
    assert sum(r["pct"] for r in rows) == pytest.approx(1.0)
    assert rows[0]["label"] == "Hermes-Agent"


def test_config_all_and_missing_config(two_bots):
    rows = cost_merge.config_all()
    assert {r["key"] for r in rows} == {"default", "openbrain"}
    assert all(r.get("unavailable") for r in rows)


def test_one_unreadable_statedb_degrades_to_a_row(two_bots):
    (two_bots / "profiles" / "openbrain" / "state.db").write_bytes(b"not a database")
    rows = cost_merge.per_bot_breakdown(days=30, now=NOW)
    bad = next(r for r in rows if r["key"] == "openbrain")
    assert bad["unavailable"] is True and bad["cost_usd"] == 0
    assert next(r for r in rows if r["key"] == "default")["cost_usd"] == pytest.approx(10.0)


def test_dashboard_all_reports_skipped_profile(two_bots):
    (two_bots / "profiles" / "openbrain" / "state.db").write_bytes(b"not a database")
    d = cost_merge.dashboard_all(days=30, now=NOW)
    assert d["skipped_profiles"] == ["openbrain"]
    # merged figures reflect only the surviving bot -- a lower bound
    assert d["summary"]["cost_usd"] == pytest.approx(10.0)
    assert d["summary"]["api_calls"] == 100
    assert {r["model"] for r in d["by_model"]} == {"claude-opus-4-8"}


def test_summary_all_reports_skipped_profile(two_bots):
    (two_bots / "profiles" / "openbrain" / "state.db").write_bytes(b"not a database")
    s = cost_merge.summary_all(days=30, now=NOW)
    assert s["skipped_profiles"] == ["openbrain"]
    assert s["cost_usd"] == pytest.approx(10.0)
    assert s["api_calls"] == 100


def test_weighted_means_are_session_pooled_not_naive(two_bots_same_platform):
    d = cost_merge.dashboard_all(days=30, now=NOW)

    eff = next(r for r in d["efficiency"] if r["platform"] == "whatsapp")
    # pooled: (10*1 + 100*3) / (1 + 3) = 77.5 ; naive mean of the avgs = 55
    assert eff["avg_messages_per_session"] == pytest.approx(77.5)

    pb = next(r for r in d["prompt_budget"] if r["platform"] == "whatsapp")
    # pooled: (1000*1 + 9000*3) / 4 = 7000 ; naive mean = 5000
    assert pb["avg_system_prompt_chars"] == pytest.approx(7000.0)
    assert pb["sessions"] == 4


def test_pruned_session_bucket_stays_none_not_zero(two_bots):
    # an orphan usage row (its session was pruned) groups under platform ''
    # with avg_messages_per_session = None -- the merge must keep it None, not
    # coerce it to 0.
    c = sqlite3.connect(str(two_bots / "state.db"))
    c.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("orphan", "claude-opus-4-8", "", 5, 100, 0, 0, 0, 0, 1.0,
               "estimated", NOW - 2 * DAY, NOW - DAY))
    c.commit(); c.close()
    d = cost_merge.dashboard_all(days=30, now=NOW)
    pruned = next(r for r in d["efficiency"] if r["platform"] == "")
    assert pruned["avg_messages_per_session"] is None
