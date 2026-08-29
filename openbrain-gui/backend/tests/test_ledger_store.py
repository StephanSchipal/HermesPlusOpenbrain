# tests/test_ledger_store.py
"""Hermes stores only lifetime-per-session counters, so a live read cannot say
what was spent on a given day. These tests pin the sampling behaviour that
turns those counters into a real time series -- above all the first-tick rule,
without which day one records a fabricated spike representing all of Hermes'
prior history (in production, ~104M tokens and ~$94)."""
import sqlite3
import pytest
from app.db import init_db, get_conn
from app import ledger_store


def _db(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    return db_path


def _row(session_id="s1", model="claude-sonnet-5", platform="whatsapp", **over):
    row = {
        "session_id": session_id, "model": model, "task": "", "platform": platform,
        "api_call_count": 10, "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 1000, "cache_write_tokens": 200,
        "reasoning_tokens": 0, "estimated_cost_usd": 1.5,
    }
    row.update(over)
    return row


def test_first_tick_seeds_watermarks_and_emits_no_deltas(tmp_path):
    db_path = _db(tmp_path)
    result = ledger_store.apply_tick([_row()], profile="default", path=db_path,
                                     observed_at="2026-08-01T00:00:00+00:00")
    assert result == {"seeded": True, "rows_written": 0}
    with get_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM usage_watermark").fetchone()[0] == 1


def test_second_tick_writes_only_the_difference(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row(api_call_count=13, cache_read_tokens=1600, estimated_cost_usd=2.0)],
        profile="default", path=db_path, observed_at="2026-08-01T00:05:00+00:00",
    )
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM usage_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["d_api_calls"] == 3
    assert rows[0]["d_cache_read"] == 600
    assert rows[0]["d_cost_usd"] == pytest.approx(0.5)
    assert rows[0]["observed_at"] == "2026-08-01T00:05:00+00:00"
    assert rows[0]["platform"] == "whatsapp"


def test_unchanged_rows_write_nothing(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    result = ledger_store.apply_tick([_row()], profile="default", path=db_path,
                                     observed_at="2026-08-01T00:05:00+00:00")
    assert result["rows_written"] == 0
    with get_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 0


def test_new_session_after_seeding_counts_in_full(tmp_path):
    """Only the FIRST tick is suppressed. A session appearing later is
    genuinely new, so all of its tokens are new."""
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row("s1")], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row("s1"), _row("s2")], profile="default", path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT session_id, d_api_calls FROM usage_ledger").fetchall()
    assert [(r["session_id"], r["d_api_calls"]) for r in rows] == [("s2", 10)]


def test_decreasing_counter_clamps_to_zero(tmp_path):
    """A counter going backwards means the session was reset or replaced.
    Never record negative spend."""
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row(api_call_count=10)], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=4, cache_read_tokens=1500)],
                            profile="default", path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT d_api_calls, d_cache_read FROM usage_ledger").fetchone()
    assert row["d_api_calls"] == 0
    assert row["d_cache_read"] == 500


def test_same_session_different_models_tracked_separately(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick(
        [_row("s1", "claude-sonnet-5"), _row("s1", "claude-opus-4-8")],
        profile="default", path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row("s1", "claude-sonnet-5", api_call_count=12),
         _row("s1", "claude-opus-4-8")],
        profile="default", path=db_path, observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT model, d_api_calls FROM usage_ledger").fetchall()
    assert [(r["model"], r["d_api_calls"]) for r in rows] == [("claude-sonnet-5", 2)]


def test_timeseries_buckets_by_day_and_group(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=12, estimated_cost_usd=2.0)],
                            profile="default", path=db_path,
                            observed_at="2026-08-01T10:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=20, estimated_cost_usd=5.0)],
                            profile="default", path=db_path,
                            observed_at="2026-08-02T10:00:00+00:00")
    series = ledger_store.timeseries(path=db_path, days=30, group="model",
                                     now_iso="2026-08-03T00:00:00+00:00")
    assert series["collecting_since"] == "2026-08-01"
    points = {(p["day"], p["group"]): p["cost_usd"] for p in series["points"]}
    assert points[("2026-08-01", "claude-sonnet-5")] == pytest.approx(0.5)
    assert points[("2026-08-02", "claude-sonnet-5")] == pytest.approx(3.0)


def test_timeseries_can_group_by_platform(tmp_path):
    """Platform is denormalised onto the ledger row, so grouping by it works
    with no access to Hermes' state.db at all."""
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row("s1", platform="whatsapp"), _row("s2", platform="cli")],
                            profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row("s1", platform="whatsapp", estimated_cost_usd=4.5),
         _row("s2", platform="cli", estimated_cost_usd=2.5)],
        profile="default", path=db_path, observed_at="2026-08-01T06:00:00+00:00")
    series = ledger_store.timeseries(path=db_path, days=30, group="platform",
                                     now_iso="2026-08-03T00:00:00+00:00")
    points = {p["group"]: p["cost_usd"] for p in series["points"]}
    assert points == {"whatsapp": pytest.approx(3.0), "cli": pytest.approx(1.0)}


def test_timeseries_rejects_unknown_group(tmp_path):
    db_path = _db(tmp_path)
    with pytest.raises(ValueError):
        ledger_store.timeseries(path=db_path, days=30, group="banana",
                                now_iso="2026-08-03T00:00:00+00:00")


def test_timeseries_is_empty_before_any_tick(tmp_path):
    db_path = _db(tmp_path)
    series = ledger_store.timeseries(path=db_path, days=30, group="model",
                                     now_iso="2026-08-03T00:00:00+00:00")
    assert series["points"] == []
    assert series["collecting_since"] is None


def test_run_once_reads_hermes_and_applies_tick(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "read_usage_rows", lambda data_dir=None: [_row()])
    result = ledger_store.run_once(profile="default", data_dir="/whatever", path=db_path)
    assert result["seeded"] is True


def test_run_once_swallows_missing_hermes_data(tmp_path, monkeypatch):
    """The poller must never crash the app because the mount is absent."""
    db_path = _db(tmp_path)
    import app.hermes_usage as hu

    def boom(data_dir=None):
        raise hu.HermesDataUnavailable("not mounted")

    monkeypatch.setattr(hu, "read_usage_rows", boom)
    assert ledger_store.run_once(profile="default", data_dir="/whatever",
                                 path=db_path) == {"skipped": "not mounted"}


def test_run_once_swallows_a_torn_snapshot(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    import app.hermes_usage as hu

    def boom(data_dir=None):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(hu, "read_usage_rows", boom)
    assert "skipped" in ledger_store.run_once(profile="default", data_dir="/whatever",
                                              path=db_path)


def test_run_once_swallows_a_write_failure(tmp_path, monkeypatch):
    """A gui.db problem must not escape either."""
    db_path = _db(tmp_path)
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "read_usage_rows", lambda data_dir=None: [_row()])

    def boom(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger_store, "apply_tick", boom)
    assert "skipped" in ledger_store.run_once(profile="default", data_dir="/whatever",
                                              path=db_path)


def test_seeding_is_per_profile(tmp_path):
    db_path = _db(tmp_path)
    # profile A ticks twice -> seeded, then has watermarks
    ledger_store.apply_tick([_row()], profile="default", path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=20)], profile="default", path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    # profile B's FIRST tick must still seed (emit nothing), not diff against A
    res = ledger_store.apply_tick([_row()], profile="coder", path=db_path,
                                  observed_at="2026-08-01T00:06:00+00:00")
    assert res == {"seeded": True, "rows_written": 0}
    with get_conn(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM usage_ledger WHERE profile='coder'").fetchone()[0] == 0


def test_deltas_are_attributed_to_the_right_profile(tmp_path):
    db_path = _db(tmp_path)
    for p in ("default", "coder"):
        ledger_store.apply_tick([_row()], profile=p, path=db_path,
                                observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=15)], profile="coder", path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT profile, d_api_calls FROM usage_ledger").fetchall()
    assert [tuple(r) for r in rows] == [("coder", 5)]


def test_timeseries_filters_by_profile(tmp_path):
    db_path = _db(tmp_path)
    for p in ("default", "coder"):
        ledger_store.apply_tick([_row()], profile=p, path=db_path,
                                observed_at="2026-08-01T00:00:00+00:00")
        ledger_store.apply_tick([_row(api_call_count=13)], profile=p, path=db_path,
                                observed_at="2026-08-01T00:05:00+00:00")
    only_coder = ledger_store.timeseries(path=db_path, days=3650, group="model",
                                         profile="coder", now_iso="2026-08-02T00:00:00+00:00")
    all_p = ledger_store.timeseries(path=db_path, days=3650, group="model",
                                    profile="all", now_iso="2026-08-02T00:00:00+00:00")
    assert sum(p["api_calls"] for p in only_coder["points"]) == 3
    assert sum(p["api_calls"] for p in all_p["points"]) == 6


def test_run_all_swallows_profile_discovery_failure(tmp_path, monkeypatch):
    """`_poll_forever` has no try/except, so a raising `list_profiles`
    (EACCES on the mount, a TOCTOU during a dir scan) would kill the poller
    for the life of the process. run_all must absorb it."""
    db_path = _db(tmp_path)
    from app import profiles

    def boom():
        raise PermissionError("[Errno 13] Permission denied: '/hermes-data'")

    monkeypatch.setattr(profiles, "list_profiles", boom)
    assert ledger_store.run_all(path=db_path) == {}


def test_run_all_fans_out_once_per_profile(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    from app import profiles

    monkeypatch.setattr(profiles, "list_profiles", lambda: [
        {"key": "default", "label": "Hermes-Agent", "data_dir": "/data"},
        {"key": "coder", "label": "coder", "data_dir": "/data/profiles/coder"},
    ])
    calls = []

    def spy(*, profile, data_dir, path):
        calls.append((profile, data_dir, path))
        return {"seeded": True, "rows_written": 0}

    monkeypatch.setattr(ledger_store, "run_once", spy)
    result = ledger_store.run_all(path=db_path)
    assert calls == [
        ("default", "/data", db_path),
        ("coder", "/data/profiles/coder", db_path),
    ]
    assert result == {
        "default": {"seeded": True, "rows_written": 0},
        "coder": {"seeded": True, "rows_written": 0},
    }
