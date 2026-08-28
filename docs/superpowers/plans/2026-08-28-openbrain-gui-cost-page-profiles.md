# Cost Page Per-Bot View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> Run in a dedicated git worktree (superpowers:using-git-worktrees), branched from `main`. Spec is already committed to `main` (`27d77df`).

**Goal:** Add a profile selector to the OpenBrain GUI Cost page — an "All" default that merges every Hermes bot's spend and adds a per-bot breakdown table, plus one entry per profile (`default` shown as "Hermes-Agent") that scopes every panel to that bot's `state.db`.

**Architecture:** `hermes_usage.py` is already parametrized by `data_dir`. A new `profiles.py` discovers the bots from the filesystem; a new `cost_merge.py` implements the "All" path by looping the existing functions per profile and summing raw counters (recomputing derived metrics). `usage_ledger`/`usage_watermark` gain a `profile` column via an idempotent `init_db` migration; the poller fans out over profiles. Routes gain `?profile=` (default `all`). Frontend adds a `<select>` and one new component.

**Tech stack:** FastAPI + SQLite (`openbrain-gui/backend`, pytest), React + Vite plain JSX (`openbrain-gui/frontend`, no test runner — verify in the browser).

**Spec:** `docs/superpowers/specs/2026-08-28-openbrain-gui-cost-page-profiles-design.md`

---

## File Structure

**Backend — new**
- `openbrain-gui/backend/app/profiles.py` — filesystem discovery of Hermes profiles; `list_profiles()`, `resolve(key)`.
- `openbrain-gui/backend/app/cost_merge.py` — the "All" path: `dashboard_all()`, `per_bot_breakdown()`, `config_all()`, plus private `_sum_counters` / `_weighted_mean`.

**Backend — modified**
- `openbrain-gui/backend/app/db.py` — `_SCHEMA` gains the `profile` column + new watermark PK; new `_migrate(conn)` called from `init_db()`.
- `openbrain-gui/backend/app/ledger_store.py` — `apply_tick(..., profile=)`, `run_once(..., profile=, data_dir=)`, new `run_all()`, `timeseries(..., profile=)`.
- `openbrain-gui/backend/app/main.py` — poller calls `run_all` not `run_once`.
- `openbrain-gui/backend/app/routes.py` — `?profile=` on 5 routes; new `/api/cost/profiles`, `/api/cost/by-bot`.

**Backend — tests**
- New: `test_profiles.py`, `test_cost_merge.py`, `test_db_migration.py`.
- Extended: `test_ledger_store.py`, `test_routes.py`.

**Frontend — new**
- `openbrain-gui/frontend/src/CostByBot.jsx`

**Frontend — modified**
- `api.js`, `CostView.jsx`, `CostConfig.jsx`, `CostTables.jsx`, `SessionDetail.jsx`, `format.js`

---

## Task 1: `profiles.py` — discovery

**Files:**
- Create: `openbrain-gui/backend/app/profiles.py`
- Test: `openbrain-gui/backend/tests/test_profiles.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py
import sqlite3
from app import profiles


def _mk_statedb(d):
    d.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(d / "state.db")).close()


def test_root_only(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    got = profiles.list_profiles()
    assert got == [{"key": "default", "label": "Hermes-Agent", "data_dir": str(tmp_path)}]


def test_root_plus_named_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    for name in ("writer", "coder", "openbrain"):
        _mk_statedb(tmp_path / "profiles" / name)
    keys = [p["key"] for p in profiles.list_profiles()]
    assert keys == ["default", "coder", "openbrain", "writer"]


def test_profile_dir_without_statedb_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    (tmp_path / "profiles" / "empty").mkdir(parents=True)
    assert [p["key"] for p in profiles.list_profiles()] == ["default"]


def test_absent_mount_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path / "nope"))
    assert profiles.list_profiles() == []


def test_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    _mk_statedb(tmp_path / "profiles" / "coder")
    assert profiles.resolve("default") == str(tmp_path)
    assert profiles.resolve("coder") == str(tmp_path / "profiles" / "coder")
    assert profiles.resolve("ghost") is None
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.profiles'`.

- [ ] **Step 3: Implement**

```python
# app/profiles.py
"""Filesystem discovery of Hermes profiles for the Cost page.

Each Hermes profile has its own state.db: the root profile at
HERMES_DATA_DIR/state.db, named profiles at HERMES_DATA_DIR/profiles/<name>/state.db.
The db file is the partition -- do NOT filter by sessions.profile_name (it is
NULL for almost every row in the root db).
"""
from pathlib import Path

from app.config import HERMES_DATA_DIR

_ROOT_KEY = "default"
_ROOT_LABEL = "Hermes-Agent"


def list_profiles() -> list[dict]:
    """[{key, label, data_dir}] -- root first, then named profiles alphabetically.
    Empty when HERMES_DATA_DIR has no state.db (local dev, no mount)."""
    root = Path(HERMES_DATA_DIR)
    out: list[dict] = []
    if (root / "state.db").is_file():
        out.append({"key": _ROOT_KEY, "label": _ROOT_LABEL, "data_dir": str(root)})
    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and (child / "state.db").is_file():
                out.append({"key": child.name, "label": child.name,
                            "data_dir": str(child)})
    return out


def resolve(key: str) -> str | None:
    """data_dir for a profile key, or None if unknown."""
    for p in list_profiles():
        if p["key"] == key:
            return p["data_dir"]
    return None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_profiles.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/profiles.py openbrain-gui/backend/tests/test_profiles.py
git commit -m "feat(gui): profiles.py — discover Hermes bots from the filesystem"
```

---

## Task 2: `db.py` — `profile` column + idempotent migration

**Files:**
- Modify: `openbrain-gui/backend/app/db.py`
- Test: `openbrain-gui/backend/tests/test_db_migration.py`

- [ ] **Step 1: Write the failing test**

```python
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
        wm = conn.execute("SELECT profile, api_call_count FROM usage_watermark").fetchone()
        assert wm == ("default", 42)
        pk = [r[1] for r in conn.execute("PRAGMA table_info(usage_watermark)") if r[5]]
        assert pk == []  # composite PK is not reported per-column here; see index check
        idx = conn.execute("PRAGMA index_list(usage_watermark)").fetchall()
        assert any("profile" in str(conn.execute(f"PRAGMA index_info({i[1]})").fetchall())
                   or i[3] == "pk" for i in idx)


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "gui.db")
    init_db(db)  # fresh, new schema
    init_db(db)  # second run must not raise or duplicate
    with get_conn(db) as conn:
        assert _cols(conn, "usage_ledger").count("profile") == 1


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
```

- [ ] **Step 2: Run it, verify failure**

Run: `python -m pytest tests/test_db_migration.py -v`
Expected: FAIL — new `init_db` has no migration; `profile` column absent.

- [ ] **Step 3: Implement — edit `_SCHEMA` and add `_migrate`**

In `db.py`, change the `usage_ledger` block to add `profile`:

```sql
CREATE TABLE IF NOT EXISTS usage_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT    NOT NULL,
    session_id    TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    profile       TEXT    NOT NULL DEFAULT 'default',
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
```

and the `usage_watermark` block to the new PK:

```sql
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
```

Add the migration and call it from `init_db`:

```python
def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a pre-per-profile gui.db up to the current schema. Idempotent:
    each branch is guarded by a column-existence check, so it is a no-op on a
    db that `_SCHEMA` already built with the final shape."""
    if "profile" not in _table_columns(conn, "usage_ledger"):
        conn.execute(
            "ALTER TABLE usage_ledger ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"
        )
    if "profile" not in _table_columns(conn, "usage_watermark"):
        conn.executescript("""
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
            SELECT 'default', session_id, model, task, api_call_count, input_tokens,
                   output_tokens, cache_read_tokens, cache_write_tokens,
                   reasoning_tokens, estimated_cost_usd
            FROM usage_watermark;
            DROP TABLE usage_watermark;
            ALTER TABLE usage_watermark_new RENAME TO usage_watermark;
        """)


def init_db(path: str | None = None) -> None:
    with get_conn(path) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_db_migration.py tests/test_db.py -v`
Expected: all pass (existing `test_db.py` unaffected).

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/db.py openbrain-gui/backend/tests/test_db_migration.py
git commit -m "feat(gui): usage_ledger/usage_watermark gain a profile column + init_db migration"
```

---

## Task 3: `ledger_store.py` — per-profile ticks

**Files:**
- Modify: `openbrain-gui/backend/app/ledger_store.py`
- Test: `openbrain-gui/backend/tests/test_ledger_store.py` (extend)

- [ ] **Step 1: Write the failing tests (append to `test_ledger_store.py`)**

```python
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
```

Also update the two existing calls in the file (`test_first_tick_...`, `test_second_tick_...`, and any others) to pass `profile="default"` — `apply_tick` now requires it.

- [ ] **Step 2: Run, verify failure**

Run: `python -m pytest tests/test_ledger_store.py -v`
Expected: FAIL — `apply_tick() got an unexpected keyword argument 'profile'` and the new tests error.

- [ ] **Step 3: Implement**

In `ledger_store.py`:

- `apply_tick(rows, *, profile, path=None, observed_at=None)` — add `profile` as a required keyword. Change the seeding check from `seeding = not marks` to:

```python
        seeding = conn.execute(
            "SELECT 1 FROM usage_watermark WHERE profile = ? LIMIT 1", (profile,)
        ).fetchone() is None
        marks = {
            (r["session_id"], r["model"], r["task"]): dict(r)
            for r in conn.execute(
                "SELECT * FROM usage_watermark WHERE profile = ?", (profile,))
        }
```

  Add `profile` to the `usage_ledger` INSERT column list and values (`profile` before `session_id`), and to the `usage_watermark` UPSERT — its conflict target becomes `(profile, session_id, model, task)` and the inserted tuple starts with `profile`:

```python
                conn.execute(
                    f"""
                    INSERT INTO usage_ledger
                        (observed_at, profile, session_id, model, task, platform,
                         {", ".join(t for _, t in _COUNTERS)})
                    VALUES (?, ?, ?, ?, ?, ?, {", ".join("?" * len(_COUNTERS))})
                    """,
                    (observed_at, profile, *key, row.get("platform") or "",
                     *(deltas[t] for _, t in _COUNTERS)),
                )
            ...
            conn.execute(
                f"""
                INSERT INTO usage_watermark
                    (profile, {", ".join(_KEY)}, {", ".join(s for s, _ in _COUNTERS)})
                VALUES ({", ".join("?" * (1 + len(_KEY) + len(_COUNTERS)))})
                ON CONFLICT(profile, session_id, model, task) DO UPDATE SET
                    {", ".join(f"{s} = excluded.{s}" for s, _ in _COUNTERS)}
                """,
                (profile, *key, *(row.get(s) or 0 for s, _ in _COUNTERS)),
            )
```

- `timeseries(*, path=None, days=30, group="model", profile="all", now_iso=None)` — after the existing `cutoff_iso`, build the WHERE:

```python
        where = "observed_at >= ?"
        params: list = [cutoff_iso]
        if profile != "all":
            where += " AND profile = ?"
            params.append(profile)
        rows = conn.execute(f"""... FROM usage_ledger WHERE {where} GROUP BY day, grp ...""", params).fetchall()
        first_q = "SELECT MIN(observed_at) AS first FROM usage_ledger"
        first_params: list = []
        if profile != "all":
            first_q += " WHERE profile = ?"; first_params.append(profile)
        first = conn.execute(first_q, first_params).fetchone()
```

- `run_once(*, profile, data_dir, path=None)` — make `profile` and `data_dir` required keywords; pass `profile` into `apply_tick`.

- New `run_all(*, path=None)`:

```python
def run_all(*, path: str | None = None) -> dict:
    """One poll cycle across every discovered profile. Never raises."""
    from app import profiles
    results = {}
    for p in profiles.list_profiles():
        results[p["key"]] = run_once(profile=p["key"], data_dir=p["data_dir"], path=path)
    return results
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_ledger_store.py -v`
Expected: all pass (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/ledger_store.py openbrain-gui/backend/tests/test_ledger_store.py
git commit -m "feat(gui): ledger ticks and timeseries are per-profile; add run_all()"
```

---

## Task 4: `main.py` — poller fans out

**Files:**
- Modify: `openbrain-gui/backend/app/main.py`

- [ ] **Step 1: Change the poll call**

In `main.py`'s `_poll_forever`, replace `await asyncio.to_thread(ledger_store.run_once)` with `await asyncio.to_thread(ledger_store.run_all)`.

- [ ] **Step 2: Verify nothing else references `run_once` with no args**

Run: `cd openbrain-gui/backend && grep -rn "run_once" app/ tests/`
Expected: only `ledger_store.py` (definition + `run_all` caller) and `test_ledger_store.py` (which passes `profile=`/`data_dir=`). If `test_routes.py` calls `run_once`, update it in Task 6.

- [ ] **Step 3: Run the full backend suite**

Run: `python -m pytest -q`
Expected: green except the route tests touched in Task 6 (not yet done) — note any failures for Task 6.

- [ ] **Step 4: Commit**

```bash
git add openbrain-gui/backend/app/main.py
git commit -m "feat(gui): usage-ledger poller fans out over all profiles"
```

---

## Task 5: `cost_merge.py` — the "All" path

**Files:**
- Create: `openbrain-gui/backend/app/cost_merge.py`
- Test: `openbrain-gui/backend/tests/test_cost_merge.py`

- [ ] **Step 1: Write the failing test**

```python
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


def test_dashboard_all_sums_and_recomputes_hit_rate(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50)
    s = d["summary"]
    assert s["cost_usd"] == pytest.approx(14.0)
    assert s["api_calls"] == 140
    # recomputed from the SUM, not the mean of the two per-bot rates
    denom = s["cache_read_tokens"] + s["cache_write_tokens"] + s["input_tokens"]
    assert s["cache_hit_rate"] == pytest.approx(s["cache_read_tokens"] / denom)


def test_summary_all_is_the_merged_summary(two_bots):
    s = cost_merge.summary_all(days=30)
    assert s["cost_usd"] == pytest.approx(14.0)
    assert s["api_calls"] == 140
    assert "cache_hit_rate" in s and "unpriced" in s


def test_by_session_tags_profile_and_respects_limit(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=1)
    assert len(d["by_session"]) == 1
    assert d["by_session"][0]["profile"] == "default"  # the $10 bot outranks the $4 bot


def test_by_model_and_platform_merge(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50)
    assert {r["model"] for r in d["by_model"]} == {"claude-opus-4-8", "claude-sonnet-5"}
    assert {r["platform"] for r in d["by_platform"]} == {"cli", "whatsapp"}


def test_top_tools_sums_calls(two_bots):
    d = cost_merge.dashboard_all(days=30, limit=50)
    terminal = next(t for t in d["top_tools"]["tools"] if t["tool_name"] == "terminal")
    assert terminal["calls"] == 6
    assert d["top_tools"]["token_attribution_available"] is False


def test_per_bot_breakdown_percentages(two_bots):
    rows = cost_merge.per_bot_breakdown(days=30)
    assert [r["key"] for r in rows] == ["default", "openbrain"]
    assert sum(r["pct"] for r in rows) == pytest.approx(1.0)
    assert rows[0]["label"] == "Hermes-Agent"


def test_config_all_and_missing_config(two_bots):
    rows = cost_merge.config_all()
    assert {r["key"] for r in rows} == {"default", "openbrain"}
    # no config.yaml written in the fixture -> unavailable rows, not an exception
    assert all(r.get("unavailable") for r in rows)


def test_one_unreadable_statedb_degrades_to_a_row(two_bots):
    (two_bots / "profiles" / "openbrain" / "state.db").write_bytes(b"not a database")
    rows = cost_merge.per_bot_breakdown(days=30)
    bad = next(r for r in rows if r["key"] == "openbrain")
    assert bad["unavailable"] is True and bad["cost_usd"] == 0
    # the good bot is still counted
    assert next(r for r in rows if r["key"] == "default")["cost_usd"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run, verify failure**

Run: `python -m pytest tests/test_cost_merge.py -v`
Expected: FAIL — `No module named 'app.cost_merge'`.

- [ ] **Step 3: Implement**

```python
# app/cost_merge.py
"""The "All" path for the Cost page: loop every Hermes profile, call the
data_dir-parametrized hermes_usage functions per profile, and combine.

Rule: sum raw counters, then RECOMPUTE derived metrics (hit rate, per-call
averages, weighted-mean prompt sizes). Never average an average."""
from app import hermes_usage, profiles
from app.hermes_usage import HermesDataUnavailable

_COUNTER_KEYS = ("sessions", "api_calls", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
                 "cost_usd")


def _sum_counters(dicts, keys):
    out = {k: 0 for k in keys}
    for d in dicts:
        for k in keys:
            out[k] += d.get(k) or 0
    return out


def _weighted_mean(pairs):
    """pairs: (value, weight). Returns Σ(v*w)/Σw or None."""
    num = sum((v or 0) * (w or 0) for v, w in pairs)
    den = sum((w or 0) for _, w in pairs)
    return num / den if den else None


def _each_dashboard(days, limit):
    for p in profiles.list_profiles():
        try:
            yield p, hermes_usage.dashboard(data_dir=p["data_dir"], days=days, limit=limit)
        except HermesDataUnavailable:
            continue


def _merge_summaries(summaries: list[dict]) -> dict:
    summary = _sum_counters(summaries, [k for k in _COUNTER_KEYS if k != "sessions"])
    summary["sessions"] = sum(s.get("sessions") or 0 for s in summaries)
    denom = summary["cache_read_tokens"] + summary["cache_write_tokens"] + summary["input_tokens"]
    summary["cache_hit_rate"] = summary["cache_read_tokens"] / denom if denom else None
    summary["cost_status"] = "estimated"
    up = [s.get("unpriced") or {} for s in summaries]
    summary["unpriced"] = {
        "api_calls": sum(u.get("api_calls") or 0 for u in up),
        "tokens": sum(u.get("tokens") or 0 for u in up),
        "models": sorted({m for u in up for m in (u.get("models") or [])}),
    }
    return summary


def summary_all(*, days: int = 30) -> dict:
    """Just the header-tile figures, summed across bots -- much cheaper than
    dashboard_all (no by_session / by_model / tools work). Used by /cost/summary."""
    got = []
    for p in profiles.list_profiles():
        try:
            got.append(hermes_usage.summary(data_dir=p["data_dir"], days=days))
        except HermesDataUnavailable:
            continue
    if not got:
        raise HermesDataUnavailable("no readable Hermes profile")
    return _merge_summaries(got)


def dashboard_all(*, days: int = 30, limit: int = 50) -> dict:
    got = list(_each_dashboard(days, limit))
    if not got:
        raise HermesDataUnavailable("no readable Hermes profile")

    summary = _merge_summaries([d["summary"] for _, d in got])
    by_model = _merge_grouped((d["by_model"] for _, d in got), "model")
    by_platform = _merge_grouped((d["by_platform"] for _, d in got), "platform")

    sessions = []
    for p, d in got:
        for row in d["by_session"]:
            sessions.append({**row, "profile": p["key"]})
    sessions.sort(key=lambda r: r.get("cost_usd") or 0, reverse=True)
    by_session = sessions[:limit]

    efficiency = _merge_efficiency(d["efficiency"] for _, d in got)
    prompt_budget = _merge_prompt_budget(d["prompt_budget"] for _, d in got)
    top_tools = _merge_tools(d["top_tools"] for _, d in got)

    return {"summary": summary, "by_model": by_model, "by_platform": by_platform,
            "by_session": by_session, "efficiency": efficiency,
            "prompt_budget": prompt_budget, "top_tools": top_tools}


def _merge_grouped(groups_iter, key):
    acc: dict = {}
    for rows in groups_iter:
        for r in rows:
            k = r[key]
            tgt = acc.setdefault(k, {key: k, **{c: 0 for c in _COUNTER_KEYS}})
            for c in _COUNTER_KEYS:
                tgt[c] += r.get(c) or 0
    return sorted(acc.values(), key=lambda r: r["cost_usd"], reverse=True)


def _merge_efficiency(effs_iter):
    acc: dict = {}
    for rows in effs_iter:
        for r in rows:
            k = r["platform"]
            tgt = acc.setdefault(k, {"platform": k, **{c: 0 for c in _COUNTER_KEYS},
                                     "_msg_pairs": []})
            for c in _COUNTER_KEYS:
                tgt[c] += r.get(c) or 0
            tgt["_msg_pairs"].append((r.get("avg_messages_per_session"), r.get("sessions")))
    out = []
    for r in acc.values():
        calls = r["api_calls"]
        toks = r["input_tokens"] + r["output_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"]
        pairs = r.pop("_msg_pairs")
        out.append({**r,
                    "tokens_per_call": toks / calls if calls else None,
                    "cache_write_per_call": r["cache_write_tokens"] / calls if calls else None,
                    "cost_per_call": r["cost_usd"] / calls if calls else None,
                    "avg_messages_per_session": _weighted_mean(pairs)})
    out.sort(key=lambda r: r["cost_usd"], reverse=True)
    return out


def _merge_prompt_budget(pbs_iter):
    acc: dict = {}
    for rows in pbs_iter:
        for r in rows:
            k = r["platform"]
            tgt = acc.setdefault(k, {"platform": k, "sessions": 0,
                                     "_avg_pairs": [], "max_system_prompt_chars": 0})
            tgt["sessions"] += r.get("sessions") or 0
            tgt["_avg_pairs"].append((r.get("avg_system_prompt_chars"), r.get("sessions")))
            tgt["max_system_prompt_chars"] = max(tgt["max_system_prompt_chars"],
                                                 r.get("max_system_prompt_chars") or 0)
    out = []
    for r in acc.values():
        pairs = r.pop("_avg_pairs")
        out.append({**r, "avg_system_prompt_chars": _weighted_mean(pairs)})
    out.sort(key=lambda r: (r["avg_system_prompt_chars"] or 0), reverse=True)
    return out


def _merge_tools(tools_iter):
    acc: dict = {}
    for t in tools_iter:
        for row in t["tools"]:
            acc[row["tool_name"]] = acc.get(row["tool_name"], 0) + row["calls"]
    tools = [{"tool_name": n, "calls": c} for n, c in
             sorted(acc.items(), key=lambda kv: kv[1], reverse=True)][:15]
    return {"tools": tools, "token_attribution_available": False}


def per_bot_breakdown(*, days: int = 30) -> list[dict]:
    rows = []
    for p in profiles.list_profiles():
        try:
            s = hermes_usage.summary(data_dir=p["data_dir"], days=days)
            rows.append({
                "key": p["key"], "label": p["label"],
                "sessions": s.get("sessions") or 0,
                "api_calls": s.get("api_calls") or 0,
                "tokens": (s.get("input_tokens") or 0) + (s.get("output_tokens") or 0)
                          + (s.get("cache_read_tokens") or 0) + (s.get("cache_write_tokens") or 0),
                "cost_usd": s.get("cost_usd") or 0,
                "unavailable": False,
            })
        except HermesDataUnavailable:
            rows.append({"key": p["key"], "label": p["label"], "sessions": 0,
                         "api_calls": 0, "tokens": 0, "cost_usd": 0, "unavailable": True})
    total = sum(r["cost_usd"] for r in rows) or 1.0
    for r in rows:
        r["pct"] = r["cost_usd"] / total
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


def config_all() -> list[dict]:
    out = []
    for p in profiles.list_profiles():
        try:
            snap = hermes_usage.config_snapshot(data_dir=p["data_dir"])
            out.append({"key": p["key"], "label": p["label"], **snap})
        except HermesDataUnavailable:
            out.append({"key": p["key"], "label": p["label"], "unavailable": True})
    return out
```

Note on `summary()`: it does not currently return a `sessions` key — it returns the `_SUM_COLUMNS` set which includes `sessions`. Confirm by reading `hermes_usage._SUM_COLUMNS` (it has `COUNT(DISTINCT u.session_id) AS sessions`). If `summary()` omits it, add `sessions` to its returned dict in a one-line change and note it in the commit.

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_cost_merge.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/cost_merge.py openbrain-gui/backend/tests/test_cost_merge.py
git commit -m "feat(gui): cost_merge.py — merged All-bots dashboard, per-bot breakdown, per-bot config"
```

---

## Task 6: `routes.py` — `?profile=` params + new routes

**Files:**
- Modify: `openbrain-gui/backend/app/routes.py`
- Test: `openbrain-gui/backend/tests/test_routes.py` (extend)

- [ ] **Step 1: Write the failing tests (append to `test_routes.py`)**

```python
def _fake_dashboard(**kw):
    return {"summary": _fake_summary(), "by_model": [], "by_platform": [],
            "by_session": [], "efficiency": [],
            "top_tools": {"tools": [], "token_attribution_available": False},
            "prompt_budget": []}


def test_profiles_route_lists_bots(client, monkeypatch, tmp_path):
    import app.profiles as pr
    root = tmp_path / "hd"; (root / "profiles" / "coder").mkdir(parents=True)
    (root / "state.db").touch(); (root / "profiles" / "coder" / "state.db").touch()
    monkeypatch.setattr(pr, "HERMES_DATA_DIR", str(root))
    body = client.get("/api/cost/profiles").json()
    assert body == [{"key": "default", "label": "Hermes-Agent"}, {"key": "coder", "label": "coder"}]


def test_dashboard_all_vs_specific_profile(client, monkeypatch):
    import app.cost_merge as cm, app.hermes_usage as hu, app.profiles as pr
    monkeypatch.setattr(pr, "resolve", lambda k: "/hd/coder" if k == "coder" else None)
    monkeypatch.setattr(cm, "dashboard_all", lambda **kw: {"_via": "merge"})
    monkeypatch.setattr(hu, "dashboard", lambda **kw: {"_via": "single", "data_dir": kw.get("data_dir")})
    assert client.get("/api/cost/dashboard").json()["_via"] == "merge"          # default all
    assert client.get("/api/cost/dashboard?profile=all").json()["_via"] == "merge"
    got = client.get("/api/cost/dashboard?profile=coder").json()
    assert got == {"_via": "single", "data_dir": "/hd/coder"}


def test_unknown_profile_is_404(client, monkeypatch):
    import app.profiles as pr
    monkeypatch.setattr(pr, "resolve", lambda k: None)
    assert client.get("/api/cost/dashboard?profile=ghost").status_code == 404


def test_session_route_requires_a_specific_profile(client, monkeypatch):
    import app.profiles as pr, app.hermes_usage as hu
    monkeypatch.setattr(pr, "resolve", lambda k: "/hd" if k == "default" else None)
    monkeypatch.setattr(hu, "session_detail", lambda sid, *, data_dir=None: {"id": sid})
    assert client.get("/api/cost/session/s1?profile=all").status_code == 400
    assert client.get("/api/cost/session/s1?profile=default").json()["id"] == "s1"


def test_by_bot_route(client, monkeypatch):
    import app.cost_merge as cm
    monkeypatch.setattr(cm, "per_bot_breakdown", lambda **kw: [{"key": "default", "cost_usd": 9.0}])
    assert client.get("/api/cost/by-bot?days=30").json()[0]["key"] == "default"


def test_summary_tco_is_fleetwide_regardless_of_profile(client, monkeypatch):
    import app.cost_merge as cm, app.hermes_usage as hu
    monkeypatch.setattr(cm, "dashboard_all",
                        lambda **kw: {"summary": {**_fake_summary(), "cost_usd": 100.0}})
    monkeypatch.setattr(hu, "summary", lambda data_dir=None, **kw: {**_fake_summary(), "cost_usd": 4.0})
    import app.profiles as pr
    monkeypatch.setattr(pr, "resolve", lambda k: "/hd/openbrain" if k == "openbrain" else "/hd")
    all_body = client.get("/api/cost/summary?profile=all").json()
    one_body = client.get("/api/cost/summary?profile=openbrain").json()
    # TCO uses the fleetwide (100) figure in both; the per-bot tile differs
    assert all_body["total_cost_of_ownership_usd"] == one_body["total_cost_of_ownership_usd"]
    assert one_body["hermes_cost_usd_selected"] == pytest.approx(4.0)
```

Update `_fake_summary()` / existing monkeypatch lambdas to accept `data_dir=` and `**kw` (some already do). Fix any existing `hu.dashboard` / `hu.summary` monkeypatches that don't accept `**kw`.

- [ ] **Step 2: Run, verify failure**

Run: `python -m pytest tests/test_routes.py -v -k "profile or by_bot or tco or dashboard_all"`
Expected: FAIL — routes have no `profile` param / no `/profiles` / no `/by-bot`.

- [ ] **Step 3: Implement**

In `routes.py`:

```python
from app import cost_merge, profiles
from fastapi import Query


def _profile_dir(profile: str) -> str:
    """Resolve a non-'all' profile key to its data_dir, or 404."""
    data_dir = profiles.resolve(profile)
    if data_dir is None:
        raise HTTPException(status_code=404, detail=f"unknown profile: {profile}")
    return data_dir


@router.get("/cost/profiles")
def get_cost_profiles():
    return [{"key": p["key"], "label": p["label"]} for p in profiles.list_profiles()]


@router.get("/cost/by-bot")
def get_cost_by_bot(days: int = 30):
    return _hermes(cost_merge.per_bot_breakdown, days=days)
```

Then thread `profile` through the existing routes:

```python
@router.get("/cost/dashboard")
def get_cost_dashboard(days: int = 30, limit: int = 50, profile: str = "all"):
    if profile == "all":
        return _hermes(cost_merge.dashboard_all, days=days, limit=limit)
    return _hermes(hermes_usage.dashboard, data_dir=_profile_dir(profile), days=days, limit=limit)


@router.get("/cost/config")
def get_cost_config(profile: str = "all"):
    if profile == "all":
        return _hermes(cost_merge.config_all)
    return _hermes(hermes_usage.config_snapshot, data_dir=_profile_dir(profile))


@router.get("/cost/session/{session_id}")
def get_cost_session(session_id: str, profile: str = Query(...)):
    if profile == "all":
        raise HTTPException(status_code=400, detail="a specific profile is required for a session lookup")
    detail = _hermes(hermes_usage.session_detail, session_id, data_dir=_profile_dir(profile))
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return detail


@router.get("/cost/timeseries")
def get_cost_timeseries(days: int = 30, group: str = "model", profile: str = "all"):
    try:
        return ledger_store.timeseries(days=days, group=group, profile=profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

`/cost/summary` — full replacement (keeps every existing response key, adds `profile` + `hermes_cost_usd_selected`):

```python
@router.get("/cost/summary")
def get_cost_summary(days: int = 30, profile: str = "all"):
    # Selected-profile figures drive the header tiles. "all" == the fleet sum.
    if profile == "all":
        selected = _hermes(cost_merge.summary_all, days=days)
        fleet = selected
    else:
        selected = _hermes(hermes_usage.summary, data_dir=_profile_dir(profile), days=days)
        fleet = _hermes(cost_merge.summary_all, days=days)

    # TCO and the invoice comparison are FLEET-WIDE: the Anthropic invoice and
    # the Hostinger line cover every bot, not the one in the dropdown.
    rate_row = fx.get_rate()
    rate = rate_row["usd_to_eur"] if rate_row else None
    rows = external_costs_store.list_rows()
    external = external_costs_store.totals(rows, rate)

    total_usd = fleet["cost_usd"] + external["monthly_usd"]
    total_incomplete = external["incomplete"]

    comparison = None
    flagged = external_costs_store.flagged_row()
    if flagged:
        baseline = (fleet["cost_usd"] if days == 30
                    else _hermes(cost_merge.summary_all, days=30)["cost_usd"])
        actual = external_costs_store.amounts(flagged, rate)["usd"]
        if actual is not None and baseline:
            comparison = {
                "name": flagged["name"], "estimated_usd": baseline,
                "actual_usd": actual, "delta_pct": (actual - baseline) / baseline * 100,
            }

    return {
        "days": days,
        "profile": profile,
        "hermes": selected,
        "hermes_cost_usd_selected": selected["cost_usd"],
        "external": external,
        "rate": rate_row,
        "total_cost_of_ownership_usd": total_usd,
        "total_cost_of_ownership_eur": total_usd * rate if rate else None,
        "total_cost_of_ownership_incomplete": total_incomplete,
        "estimate_vs_actual": comparison,
    }
```

- [ ] **Step 4: Run the full backend suite**

Run: `cd openbrain-gui/backend && python -m pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui): cost routes take ?profile=; add /cost/profiles and /cost/by-bot"
```

---

## Task 7: `api.js` — frontend cost helpers

**Files:**
- Modify: `openbrain-gui/frontend/src/api.js`

- [ ] **Step 1: Edit the cost helpers**

```js
  getCostProfiles: () => request('/cost/profiles'),
  getCostByBot: (days) => request(`/cost/by-bot?days=${days}`),
  getCostDashboard: (days, limit = 50, profile = 'all') =>
    request(`/cost/dashboard?days=${days}&limit=${limit}&profile=${profile}`),
  getCostSummary: (days, profile = 'all') =>
    request(`/cost/summary?days=${days}&profile=${profile}`),
  getCostSession: (id, profile) =>
    request(`/cost/session/${encodeURIComponent(id)}?profile=${encodeURIComponent(profile)}`),
  getCostConfig: (profile = 'all') => request(`/cost/config?profile=${profile}`),
  getCostTimeseries: (days, group, profile = 'all') =>
    request(`/cost/timeseries?days=${days}&group=${group}&profile=${profile}`),
```

- [ ] **Step 2: Verify build**

Run: `cd openbrain-gui/frontend && npm run build`
Expected: builds clean (no test runner in this project).

- [ ] **Step 3: Commit**

```bash
git add openbrain-gui/frontend/src/api.js
git commit -m "feat(gui): api.js cost helpers take a profile arg; add getCostProfiles/getCostByBot"
```

---

## Task 8: `CostView.jsx` — profile state + selector

**Files:**
- Modify: `openbrain-gui/frontend/src/CostView.jsx`

- [ ] **Step 1: Add profile state and the selector**

- Add `const [profile, setProfile] = useState('all')` and `const [profileList, setProfileList] = useState([{ key: 'all', label: 'All' }])`.
- On mount: `useEffect(() => { api.getCostProfiles().then(ps => setProfileList([{ key: 'all', label: 'All' }, ...ps])).catch(() => {}) }, [])`.
- In `load`: pass `profile` — `api.getCostDashboard(days, 50, profile)`, `api.getCostSummary(days, profile)`, `api.getCostConfig(profile)`. Add `profile` to the `useCallback` deps.
- In the timeseries effect: `api.getCostTimeseries(days, chartGroup, profile)`; add `profile` to deps.
- Render a `<select>` in the `.cost-range` header row (next to the range buttons):

```jsx
  <select className="cost-profile" value={profile} onChange={(e) => setProfile(e.target.value)}>
    {profileList.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
  </select>
```

- Render `<CostByBot days={days} onPick={setProfile} />` immediately below `<CostSummary>` **only when `profile === 'all'`**.
- Pass `profile` to `<CostTables>` and `<SessionDetail>` (Tasks 10–11 consume it).
- `reportName`/`dateRangeLabel` calls: pass `profile` to `reportName` (Task 12).

- [ ] **Step 2: Verify build + manual smoke**

Run: `cd openbrain-gui/frontend && npm run build`
Then Task 13 covers the live check.

- [ ] **Step 3: Commit**

```bash
git add openbrain-gui/frontend/src/CostView.jsx
git commit -m "feat(gui): CostView — profile selector, threaded through every cost fetch"
```

---

## Task 9: `CostByBot.jsx` — per-bot breakdown table

**Files:**
- Create: `openbrain-gui/frontend/src/CostByBot.jsx`

- [ ] **Step 1: Implement**

```jsx
import { useEffect, useState } from 'react'
import { api } from './api'
import { usd, tokens, pct } from './format'

export default function CostByBot({ days, onPick }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    api.getCostByBot(days).then(setRows).catch(() => setRows(null))
  }, [days])
  if (!rows) return null
  return (
    <div className="cost-panel">
      <h3>Cost by bot</h3>
      <table className="cost-table">
        <thead>
          <tr><th>Bot</th><th>Sessions</th><th>API calls</th><th>Tokens</th><th>Cost</th><th>%</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="clickable" onClick={() => onPick(r.key)}>
              <td>{r.label}{r.unavailable ? ' (unavailable)' : ''}</td>
              <td>{r.sessions}</td>
              <td>{r.api_calls}</td>
              <td>{tokens(r.tokens)}</td>
              <td>{usd(r.cost_usd)}</td>
              <td>{pct(r.pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

Check `format.js` exports `usd`, `tokens`, `pct` with these signatures (grep confirmed they exist). Match the existing table class names used in `CostTables.jsx` — open it and reuse whatever it uses (`cost-table` above is a placeholder; use the real class).

- [ ] **Step 2: Build**

Run: `cd openbrain-gui/frontend && npm run build`

- [ ] **Step 3: Commit**

```bash
git add openbrain-gui/frontend/src/CostByBot.jsx
git commit -m "feat(gui): CostByBot — per-bot spend table on the All view"
```

---

## Task 10: `CostConfig.jsx` — dict or per-bot table

**Files:**
- Modify: `openbrain-gui/frontend/src/CostConfig.jsx`

- [ ] **Step 1: Read the current component**

It currently renders a single `config` dict (`{ "model.default": ..., ... }`).

- [ ] **Step 2: Branch on shape**

If `Array.isArray(config)` → render a table, one row per bot, columns: Bot, `model.default`, `compression.threshold`, `compression.threshold_tokens`, `prompt_caching.cache_ttl`, `agent.max_turns`, `agent.disabled_toolsets`, `sessions.retention_days`. `unavailable` rows show "—" across. Otherwise render exactly as today.

```jsx
if (Array.isArray(config)) {
  const KEYS = ['model.default', 'compression.threshold', 'compression.threshold_tokens',
                'prompt_caching.cache_ttl', 'agent.max_turns', 'agent.disabled_toolsets',
                'sessions.retention_days']
  return (
    <div className="cost-panel">
      <h3>Config by bot</h3>
      <table className="cost-table">
        <thead><tr><th>Bot</th>{KEYS.map((k) => <th key={k}>{k}</th>)}</tr></thead>
        <tbody>
          {config.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              {KEYS.map((k) => <td key={k}>{row.unavailable ? '—' : fmt(row[k])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

where `fmt` renders arrays as comma-joined and `undefined` as "—".

- [ ] **Step 3: Build + commit**

```bash
cd openbrain-gui/frontend && npm run build
git add openbrain-gui/frontend/src/CostConfig.jsx
git commit -m "feat(gui): CostConfig renders a per-bot table under the All view"
```

---

## Task 11: `CostTables.jsx` + `SessionDetail.jsx` — Bot column + profile prop

**Files:**
- Modify: `openbrain-gui/frontend/src/CostTables.jsx`, `openbrain-gui/frontend/src/SessionDetail.jsx`

- [ ] **Step 1: `CostTables.jsx`**

- Accept a `profile` prop.
- In the **by-session** table only: when `profile === 'all'`, add a leading `<th>Bot</th>` and render `row.profile` per row.
- The row click handler currently passes `session_id` up. Also pass the row's `profile` (fall back to the current `profile` prop when a single bot is selected — its rows have no `profile` field). e.g. `onSelectSession({ id: row.session_id, profile: row.profile || profile })`.
- Update `CostView.jsx`'s `selectedSession` state to hold `{ id, profile }` instead of a bare id, and pass both to `<SessionDetail>`.

- [ ] **Step 2: `SessionDetail.jsx`**

- Accept `profile` prop; change the fetch to `api.getCostSession(sessionId, profile)`.

- [ ] **Step 3: Build**

Run: `cd openbrain-gui/frontend && npm run build`

- [ ] **Step 4: Commit**

```bash
git add openbrain-gui/frontend/src/CostTables.jsx openbrain-gui/frontend/src/SessionDetail.jsx openbrain-gui/frontend/src/CostView.jsx
git commit -m "feat(gui): by-session Bot column + profile-scoped session drill-down"
```

---

## Task 12: `format.js` — profile in the saved-report name

**Files:**
- Modify: `openbrain-gui/frontend/src/format.js`, `openbrain-gui/frontend/src/CostView.jsx`

- [ ] **Step 1: Extend `reportName`**

```js
// CostReport_<profile>_<rangecode>_<dd.mm.yyyy-dd.mm.yyyy> so reports for
// different bots and ranges sort and scan together.
export function reportName(days, isToday, profile = 'all') {
  const code = /* existing range code */
  const datePart = /* existing */
  return `CostReport_${profile}_${code}_${datePart}`
}
```

Keep the existing body; only prepend `${profile}_`. Old-named reports still load (lookup is by exact name).

- [ ] **Step 2: Pass `profile` at the call site**

In `CostView.jsx`'s save handler: `const name = reportName(days, activeRange?.today, profile)`.

- [ ] **Step 3: Build + commit**

```bash
cd openbrain-gui/frontend && npm run build
git add openbrain-gui/frontend/src/format.js openbrain-gui/frontend/src/CostView.jsx
git commit -m "feat(gui): saved cost-report names carry the profile key"
```

---

## Task 13: Verify end-to-end + docs

**Files:**
- Modify: `costpage.md`

- [ ] **Step 1: Full backend suite**

Run: `cd openbrain-gui/backend && python -m pytest -q`
Expected: all green (was 177+ tests; now +~20).

- [ ] **Step 2: Frontend build**

Run: `cd openbrain-gui/frontend && npm run build`
Expected: clean.

- [ ] **Step 3: Local smoke (degraded — no `/hermes-data`)**

Start backend + frontend locally per `README.md` "Running it locally". With no `/hermes-data`:
- `GET /api/cost/profiles` → `[]`; the dropdown shows only "All".
- Cost page: Part 1 panels show the 503 message, Part 2 (external grid) fully usable, chart shows "collecting since …".
This confirms graceful degradation; real data needs the VPS.

- [ ] **Step 4: VPS deploy + real check**

```bash
ssh root@srv1608402.hstgr.cloud 'cd /root/HermesPlusOpenbrain && git pull --ff-only origin main && cd deploy && docker compose -f docker-compose.openbrain.yml up -d --build openbrain-gui'
```

Then at `https://gui.srv1608402.hstgr.cloud` → Cost report:
- dropdown lists **All, Hermes-Agent, coder, designer, master, openbrain, researcher, writer**;
- "All" shows the "Cost by bot" table and a "Config by bot" table; the total is higher than the pre-change (`default`-only) figure;
- selecting **openbrain** scopes every panel to it; the by-session table loses its Bot column; a session drill-down opens;
- the "Total cost of ownership" tile is identical across dropdown selections; the "Hermes API cost" tile changes.
- `docker logs deploy-openbrain-gui-1 --tail 20` — the ledger migration ran once, no errors; next poll tick seeds the six new profiles.

- [ ] **Step 5: `costpage.md`**

Add a "Per-bot view" section: the dropdown and its `all` default, what "All" merges vs. the two new tables, why TCO + the invoice comparison stay fleet-wide, and the `profile` column now on `usage_ledger`/`usage_watermark` (plus: `hermes insights` / a single bot's `state.db` is still the per-bot source of truth).

- [ ] **Step 6: Commit + finish**

```bash
git add costpage.md
git commit -m "docs: costpage.md — per-bot (per-profile) Cost page view"
```

Then invoke **superpowers:finishing-a-development-branch**.

---

## Self-review notes

- **Spec coverage:** profile discovery (T1), `profile` ledger column + migration (T2), per-profile ticks + `run_all` + `timeseries(profile)` (T3), poller fan-out (T4), `cost_merge` all-path incl. `per_bot_breakdown` + `config_all` (T5), `?profile=` on 5 routes + `/profiles` + `/by-bot` + fleet-wide TCO (T6), api.js (T7), selector + wiring (T8), CostByBot (T9), CostConfig dual shape (T10), Bot column + scoped drill-down (T11), report name (T12), docs + VPS verify (T13). All spec sections map to a task.
- **Placeholder scan:** the only "read the current file / match the real class name" instructions are in T6 (`/cost/summary` body) and T9/T10/T11 (CSS class names, `CostConfig` current render) — these are "follow the existing pattern" pointers, not missing content; the surrounding code is given.
- **Type consistency:** `apply_tick(rows, *, profile, path, observed_at)`, `run_once(*, profile, data_dir, path)`, `run_all(*, path)`, `timeseries(*, path, days, group, profile, now_iso)`, `dashboard_all(*, days, limit)`, `per_bot_breakdown(*, days)`, `config_all()` — consistent across tasks and tests. Frontend `getCostSession(id, profile)` used the same way in api.js (T7), CostView `selectedSession={id,profile}` (T11), SessionDetail (T11).
- **Test runner:** backend has pytest; frontend has none — frontend tasks verify via `npm run build` + the T13 live check, matching the original cost-page's approach.
