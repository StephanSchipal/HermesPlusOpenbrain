# OpenBrain GUI Cost & Token Usage Page (Spec A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Cost` page to the OpenBrain GUI showing where Hermes tokens and dollars go (read-only, from Hermes' `state.db`) plus a spreadsheet for external costs, combined into one monthly total.

**Architecture:** Additive to `openbrain-gui` only — no changes to Hermes, `openbrain-mcp`, or `openbrain-db`. The GUI container gains a read-only bind mount of Hermes' data directory. Because `state.db` is WAL-mode, every read copies `state.db` + `state.db-wal` to container scratch and opens the copy. A 5-minute poller diffs lifetime counters against a watermark to build a real time series in `gui.db`, since Hermes stores only per-session lifetime totals.

**Tech Stack:** Python 3.11 · FastAPI · SQLite (stdlib `sqlite3`) · httpx · React 18 · Vite · plain SVG for charts · pytest

**Spec:** `docs/superpowers/specs/2026-07-31-openbrain-gui-cost-page-design.md`

---

## Deviation from the spec (read before starting)

Spec §4.2 says the snapshot copy is opened with `mode=ro`. The plan opens the **copy** read-write
instead, and the spec's stated reason for the copy was imprecise.

**Corrected during implementation, verified experimentally.** The earlier claim — "SQLite must
replay the `-wal`, which requires write access to the database *file*, so `mode=ro` fails on any
live database" — is wrong. `mode=ro` against a live WAL database on a writable directory
*succeeds*.

The actual constraint: reading a WAL database makes SQLite create and maintain a **`-shm`
coordination file next to the database**, which requires write access to the containing
**directory**. Two consequences, either sufficient on its own:

- The production mount is `:ro`, so creating the `-shm` fails and the open errors.
- Even on a writable mount, we would be depositing files into Hermes' own data directory — the
  precise thing this module exists to avoid.

So the copy is still necessary; the copy lives in a `TemporaryDirectory` where SQLite can build its
`-shm` freely, and Hermes' files are only ever read. `tests/test_hermes_usage.py` pins this with
`test_snapshot_reads_a_real_wal_database` (the other fixtures use SQLite's default journal mode and
would not catch a regression here) and `test_snapshot_leaves_the_source_directory_untouched`, which
asserts on the whole directory rather than one mtime — a stray `-shm` is exactly the failure mode.

---

## File structure

### Backend — `openbrain-gui/backend/app/`

| File | Responsibility |
|---|---|
| `db.py` **(modify)** | Append four tables to `_SCHEMA` |
| `config.py` **(modify)** | Add `HERMES_DATA_DIR`, `LEDGER_POLL_SECONDS`, `FRANKFURTER_URL` |
| `fx.py` **(create)** | USD→EUR rate: fetch from frankfurter, cache in `gui.db`, manual override |
| `external_costs_store.py` **(create)** | Part 2 CRUD, period maths, currency derivation, single-flag invariant |
| `hermes_usage.py` **(create)** | Snapshot + read `state.db`; aggregations; session detail; config snapshot |
| `ledger_store.py` **(create)** | Poll tick (diff vs. watermark), time-series queries |
| `routes.py` **(modify)** | `/api/cost/*` endpoints |
| `main.py` **(modify)** | Start/stop the poller on the app lifespan |

### Backend tests — `openbrain-gui/backend/tests/`

`test_fx.py` · `test_external_costs_store.py` · `test_hermes_usage.py` · `test_ledger_store.py` ·
`test_db.py` **(modify)** · `test_routes.py` **(modify)**

### Frontend — `openbrain-gui/frontend/src/`

| File | Responsibility |
|---|---|
| `App.jsx` **(modify)** | `Cost` button next to `Show keyword graph`; render `CostView` |
| `api.js` **(modify)** | `/api/cost/*` client methods |
| `CostView.jsx` **(create)** | Page shell, date-range selector, composes the children below |
| `CostSummary.jsx` **(create)** | Header tiles (§5.1) |
| `CostChart.jsx` **(create)** | Stacked daily bars from the ledger (§5.2) |
| `CostTables.jsx` **(create)** | By-model / by-platform / by-session (§5.3) |
| `CostEfficiency.jsx` **(create)** | Token composition bar (§5.5) + per-platform efficiency table (§5.6) |
| `SessionDetail.jsx` **(create)** | Drill-down pane (§5.4) |
| `CostConfig.jsx` **(create)** | Prompt budget, top tools, config snapshot (§5.7–5.9) |
| `ExternalCostGrid.jsx` **(create)** | Part 2 spreadsheet + rate control (§6) |
| `index.css` **(modify)** | Styles for the above, reusing existing theme variables |

**No frontend test framework exists in this project** (`package.json` has no test runner and
`node_modules` contains none). Backend work is TDD. Frontend tasks are verified by building and
driving the running app in the browser — each frontend task states exactly what to check.

### Deploy

`deploy/docker-compose.openbrain.yml` **(modify)** — add the read-only mount.

---

## Task ordering

Tasks 1–6 deliver **Part 2 complete and shippable** with no dependency on the VPS mount, so the
work is useful even if Part 1 stalls. Tasks 7–12 add the Hermes dashboard. Tasks 13–15 add the
time series. Task 16 deploys.

---

## Task 1: gui.db schema

**Files:**
- Modify: `openbrain-gui/backend/app/db.py:7-23`
- Test: `openbrain-gui/backend/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_db.py`:

```python
def test_init_db_creates_cost_tables(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"external_costs", "fx_rate", "usage_ledger", "usage_watermark"} <= names


def test_init_db_is_idempotent_on_existing_db(tmp_path):
    """The deployed gui.db already has prompts/delete_log -- re-running
    init_db must add the new tables without touching existing rows."""
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        conn.execute("INSERT INTO prompts (text, created_at) VALUES ('keep me', '2026-07-31')")
        conn.commit()
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT text FROM prompts").fetchall()
    assert [r["text"] for r in rows] == ["keep me"]
```

Make sure the import line at the top of `test_db.py` includes `get_conn`:

```python
from app.db import init_db, get_conn
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_db.py -v
```

Expected: `test_init_db_creates_cost_tables` FAILS on the assertion (the four names are missing).

- [ ] **Step 3: Write minimal implementation**

In `openbrain-gui/backend/app/db.py`, append to the `_SCHEMA` string (before the closing `"""`):

```sql
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_db.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/db.py openbrain-gui/backend/tests/test_db.py
git commit -m "feat(gui): add cost/ledger tables to gui.db schema"
```

---

## Task 2: FX rate store and fetch

**Files:**
- Create: `openbrain-gui/backend/app/fx.py`
- Modify: `openbrain-gui/backend/app/config.py`
- Test: `openbrain-gui/backend/tests/test_fx.py`

- [ ] **Step 1: Write the failing test**

Create `openbrain-gui/backend/tests/test_fx.py`:

```python
# tests/test_fx.py
import httpx
import pytest
from app.db import init_db
from app import fx


def test_get_rate_returns_none_when_never_fetched(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    assert fx.get_rate(path=db_path) is None


def test_set_manual_rate_then_get(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.8607, path=db_path)
    rate = fx.get_rate(path=db_path)
    assert rate["usd_to_eur"] == 0.8607
    assert rate["source"] == "manual"
    assert rate["fetched_at"]


def test_set_manual_rate_overwrites_single_row(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.90, path=db_path)
    fx.set_manual_rate(0.85, path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.85


def test_refresh_rate_stores_frankfurter_response(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)

    def fake_get(url, timeout):
        assert "USD" in url and "EUR" in url
        return httpx.Response(200, json={"rates": {"EUR": 0.8607}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    rate = fx.refresh_rate(path=db_path)
    assert rate["usd_to_eur"] == 0.8607
    assert rate["source"] == "frankfurter"


def test_refresh_rate_keeps_cached_rate_on_network_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.90, path=db_path)

    def boom(url, timeout):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(fx.httpx, "get", boom)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.90


def test_refresh_rate_rejects_nonsense_payload(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)

    def fake_get(url, timeout):
        return httpx.Response(200, json={"rates": {}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_fx.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.fx'`.

- [ ] **Step 3: Write minimal implementation**

Add to `openbrain-gui/backend/app/config.py`:

```python
HERMES_DATA_DIR = os.environ.get("HERMES_DATA_DIR", "/hermes-data")
LEDGER_POLL_SECONDS = int(os.environ.get("LEDGER_POLL_SECONDS", "300"))
FRANKFURTER_URL = os.environ.get(
    "FRANKFURTER_URL", "https://api.frankfurter.app/latest?from=USD&to=EUR"
)
FX_TIMEOUT_SECONDS = 10.0
```

Create `openbrain-gui/backend/app/fx.py`:

```python
# app/fx.py
"""USD->EUR rate for the cost page. One row in `fx_rate`, refreshed from
frankfurter.app (European Central Bank daily reference rates -- free, no API
key) or set manually. A failed refresh NEVER clears the cached rate: the
external-cost grid must stay usable with a stale rate rather than break on a
network hiccup (design spec section 9)."""
import httpx
from datetime import datetime, timezone

from app.config import FRANKFURTER_URL, FX_TIMEOUT_SECONDS
from app.db import get_conn


class FxUnavailable(RuntimeError):
    """Raised when a fresh rate could not be obtained. The caller should keep
    showing whatever `get_rate` returns."""


def get_rate(*, path: str | None = None) -> dict | None:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT usd_to_eur, source, fetched_at FROM fx_rate WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def _store(usd_to_eur: float, source: str, *, path: str | None = None) -> dict:
    fetched_at = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO fx_rate (id, usd_to_eur, source, fetched_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                usd_to_eur = excluded.usd_to_eur,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (usd_to_eur, source, fetched_at),
        )
        conn.commit()
    return {"usd_to_eur": usd_to_eur, "source": source, "fetched_at": fetched_at}


def set_manual_rate(usd_to_eur: float, *, path: str | None = None) -> dict:
    if not usd_to_eur or usd_to_eur <= 0:
        raise ValueError("rate must be a positive number")
    return _store(float(usd_to_eur), "manual", path=path)


def refresh_rate(*, path: str | None = None) -> dict:
    try:
        resp = httpx.get(FRANKFURTER_URL, timeout=FX_TIMEOUT_SECONDS)
        resp.raise_for_status()
        value = resp.json()["rates"]["EUR"]
    except Exception as exc:
        raise FxUnavailable(f"could not fetch rate: {exc}") from exc
    if not isinstance(value, (int, float)) or value <= 0:
        raise FxUnavailable(f"frankfurter returned an unusable rate: {value!r}")
    return _store(float(value), "frankfurter", path=path)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_fx.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/fx.py openbrain-gui/backend/app/config.py openbrain-gui/backend/tests/test_fx.py
git commit -m "feat(gui): USD->EUR rate store with frankfurter refresh"
```

---

## Task 3: External cost maths (period + currency)

Pure functions, no database. Split from CRUD so the arithmetic is testable on its own.

**Files:**
- Create: `openbrain-gui/backend/app/external_costs_store.py`
- Test: `openbrain-gui/backend/tests/test_external_costs_store.py`

- [ ] **Step 1: Write the failing test**

Create `openbrain-gui/backend/tests/test_external_costs_store.py`:

```python
# tests/test_external_costs_store.py
import pytest
from app import external_costs_store as store

RATE = 0.80  # 1 USD = 0.80 EUR, chosen so the arithmetic is obvious by eye


def test_usd_row_derives_eur():
    row = {"amount": 10.0, "entered_currency": "USD"}
    assert store.amounts(row, RATE) == {"usd": 10.0, "eur": 8.0}


def test_eur_row_derives_usd():
    row = {"amount": 8.0, "entered_currency": "EUR"}
    assert store.amounts(row, RATE) == {"usd": 10.0, "eur": 8.0}


def test_amounts_without_rate_leaves_derived_side_none():
    row = {"amount": 10.0, "entered_currency": "USD"}
    assert store.amounts(row, None) == {"usd": 10.0, "eur": None}


def test_amounts_with_null_amount():
    row = {"amount": None, "entered_currency": "USD"}
    assert store.amounts(row, RATE) == {"usd": None, "eur": None}


@pytest.mark.parametrize("period,expected_monthly,expected_onetime", [
    ("monthly", 12.0, 0.0),
    ("yearly", 1.0, 0.0),
    ("onetime", 0.0, 12.0),
    ("none", 0.0, 0.0),
])
def test_period_rules(period, expected_monthly, expected_onetime):
    row = {"amount": 12.0, "entered_currency": "USD", "period": period}
    assert store.monthly_usd(row, RATE) == pytest.approx(expected_monthly)
    assert store.onetime_usd(row, RATE) == pytest.approx(expected_onetime)


def test_totals_sums_recurring_and_onetime_separately():
    rows = [
        {"amount": 12.99, "entered_currency": "USD", "period": "monthly"},
        {"amount": 120.0, "entered_currency": "USD", "period": "yearly"},
        {"amount": 50.0, "entered_currency": "USD", "period": "onetime"},
        {"amount": 999.0, "entered_currency": "USD", "period": "none"},
    ]
    totals = store.totals(rows, RATE)
    assert totals["monthly_usd"] == pytest.approx(22.99)   # 12.99 + 120/12
    assert totals["monthly_eur"] == pytest.approx(18.392)
    assert totals["onetime_usd"] == pytest.approx(50.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_external_costs_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.external_costs_store'`.

- [ ] **Step 3: Write minimal implementation**

Create `openbrain-gui/backend/app/external_costs_store.py`:

```python
# app/external_costs_store.py
"""The Part 2 spreadsheet: manually-entered external costs (Hostinger, the
Anthropic invoice, ...) stored in gui.db.

Currency model (design spec section 6.1): a row stores exactly ONE amount plus
the currency it was typed in. The other column is derived at read time. So
refreshing the exchange rate moves the derived figure and never rewrites what
the user actually entered."""

PERIODS = ("yearly", "monthly", "onetime", "none")
CURRENCIES = ("USD", "EUR")


def amounts(row: dict, rate: float | None) -> dict:
    """Both currency columns for display. `rate` is USD->EUR; None when no
    rate has ever been fetched, in which case only the entered side is known."""
    amount = row.get("amount")
    if amount is None:
        return {"usd": None, "eur": None}
    if row.get("entered_currency") == "EUR":
        return {"usd": amount / rate if rate else None, "eur": amount}
    return {"usd": amount, "eur": amount * rate if rate else None}


def monthly_usd(row: dict, rate: float | None) -> float:
    """Contribution to the recurring monthly total. `onetime` and `none` are
    excluded by design (spec section 6.2)."""
    usd = amounts(row, rate)["usd"]
    if usd is None:
        return 0.0
    period = row.get("period")
    if period == "monthly":
        return usd
    if period == "yearly":
        return usd / 12.0
    return 0.0


def onetime_usd(row: dict, rate: float | None) -> float:
    if row.get("period") != "onetime":
        return 0.0
    return amounts(row, rate)["usd"] or 0.0


def totals(rows: list[dict], rate: float | None) -> dict:
    monthly = sum(monthly_usd(r, rate) for r in rows)
    onetime = sum(onetime_usd(r, rate) for r in rows)
    return {
        "monthly_usd": monthly,
        "monthly_eur": monthly * rate if rate else None,
        "onetime_usd": onetime,
        "onetime_eur": onetime * rate if rate else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_external_costs_store.py -v
```

Expected: 10 PASS (the parametrised test counts as 4).

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/external_costs_store.py openbrain-gui/backend/tests/test_external_costs_store.py
git commit -m "feat(gui): external cost period and currency maths"
```

---

## Task 4: External cost CRUD + validation + single-flag invariant

**Files:**
- Modify: `openbrain-gui/backend/app/external_costs_store.py`
- Test: `openbrain-gui/backend/tests/test_external_costs_store.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_external_costs_store.py`:

```python
from app.db import init_db


def _db(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    return db_path


def test_save_assigns_ids_and_lists_back(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": "https://hpanel.hostinger.com",
         "comments": "KVM2", "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    assert saved[0]["id"] is not None
    rows = store.list_rows(path=db_path)
    assert len(rows) == 1
    assert rows[0]["name"] == "Hostinger"
    assert rows[0]["amount"] == 12.99


def test_save_updates_existing_row_in_place(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    row_id = saved[0]["id"]
    store.save_rows([{**saved[0], "amount": 14.99}], path=db_path)
    rows = store.list_rows(path=db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["amount"] == 14.99


def test_delete_row(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Gone", "period": "none", "amount": None, "entered_currency": "USD",
         "url": None, "comments": None, "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    assert store.delete_row(saved[0]["id"], path=db_path) is True
    assert store.list_rows(path=db_path) == []
    assert store.delete_row(9999, path=db_path) is False


def test_only_one_row_can_carry_the_compare_flag(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Anthropic", "period": "monthly", "amount": 94.17,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 1, "sort_order": 0},
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 0, "sort_order": 1},
    ], path=db_path)
    # Now flag the second row too -- the first must be cleared.
    store.save_rows([{**saved[1], "compare_to_estimate": 1}], path=db_path)
    rows = {r["name"]: r for r in store.list_rows(path=db_path)}
    assert rows["Hostinger"]["compare_to_estimate"] == 1
    assert rows["Anthropic"]["compare_to_estimate"] == 0


@pytest.mark.parametrize("bad", [
    {"period": "weekly"},
    {"entered_currency": "GBP"},
    {"amount": -5.0},
    {"url": "javascript:alert(1)"},
    {"url": "file:///etc/passwd"},
])
def test_save_rejects_invalid_field(tmp_path, bad):
    db_path = _db(tmp_path)
    row = {"name": "x", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "url": None, "comments": None, "compare_to_estimate": 0, "sort_order": 0}
    with pytest.raises(ValueError):
        store.save_rows([{**row, **bad}], path=db_path)


def test_save_accepts_http_and_https_urls(tmp_path):
    db_path = _db(tmp_path)
    row = {"name": "x", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "comments": None, "compare_to_estimate": 0, "sort_order": 0}
    store.save_rows([
        {**row, "url": "https://example.com/billing"},
        {**row, "url": "http://example.com/billing"},
        {**row, "url": ""},
    ], path=db_path)
    assert len(store.list_rows(path=db_path)) == 3


def test_list_rows_ordered_by_sort_order_then_id(tmp_path):
    db_path = _db(tmp_path)
    row = {"period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "url": None, "comments": None, "compare_to_estimate": 0}
    store.save_rows([
        {**row, "name": "third", "sort_order": 2},
        {**row, "name": "first", "sort_order": 0},
        {**row, "name": "second", "sort_order": 1},
    ], path=db_path)
    assert [r["name"] for r in store.list_rows(path=db_path)] == ["first", "second", "third"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_external_costs_store.py -v
```

Expected: the new tests FAIL with `AttributeError: module 'app.external_costs_store' has no attribute 'save_rows'`.

- [ ] **Step 3: Write minimal implementation**

Append to `openbrain-gui/backend/app/external_costs_store.py`:

```python
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.db import get_conn

_ALLOWED_URL_SCHEMES = ("http", "https")

_FIELDS = ("name", "period", "amount", "entered_currency", "url",
           "comments", "compare_to_estimate", "sort_order")


def _validate(row: dict) -> None:
    if row.get("period") not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {row.get('period')!r}")
    if row.get("entered_currency") not in CURRENCIES:
        raise ValueError(
            f"entered_currency must be one of {CURRENCIES}, got {row.get('entered_currency')!r}"
        )
    amount = row.get("amount")
    if amount is not None and amount < 0:
        raise ValueError(f"amount must be >= 0, got {amount}")
    url = row.get("url")
    if url:
        # Blocks javascript:/file:/data: URLs, which the grid renders as a
        # clickable anchor -- see design spec section 9.1.
        if urlparse(url).scheme not in _ALLOWED_URL_SCHEMES:
            raise ValueError(f"url scheme must be http or https, got {url!r}")


def list_rows(*, path: str | None = None) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, period, amount, entered_currency, url, comments,
                   compare_to_estimate, sort_order, created_at, updated_at
            FROM external_costs ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def save_rows(rows: list[dict], *, path: str | None = None) -> list[dict]:
    """Insert rows without an `id`, update those with one. Validation runs over
    every row BEFORE anything is written, so a bad row in the batch cannot leave
    a half-saved table behind."""
    for row in rows:
        _validate(row)

    now = datetime.now(timezone.utc).isoformat()
    saved: list[dict] = []
    with get_conn(path) as conn:
        for row in rows:
            values = tuple(row.get(f) for f in _FIELDS)
            row_id = row.get("id")
            if row_id is None:
                cur = conn.execute(
                    f"""
                    INSERT INTO external_costs
                        ({", ".join(_FIELDS)}, created_at, updated_at)
                    VALUES ({", ".join("?" * len(_FIELDS))}, ?, ?)
                    """,
                    values + (now, now),
                )
                row_id = cur.lastrowid
            else:
                conn.execute(
                    f"""
                    UPDATE external_costs
                    SET {", ".join(f"{f} = ?" for f in _FIELDS)}, updated_at = ?
                    WHERE id = ?
                    """,
                    values + (now, row_id),
                )
            if row.get("compare_to_estimate"):
                # Invariant (design spec section 6.3): at most one row carries
                # the flag. Enforced here rather than in the UI so it holds no
                # matter who writes.
                conn.execute(
                    "UPDATE external_costs SET compare_to_estimate = 0 WHERE id != ?",
                    (row_id,),
                )
            saved.append({**row, "id": row_id})
        conn.commit()
    return saved


def delete_row(row_id: int, *, path: str | None = None) -> bool:
    with get_conn(path) as conn:
        cur = conn.execute("DELETE FROM external_costs WHERE id = ?", (row_id,))
        conn.commit()
        return cur.rowcount > 0


def flagged_row(*, path: str | None = None) -> dict | None:
    """The row marked 'compare to Hermes estimate', if any."""
    for row in list_rows(path=path):
        if row["compare_to_estimate"]:
            return row
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_external_costs_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/external_costs_store.py openbrain-gui/backend/tests/test_external_costs_store.py
git commit -m "feat(gui): external cost CRUD with validation and single-flag invariant"
```

---

## Task 5: Part 2 routes

**Files:**
- Modify: `openbrain-gui/backend/app/routes.py`
- Modify: `openbrain-gui/backend/tests/test_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_routes.py`:

```python
def test_external_costs_round_trip(client):
    resp = client.put("/api/cost/external", json={"rows": [
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": "https://hpanel.hostinger.com",
         "comments": "KVM2", "compare_to_estimate": False, "sort_order": 0},
    ]})
    assert resp.status_code == 200
    listed = client.get("/api/cost/external").json()
    assert len(listed["rows"]) == 1
    assert listed["rows"][0]["name"] == "Hostinger"
    assert listed["totals"]["onetime_usd"] == 0.0
    assert listed["totals"]["incomplete"] is False


def test_external_totals_report_incomplete_without_a_rate(client):
    """A EUR row contributes 0 to the USD total until a rate exists. The API
    must say so -- see external_costs_store.totals."""
    client.put("/api/cost/external", json={"rows": [
        {"name": "Euro thing", "period": "monthly", "amount": 10.0,
         "entered_currency": "EUR", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    totals = client.get("/api/cost/external").json()["totals"]
    assert totals["incomplete"] is True
    assert totals["monthly_usd"] == pytest.approx(0.0)


def test_external_costs_reject_bad_url(client):
    resp = client.put("/api/cost/external", json={"rows": [
        {"name": "bad", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
         "url": "javascript:alert(1)", "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"]


def test_delete_external_cost_row(client):
    client.put("/api/cost/external", json={"rows": [
        {"name": "Gone", "period": "none", "amount": None, "entered_currency": "USD",
         "url": None, "comments": None, "compare_to_estimate": False, "sort_order": 0},
    ]})
    row_id = client.get("/api/cost/external").json()["rows"][0]["id"]
    assert client.delete(f"/api/cost/external/{row_id}").status_code == 200
    assert client.get("/api/cost/external").json()["rows"] == []
    assert client.delete(f"/api/cost/external/{row_id}").status_code == 404


def test_fx_manual_override_then_read(client):
    assert client.get("/api/cost/fx").json()["rate"] is None
    resp = client.put("/api/cost/fx", json={"usd_to_eur": 0.8607})
    assert resp.status_code == 200
    assert client.get("/api/cost/fx").json()["rate"]["usd_to_eur"] == 0.8607


def test_fx_refresh_failure_returns_503_and_keeps_rate(client, monkeypatch):
    import app.fx as fx_module
    client.put("/api/cost/fx", json={"usd_to_eur": 0.90})

    def boom(*, path=None):
        raise fx_module.FxUnavailable("no network")

    monkeypatch.setattr(fx_module, "refresh_rate", boom)
    assert client.post("/api/cost/fx/refresh").status_code == 503
    assert client.get("/api/cost/fx").json()["rate"]["usd_to_eur"] == 0.90


def test_external_totals_use_current_rate(client):
    client.put("/api/cost/fx", json={"usd_to_eur": 0.80})
    client.put("/api/cost/external", json={"rows": [
        {"name": "Yearly thing", "period": "yearly", "amount": 120.0,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    totals = client.get("/api/cost/external").json()["totals"]
    assert totals["monthly_usd"] == pytest.approx(10.0)
    assert totals["monthly_eur"] == pytest.approx(8.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k external or fx
```

Expected: FAIL with 404s — the routes do not exist.

- [ ] **Step 3: Write minimal implementation**

In `openbrain-gui/backend/app/routes.py`, extend the imports:

```python
from app import mcp_client, prompts_store, delete_log_store, subject_line, graph
from app import external_costs_store, fx
```

Add the request models near the other `BaseModel` classes:

```python
class ExternalCostRow(BaseModel):
    id: int | None = None
    name: str = ""
    period: Literal["yearly", "monthly", "onetime", "none"] = "monthly"
    amount: float | None = None
    entered_currency: Literal["USD", "EUR"] = "USD"
    url: str | None = None
    comments: str | None = None
    compare_to_estimate: bool = False
    sort_order: int = 0

class ExternalCostSaveRequest(BaseModel):
    rows: list[ExternalCostRow]

class FxRateRequest(BaseModel):
    usd_to_eur: float
```

Append the routes:

```python
@router.get("/cost/external")
def get_external_costs():
    rate_row = fx.get_rate()
    rate = rate_row["usd_to_eur"] if rate_row else None
    rows = external_costs_store.list_rows()
    for row in rows:
        row.update(external_costs_store.amounts(row, rate))
    return {
        "rows": rows,
        "totals": external_costs_store.totals(rows, rate),
        "rate": rate_row,
    }

@router.put("/cost/external")
def put_external_costs(body: ExternalCostSaveRequest):
    payload = [r.model_dump() for r in body.rows]
    for row in payload:
        row["compare_to_estimate"] = int(row["compare_to_estimate"])
    try:
        external_costs_store.save_rows(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_external_costs()

@router.delete("/cost/external/{row_id}")
def delete_external_cost(row_id: int):
    if not external_costs_store.delete_row(row_id):
        raise HTTPException(status_code=404, detail="row not found")
    return {"id": row_id, "deleted": True}

@router.get("/cost/fx")
def get_fx():
    return {"rate": fx.get_rate()}

@router.post("/cost/fx/refresh")
def post_fx_refresh():
    try:
        return {"rate": fx.refresh_rate()}
    except fx.FxUnavailable as exc:
        # 503, not 500: the cached rate is still valid and the UI keeps working.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.put("/cost/fx")
def put_fx(body: FxRateRequest):
    try:
        return {"rate": fx.set_manual_rate(body.usd_to_eur)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: all PASS, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui): /api/cost/external and /api/cost/fx endpoints"
```

---

## Task 6: Cost button, page shell, and the external cost grid

Part 2 is complete and usable after this task.

**Files:**
- Create: `openbrain-gui/frontend/src/CostView.jsx`
- Create: `openbrain-gui/frontend/src/ExternalCostGrid.jsx`
- Modify: `openbrain-gui/frontend/src/App.jsx:288-296`
- Modify: `openbrain-gui/frontend/src/api.js:38`
- Modify: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Add the API client methods**

In `openbrain-gui/frontend/src/api.js`, add inside the `api` object after `getGraph`:

```js
  getExternalCosts: () => request('/cost/external'),
  saveExternalCosts: (rows) =>
    request('/cost/external', { method: 'PUT', body: JSON.stringify({ rows }) }),
  deleteExternalCost: (id) => request(`/cost/external/${id}`, { method: 'DELETE' }),
  getFx: () => request('/cost/fx'),
  refreshFx: () => request('/cost/fx/refresh', { method: 'POST' }),
  setFx: (usd_to_eur) =>
    request('/cost/fx', { method: 'PUT', body: JSON.stringify({ usd_to_eur }) }),
```

- [ ] **Step 2: Create the grid component**

Create `openbrain-gui/frontend/src/ExternalCostGrid.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { api } from './api'

const PERIODS = ['yearly', 'monthly', 'onetime', 'none']

function blankRow(sortOrder) {
  return {
    id: null, name: '', period: 'monthly', amount: null, entered_currency: 'USD',
    url: '', comments: '', compare_to_estimate: false, sort_order: sortOrder,
    usd: null, eur: null,
  }
}

// Two decimals for money, blank for "not known yet" (no rate fetched).
function money(value) {
  return value == null ? '' : Number(value).toFixed(2)
}

export default function ExternalCostGrid({ onTotalsChange }) {
  const [rows, setRows] = useState([])
  const [rate, setRate] = useState(null)
  const [rateInput, setRateInput] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    const data = await api.getExternalCosts()
    setRows(data.rows)
    setRate(data.rate)
    setRateInput(data.rate ? String(data.rate.usd_to_eur) : '')
    setDirty(false)
    onTotalsChange?.(data.totals)
  }

  useEffect(() => { load().catch((e) => setError(e.message)) }, [])

  function patch(idx, changes) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...changes } : r)))
    setDirty(true)
  }

  // Typing in one currency box makes THAT currency authoritative; the other
  // is derived on the next load. See design spec section 6.1.
  function setAmount(idx, currency, raw) {
    const amount = raw === '' ? null : Number(raw)
    if (raw !== '' && Number.isNaN(amount)) return
    const factor = rate?.usd_to_eur
    patch(idx, {
      amount,
      entered_currency: currency,
      usd: currency === 'USD' ? amount : (factor && amount != null ? amount / factor : null),
      eur: currency === 'EUR' ? amount : (factor && amount != null ? amount * factor : null),
    })
  }

  async function handleSave() {
    setBusy(true); setError(null)
    try {
      await api.saveExternalCosts(rows.map(({ usd, eur, ...row }) => row))
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function handleDelete() {
    if (selectedIdx == null) return
    const row = rows[selectedIdx]
    if (!window.confirm(`Delete row "${row.name || '(unnamed)'}"?`)) return
    setBusy(true); setError(null)
    try {
      if (row.id != null) await api.deleteExternalCost(row.id)
      setSelectedIdx(null)
      if (row.id == null) {
        setRows((prev) => prev.filter((_, i) => i !== selectedIdx))
      } else {
        await load()
      }
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function handleRefreshRate() {
    setBusy(true); setError(null)
    try {
      const { rate: fresh } = await api.refreshFx()
      setRate(fresh); setRateInput(String(fresh.usd_to_eur))
      await load()
    } catch (e) {
      setError(`${e.message} — showing the previous rate`)
    } finally { setBusy(false) }
  }

  async function handleManualRate() {
    const value = Number(rateInput)
    if (!value || value <= 0) { setError('Rate must be a positive number'); return }
    setBusy(true); setError(null)
    try {
      const { rate: fresh } = await api.setFx(value)
      setRate(fresh)
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <section className="external-costs">
      <div className="external-costs-header">
        <h3>External costs</h3>
        <div className="fx-control">
          <label>
            Rate $ → €
            <input
              value={rateInput}
              onChange={(e) => setRateInput(e.target.value)}
              onBlur={handleManualRate}
              size={8}
            />
          </label>
          <button onClick={handleRefreshRate} disabled={busy} title="Fetch ECB daily rate">⟳</button>
          <span className="fx-meta">
            {rate ? `${rate.source}, ${rate.fetched_at.slice(0, 10)}` : 'no rate yet'}
          </span>
        </div>
      </div>

      {error && <p className="external-costs-error">{error}</p>}

      <table className="external-costs-table">
        <thead>
          <tr>
            <th /><th>Name</th><th>Period</th><th>$</th><th>€</th>
            <th>URL</th><th>Comments</th><th title="Compare to Hermes estimate">≈</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={row.id ?? `new-${idx}`} className={selectedIdx === idx ? 'selected' : ''}>
              <td>
                <input type="radio" name="external-cost-row" checked={selectedIdx === idx}
                       onChange={() => setSelectedIdx(idx)} />
              </td>
              <td><input value={row.name} onChange={(e) => patch(idx, { name: e.target.value })} /></td>
              <td>
                <select value={row.period} onChange={(e) => patch(idx, { period: e.target.value })}>
                  {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </td>
              <td>
                <input className="num" value={money(row.usd)}
                       onChange={(e) => setAmount(idx, 'USD', e.target.value)} />
              </td>
              <td>
                <input className="num" value={money(row.eur)}
                       onChange={(e) => setAmount(idx, 'EUR', e.target.value)} />
              </td>
              <td>
                <input value={row.url || ''} onChange={(e) => patch(idx, { url: e.target.value })} />
                {row.url && (
                  <a href={row.url} target="_blank" rel="noopener noreferrer" title="Open billing page">↗</a>
                )}
              </td>
              <td>
                <input value={row.comments || ''}
                       onChange={(e) => patch(idx, { comments: e.target.value })} />
              </td>
              <td>
                <input type="checkbox" checked={!!row.compare_to_estimate}
                       onChange={(e) => patch(idx, { compare_to_estimate: e.target.checked })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="external-costs-actions">
        <button onClick={() => { setRows((p) => [...p, blankRow(p.length)]); setDirty(true) }}>
          Add row
        </button>
        <button onClick={handleDelete} disabled={selectedIdx == null || busy}>Delete row</button>
        <button onClick={handleSave} disabled={!dirty || busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Create the page shell**

Create `openbrain-gui/frontend/src/CostView.jsx`:

```jsx
import { useState } from 'react'
import ExternalCostGrid from './ExternalCostGrid'

const RANGES = [7, 30, 90]

export default function CostView() {
  const [days, setDays] = useState(30)
  const [externalTotals, setExternalTotals] = useState(null)

  return (
    <div className="cost-view">
      <div className="cost-range">
        {RANGES.map((d) => (
          <button key={d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>
            {d} days
          </button>
        ))}
      </div>

      {externalTotals && (
        <p className="cost-external-total">
          External recurring: ${externalTotals.monthly_usd.toFixed(2)}/month
          {externalTotals.onetime_usd > 0 &&
            ` · one-off: $${externalTotals.onetime_usd.toFixed(2)}`}
          {/* A EUR row with no FX rate contributes 0 to the sums above, so the
              figure is understated. Say so rather than show it bare. */}
          {externalTotals.incomplete && (
            <span className="cost-warning"> — incomplete, a euro row needs an exchange rate</span>
          )}
        </p>
      )}

      <ExternalCostGrid onTotalsChange={setExternalTotals} />
    </div>
  )
}
```

- [ ] **Step 4: Wire the button into App.jsx**

In `openbrain-gui/frontend/src/App.jsx`, add the import next to the others at the top:

```jsx
import CostView from './CostView'
```

Add the button after the keyword-graph button (currently `App.jsx:288-290`):

```jsx
        <button onClick={() => setView((v) => (v === 'cost' ? 'results' : 'cost'))}>
          {view === 'cost' ? 'Back to results' : 'Cost'}
        </button>
```

Add the branch to the render chain (currently starting `App.jsx:293`):

```jsx
      {view === 'deleteLog' ? (
        <DeleteLogView />
      ) : view === 'graph' ? (
        <KeywordGraph onKeywordClick={handleKeywordClick} />
      ) : view === 'cost' ? (
        <CostView />
      ) : searching ? (
```

- [ ] **Step 5: Add styles**

Append to `openbrain-gui/frontend/src/index.css`:

```css
.cost-view { display: flex; flex-direction: column; gap: 1rem; }
.cost-range { display: flex; gap: 0.5rem; }
.cost-range button.active { font-weight: 600; text-decoration: underline; }
.cost-external-total { margin: 0; opacity: 0.85; }
.cost-warning { color: #c0392b; }

.external-costs-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.fx-control { display: flex; align-items: center; gap: 0.5rem; }
.fx-meta { opacity: 0.7; font-size: 0.85em; }
.external-costs-error { color: #c0392b; margin: 0.25rem 0; }

.external-costs-table { width: 100%; border-collapse: collapse; }
.external-costs-table th, .external-costs-table td { padding: 0.25rem 0.4rem; text-align: left; }
.external-costs-table input[type="text"], .external-costs-table input:not([type]) { width: 100%; }
.external-costs-table input.num { width: 6rem; text-align: right; }
.external-costs-table tr.selected { background: rgba(127, 127, 127, 0.15); }
.external-costs-actions { display: flex; gap: 0.5rem; }
```

- [ ] **Step 6: Build and verify in the browser**

```bash
cd openbrain-gui/frontend && npm run build
```

Expected: build succeeds with no errors.

Then start the backend and open the app:

```bash
cd openbrain-gui/backend && python -m uvicorn app.main:app --port 8000
```

In the browser at `http://localhost:8000`, verify each of these:

1. A `Cost` button appears next to `Show keyword graph`.
2. Clicking it shows the cost page; the button text becomes `Back to results`.
3. `Add row` appends an editable row.
4. Typing `12.99` into the `$` box leaves `€` blank (no rate fetched yet).
5. Clicking `⟳` fetches a rate; the `€` column fills after `Save`.
6. Entering a value in `€` instead and saving keeps that € value exactly, deriving `$`.
7. Selecting a row's radio and clicking `Delete row` prompts, then removes it.
8. Entering `javascript:alert(1)` in URL and saving shows a red error and saves nothing.
9. Ticking `≈` on two different rows and saving leaves only the last one ticked.

- [ ] **Step 7: Commit**

```bash
git add openbrain-gui/frontend/src/CostView.jsx openbrain-gui/frontend/src/ExternalCostGrid.jsx openbrain-gui/frontend/src/App.jsx openbrain-gui/frontend/src/api.js openbrain-gui/frontend/src/index.css
git commit -m "feat(gui): Cost page shell and external cost spreadsheet"
```

---

## Task 7: Hermes state.db snapshot reader

**Files:**
- Create: `openbrain-gui/backend/app/hermes_usage.py`
- Test: `openbrain-gui/backend/tests/test_hermes_usage.py`

- [ ] **Step 1: Write the failing test**

Create `openbrain-gui/backend/tests/test_hermes_usage.py`:

```python
# tests/test_hermes_usage.py
"""Tests build a fixture state.db with the real column names taken from the
live Hermes database (design spec section 3). Two details are load-bearing and
easy to get wrong:

  * `first_seen`/`last_seen` are EPOCH FLOATS, not ISO strings. A
    `datetime("now","-30 days")` comparison silently returns zero rows.
  * platform comes from `sessions.source`, joined on session_id -- it is not
    a column on session_model_usage.
"""
import sqlite3
import time
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


def test_snapshot_copies_wal_when_present(hermes_dir, tmp_path):
    open(f"{hermes_dir}/state.db-wal", "wb").close()
    with hermes_usage.snapshot(hermes_dir) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 3


def test_read_usage_rows_returns_raw_counters_with_platform(hermes_dir):
    rows = hermes_usage.read_usage_rows(hermes_dir)
    assert len(rows) == 3
    by_session = {r["session_id"]: r for r in rows}
    assert by_session["s-wa"]["cache_read_tokens"] == 30_000_000
    assert by_session["s-wa"]["api_call_count"] == 193
    # Platform is denormalised into the ledger so the chart can group by it
    # without the mount -- see Task 13.
    assert by_session["s-wa"]["platform"] == "whatsapp"
    assert by_session["s-cli"]["platform"] == "cli"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.hermes_usage'`.

- [ ] **Step 3: Write minimal implementation**

Create `openbrain-gui/backend/app/hermes_usage.py`:

```python
# app/hermes_usage.py
"""Read-only access to Hermes' own `state.db`, mounted at /hermes-data.

`state.db` is WAL-mode. SQLite must replay the -wal into the database when
opening it, which needs WRITE access to the database file -- so the live file
cannot be opened at all from a read-only mount. Every read therefore copies
state.db (+ -wal) into a TemporaryDirectory and opens the COPY read-write.
Hermes' real files are never opened for writing; the mount stays :ro so it is
not even possible.

`state.db-shm` is deliberately NOT copied: it is derived state that SQLite
rebuilds from the WAL, and a stale copy is worse than none.

A copy taken mid-write can land torn. That is an expected failure, not an
exception path worth special-casing -- the caller serves the previous snapshot.
"""
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from app.config import HERMES_DATA_DIR


class HermesDataUnavailable(RuntimeError):
    """/hermes-data is not mounted, or state.db is not readable there."""


@contextmanager
def snapshot(data_dir: str | None = None) -> Iterator[sqlite3.Connection]:
    src = Path(data_dir or HERMES_DATA_DIR) / "state.db"
    if not src.is_file():
        raise HermesDataUnavailable(f"Hermes state.db not found at {src}")
    with tempfile.TemporaryDirectory(prefix="hermes-snap-") as tmp:
        dst = Path(tmp) / "state.db"
        try:
            shutil.copy2(src, dst)
            wal = src.with_name("state.db-wal")
            if wal.is_file():
                shutil.copy2(wal, dst.with_name("state.db-wal"))
        except OSError as exc:
            raise HermesDataUnavailable(f"could not copy state.db: {exc}") from exc
        conn = sqlite3.connect(str(dst))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def read_usage_rows(data_dir: str | None = None) -> list[dict]:
    """Raw per-(session, model, task) lifetime counters, plus the session's
    platform. Used by the ledger poller, which needs the numbers unaggregated
    and unfiltered -- and needs `platform` denormalised, because the chart must
    group by it later without re-reading state.db.

    LEFT JOIN, not JOIN: a usage row whose session has been pruned still
    carries real spend and must not vanish from the ledger."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT u.session_id, u.model, COALESCE(u.task, '') AS task,
                   COALESCE(s.source, '')            AS platform,
                   COALESCE(u.api_call_count, 0)     AS api_call_count,
                   COALESCE(u.input_tokens, 0)       AS input_tokens,
                   COALESCE(u.output_tokens, 0)      AS output_tokens,
                   COALESCE(u.cache_read_tokens, 0)  AS cache_read_tokens,
                   COALESCE(u.cache_write_tokens, 0) AS cache_write_tokens,
                   COALESCE(u.reasoning_tokens, 0)   AS reasoning_tokens,
                   COALESCE(u.estimated_cost_usd, 0) AS estimated_cost_usd,
                   u.last_seen
            FROM session_model_usage u
            LEFT JOIN sessions s ON s.id = u.session_id
            """
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/hermes_usage.py openbrain-gui/backend/tests/test_hermes_usage.py
git commit -m "feat(gui): read-only snapshot reader for Hermes state.db"
```

---

## Task 8: Aggregations by model and platform

**Files:**
- Modify: `openbrain-gui/backend/app/hermes_usage.py`
- Test: `openbrain-gui/backend/tests/test_hermes_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_hermes_usage.py`:

```python
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
    """Design spec section 5.6 -- the numbers that actually move the bill.
    WhatsApp writes far more cache per call than CLI, which is the whole
    reason this panel exists."""
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: FAIL — `AttributeError: module 'app.hermes_usage' has no attribute 'by_model'`.

- [ ] **Step 3: Write minimal implementation**

Append to `openbrain-gui/backend/app/hermes_usage.py`:

```python
import time

# A session's usage row spans its whole life, so it cannot be split across days.
# The window rule (design spec section 5) is: include the row when `last_seen`
# falls inside it, and count the whole row. Exact totals, slightly late
# attribution -- proportional splitting would invent numbers.
_SUM_COLUMNS = """
    COUNT(DISTINCT u.session_id)               AS sessions,
    SUM(COALESCE(u.api_call_count, 0))         AS api_calls,
    SUM(COALESCE(u.input_tokens, 0))           AS input_tokens,
    SUM(COALESCE(u.output_tokens, 0))          AS output_tokens,
    SUM(COALESCE(u.cache_read_tokens, 0))      AS cache_read_tokens,
    SUM(COALESCE(u.cache_write_tokens, 0))     AS cache_write_tokens,
    SUM(COALESCE(u.reasoning_tokens, 0))       AS reasoning_tokens,
    SUM(COALESCE(u.estimated_cost_usd, 0))     AS cost_usd
"""


def _window(days: int, now: float | None) -> tuple[float, float]:
    """The window is `[now - days, now]`, not just `>= cutoff` -- a lower
    bound alone would let rows leak in whenever `now` is set earlier than the
    data (e.g. probing an empty window in the past), since their real
    `last_seen` values still satisfy `>= cutoff`."""
    effective_now = now if now is not None else time.time()
    return effective_now - days * 86400.0, effective_now


def _grouped(data_dir: str | None, days: int, now: float | None,
             group_sql: str, label: str) -> list[dict]:
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT {group_sql} AS {label}, {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY {group_sql}
            ORDER BY cost_usd DESC
            """,
            _window(days, now),
        ).fetchall()
    return [dict(r) for r in rows]


def by_model(data_dir: str | None = None, *, days: int = 30,
             now: float | None = None) -> list[dict]:
    return _grouped(data_dir, days, now, "u.model", "model")


def by_platform(data_dir: str | None = None, *, days: int = 30,
                now: float | None = None) -> list[dict]:
    return _grouped(data_dir, days, now, "s.source", "platform")


def summary(data_dir: str | None = None, *, days: int = 30,
            now: float | None = None) -> dict:
    with snapshot(data_dir) as conn:
        row = dict(conn.execute(
            f"""
            SELECT {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            """,
            _window(days, now),
        ).fetchone())
    for key, value in row.items():
        if value is None:
            row[key] = 0
    denominator = (row["cache_read_tokens"] + row["cache_write_tokens"]
                   + row["input_tokens"])
    row["cache_hit_rate"] = (
        row["cache_read_tokens"] / denominator if denominator else None
    )
    row["cost_status"] = "estimated"
    return row


def efficiency(data_dir: str | None = None, *, days: int = 30,
               now: float | None = None) -> list[dict]:
    """Per-platform per-call averages (design spec section 5.6). Prompt size
    times call count is what drives the bill, and cache WRITE volume per call
    is where the money actually goes -- a write costs 12.5x a read."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT s.source AS platform, AVG(s.message_count) AS avg_messages_per_session,
                   {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY s.source
            HAVING api_calls > 0
            ORDER BY cost_usd DESC
            """,
            _window(days, now),
        ).fetchall()

    result = []
    for raw in rows:
        row = dict(raw)
        calls = row["api_calls"]
        total_tokens = (row["input_tokens"] + row["output_tokens"]
                        + row["cache_read_tokens"] + row["cache_write_tokens"])
        # `HAVING api_calls > 0` already excludes the divide-by-zero case;
        # the guard keeps that true if the HAVING clause is ever relaxed.
        row["tokens_per_call"] = total_tokens / calls if calls else None
        row["cache_write_per_call"] = row["cache_write_tokens"] / calls if calls else None
        row["cost_per_call"] = row["cost_usd"] / calls if calls else None
        result.append(row)
    return result
```

Note: `by_model`/`by_platform` must be defined *after* `snapshot`, and the
`import time` line belongs with the other imports at the top of the file — move
it there rather than leaving it mid-file.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/hermes_usage.py openbrain-gui/backend/tests/test_hermes_usage.py
git commit -m "feat(gui): aggregate Hermes usage by model, platform, and efficiency"
```

---

## Task 9: Top sessions and session drill-down

**Files:**
- Modify: `openbrain-gui/backend/app/hermes_usage.py`
- Test: `openbrain-gui/backend/tests/test_hermes_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_hermes_usage.py`:

```python
def test_by_session_returns_titles_and_costs_sorted(hermes_dir):
    rows = hermes_usage.by_session(hermes_dir, days=30, now=NOW)
    assert [r["title"] for r in rows] == ["Context Engineering", "Twilio Voice"]
    assert rows[0]["platform"] == "whatsapp"
    assert rows[0]["cost_usd"] == pytest.approx(18.39)
    assert rows[0]["message_count"] == 347


def test_by_session_respects_limit(hermes_dir):
    rows = hermes_usage.by_session(hermes_dir, days=30, now=NOW, limit=1)
    assert len(rows) == 1
    assert rows[0]["title"] == "Context Engineering"


def test_session_detail_includes_prompt_and_compression_state(hermes_dir):
    detail = hermes_usage.session_detail("s-cli", data_dir=hermes_dir)
    assert detail["title"] == "Twilio Voice"
    assert detail["platform"] == "cli"
    assert detail["cwd"] == "/root/proj"
    assert detail["git_branch"] == "main"
    assert detail["compression_fallback_streak"] == 2
    assert detail["compression_failure_error"] == "boom"
    assert detail["system_prompt_chars"] == 17000
    assert detail["system_prompt"].startswith("yyy")
    assert len(detail["models"]) == 1
    assert detail["models"][0]["model"] == "claude-opus-4-8"


def test_session_detail_unknown_id_returns_none(hermes_dir):
    assert hermes_usage.session_detail("nope", data_dir=hermes_dir) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v -k session
```

Expected: FAIL — `AttributeError: ... has no attribute 'by_session'`.

- [ ] **Step 3: Write minimal implementation**

Append to `openbrain-gui/backend/app/hermes_usage.py`:

```python
def by_session(data_dir: str | None = None, *, days: int = 30,
               now: float | None = None, limit: int = 50) -> list[dict]:
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT u.session_id, s.title, s.source AS platform,
                   s.message_count, s.tool_call_count,
                   GROUP_CONCAT(DISTINCT u.model) AS models,
                   MAX(u.last_seen) AS last_seen,
                   {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY u.session_id
            ORDER BY cost_usd DESC
            LIMIT ?
            """,
            (*_window(days, now), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def session_detail(session_id: str, *, data_dir: str | None = None) -> dict | None:
    with snapshot(data_dir) as conn:
        session = conn.execute(
            """
            SELECT id, title, source AS platform, model, message_count,
                   tool_call_count, cwd, git_branch, profile_name, system_prompt,
                   compression_fallback_streak, compression_failure_error,
                   compression_failure_cooldown_until
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        models = conn.execute(
            """
            SELECT model, COALESCE(task, '') AS task,
                   COALESCE(api_call_count, 0)     AS api_calls,
                   COALESCE(input_tokens, 0)       AS input_tokens,
                   COALESCE(output_tokens, 0)      AS output_tokens,
                   COALESCE(cache_read_tokens, 0)  AS cache_read_tokens,
                   COALESCE(cache_write_tokens, 0) AS cache_write_tokens,
                   COALESCE(estimated_cost_usd, 0) AS cost_usd,
                   first_seen, last_seen
            FROM session_model_usage WHERE session_id = ?
            ORDER BY cost_usd DESC
            """,
            (session_id,),
        ).fetchall()

    detail = dict(session)
    prompt = detail.get("system_prompt") or ""
    detail["system_prompt_chars"] = len(prompt)
    detail["models"] = [dict(m) for m in models]
    return detail
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: 16 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/hermes_usage.py openbrain-gui/backend/tests/test_hermes_usage.py
git commit -m "feat(gui): top-spender sessions and session drill-down"
```

---

## Task 10: Top tools, prompt budget, and config snapshot

**Files:**
- Modify: `openbrain-gui/backend/app/hermes_usage.py`
- Test: `openbrain-gui/backend/tests/test_hermes_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_hermes_usage.py`:

```python
def test_top_tools_counts_only_and_declares_the_limitation(hermes_dir):
    result = hermes_usage.top_tools(hermes_dir, days=30, now=NOW)
    assert result["token_attribution_available"] is False
    counts = {t["tool_name"]: t["calls"] for t in result["tools"]}
    assert counts == {"terminal": 2, "read_file": 1}


def test_prompt_budget_averages_per_platform(hermes_dir):
    rows = {r["platform"]: r for r in hermes_usage.prompt_budget(hermes_dir, days=30, now=NOW)}
    assert rows["whatsapp"]["avg_system_prompt_chars"] == pytest.approx(10000)
    assert rows["cli"]["avg_system_prompt_chars"] == pytest.approx(17000)
    assert rows["cli"]["max_system_prompt_chars"] == 17000


def test_config_snapshot_returns_only_whitelisted_keys(tmp_path):
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(
        "model:\n"
        "  default: claude-sonnet-5\n"
        "compression:\n"
        "  threshold: 0.5\n"
        "tool_output:\n"
        "  max_bytes: 50000\n"
        "prompt_caching:\n"
        "  cache_ttl: 5m\n"
        "secrets:\n"
        "  anthropic_api_key: sk-ant-SHOULD-NEVER-APPEAR\n",
        encoding="utf-8",
    )
    snap = hermes_usage.config_snapshot(str(data_dir))
    assert snap["model.default"] == "claude-sonnet-5"
    assert snap["compression.threshold"] == 0.5
    assert snap["tool_output.max_bytes"] == 50000
    assert snap["prompt_caching.cache_ttl"] == "5m"
    assert "sk-ant-SHOULD-NEVER-APPEAR" not in str(snap)
    assert not any("secret" in k or "key" in k for k in snap)


def test_config_snapshot_missing_file_raises(tmp_path):
    data_dir = tmp_path / "hermes-data"
    data_dir.mkdir()
    with pytest.raises(hermes_usage.HermesDataUnavailable):
        hermes_usage.config_snapshot(str(data_dir))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v -k "tools or prompt_budget or config"
```

Expected: FAIL — `AttributeError: ... has no attribute 'top_tools'`.

- [ ] **Step 3: Write minimal implementation**

Add `pyyaml>=6.0` to `dependencies` in `openbrain-gui/backend/pyproject.toml`, then install it:

```bash
cd openbrain-gui/backend && python -m pip install -e ".[dev]"
```

Append to `openbrain-gui/backend/app/hermes_usage.py`:

```python
import yaml

# Only these keys are ever read out of config.yaml. The file also holds API
# keys and auth material; an explicit whitelist means a future edit to the
# panel cannot accidentally start returning them (design spec section 4.4).
_CONFIG_KEYS = (
    ("model", "default"),
    ("compression", "threshold"),
    ("compression", "threshold_tokens"),
    ("tool_output", "max_bytes"),
    ("prompt_caching", "cache_ttl"),
    ("agent", "max_turns"),
    ("agent", "disabled_toolsets"),
    ("sessions", "auto_prune"),
    ("sessions", "retention_days"),
)


def top_tools(data_dir: str | None = None, *, days: int = 30,
              now: float | None = None, limit: int = 15) -> dict:
    """Call counts by tool. Counts ONLY -- `messages.token_count` is NULL in
    every row of the live database, so tokens cannot be attributed to tools.
    The flag is returned so the UI states this rather than implying precision
    the data does not have (design spec section 5.8)."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT tool_name, COUNT(*) AS calls
            FROM messages
            WHERE tool_name IS NOT NULL AND tool_name != ''
              AND timestamp >= ? AND timestamp <= ?
            GROUP BY tool_name
            ORDER BY calls DESC
            LIMIT ?
            """,
            (*_window(days, now), limit),
        ).fetchall()
    return {
        "tools": [dict(r) for r in rows],
        "token_attribution_available": False,
    }


def prompt_budget(data_dir: str | None = None, *, days: int = 30,
                  now: float | None = None) -> list[dict]:
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT s.source AS platform,
                   COUNT(*)                       AS sessions,
                   AVG(LENGTH(s.system_prompt))   AS avg_system_prompt_chars,
                   MAX(LENGTH(s.system_prompt))   AS max_system_prompt_chars
            FROM sessions s
            WHERE s.system_prompt IS NOT NULL
              AND s.id IN (SELECT session_id FROM session_model_usage
                            WHERE last_seen >= ? AND last_seen <= ?)
            GROUP BY s.source
            ORDER BY avg_system_prompt_chars DESC
            """,
            _window(days, now),
        ).fetchall()
    return [dict(r) for r in rows]


def config_snapshot(data_dir: str | None = None) -> dict:
    path = Path(data_dir or HERMES_DATA_DIR) / "config.yaml"
    if not path.is_file():
        raise HermesDataUnavailable(f"Hermes config.yaml not found at {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise HermesDataUnavailable(f"config.yaml is not valid YAML: {exc}") from exc

    snap: dict = {}
    for section, key in _CONFIG_KEYS:
        value = (loaded.get(section) or {}).get(key) if isinstance(loaded.get(section), dict) else None
        if value is not None:
            snap[f"{section}.{key}"] = value
    return snap
```

Move `import yaml` up with the other imports at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_hermes_usage.py -v
```

Expected: 20 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/hermes_usage.py openbrain-gui/backend/tests/test_hermes_usage.py openbrain-gui/backend/pyproject.toml
git commit -m "feat(gui): top tools, prompt budget, and whitelisted config snapshot"
```

---

## Task 11: Part 1 read routes with 503 degradation

**Files:**
- Modify: `openbrain-gui/backend/app/routes.py`
- Modify: `openbrain-gui/backend/tests/test_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_routes.py`:

```python
HERMES_ENDPOINTS = [
    "/api/cost/summary", "/api/cost/by-model", "/api/cost/by-platform",
    "/api/cost/by-session", "/api/cost/tools", "/api/cost/prompt-budget",
    "/api/cost/efficiency", "/api/cost/config",
]


@pytest.mark.parametrize("endpoint", HERMES_ENDPOINTS)
def test_hermes_endpoints_return_503_when_data_dir_absent(client, monkeypatch, tmp_path, endpoint):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    resp = client.get(endpoint)
    assert resp.status_code == 503
    assert "not found" in resp.json()["detail"]


def test_part2_still_works_when_hermes_data_absent(client, monkeypatch, tmp_path):
    """The whole point of the 503 design: the external cost grid must not
    break because the VPS mount is missing (design spec section 9)."""
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    assert client.get("/api/cost/external").status_code == 200
    assert client.get("/api/cost/fx").status_code == 200


def test_summary_combines_hermes_and_external_costs(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "summary", lambda data_dir=None, *, days=30, now=None: {
        "sessions": 10, "api_calls": 712, "input_tokens": 1_000,
        "output_tokens": 500, "cache_read_tokens": 69_000_000,
        "cache_write_tokens": 8_700_000, "reasoning_tokens": 0,
        "cost_usd": 94.17, "cache_hit_rate": 0.895, "cost_status": "estimated",
    })
    client.put("/api/cost/fx", json={"usd_to_eur": 0.80})
    client.put("/api/cost/external", json={"rows": [
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    body = client.get("/api/cost/summary?days=30").json()
    assert body["hermes"]["cost_usd"] == pytest.approx(94.17)
    assert body["external"]["monthly_usd"] == pytest.approx(12.99)
    assert body["total_cost_of_ownership_usd"] == pytest.approx(107.16)
    assert body["total_cost_of_ownership_eur"] == pytest.approx(85.728)


def test_summary_reports_estimate_vs_actual_when_row_flagged(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "summary", lambda data_dir=None, *, days=30, now=None: {
        "sessions": 1, "api_calls": 1, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "cost_usd": 94.17, "cache_hit_rate": None, "cost_status": "estimated",
    })
    client.put("/api/cost/external", json={"rows": [
        {"name": "Anthropic", "period": "monthly", "amount": 91.40,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": True, "sort_order": 0},
    ]})
    comparison = client.get("/api/cost/summary?days=7").json()["estimate_vs_actual"]
    # Always the 30-day estimate regardless of the selected range
    # (design spec section 6.3).
    assert comparison["estimated_usd"] == pytest.approx(94.17)
    assert comparison["actual_usd"] == pytest.approx(91.40)
    assert comparison["delta_pct"] == pytest.approx((91.40 - 94.17) / 94.17 * 100)


def test_session_detail_404_for_unknown_id(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "session_detail", lambda sid, *, data_dir=None: None)
    assert client.get("/api/cost/session/nope").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k cost
```

Expected: the new tests FAIL with 404 — the routes do not exist.

- [ ] **Step 3: Write minimal implementation**

In `openbrain-gui/backend/app/routes.py`, extend the imports:

```python
from app import external_costs_store, fx, hermes_usage
from app.hermes_usage import HermesDataUnavailable
```

Append the routes:

```python
def _hermes(fn, *args, **kwargs):
    """Every state.db-backed endpoint degrades to 503 rather than 500 when the
    mount is missing -- the external cost grid must stay usable."""
    try:
        return fn(*args, **kwargs)
    except HermesDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.get("/cost/by-model")
def get_cost_by_model(days: int = 30):
    return _hermes(hermes_usage.by_model, days=days)

@router.get("/cost/by-platform")
def get_cost_by_platform(days: int = 30):
    return _hermes(hermes_usage.by_platform, days=days)

@router.get("/cost/by-session")
def get_cost_by_session(days: int = 30, limit: int = 50):
    return _hermes(hermes_usage.by_session, days=days, limit=limit)

@router.get("/cost/session/{session_id}")
def get_cost_session(session_id: str):
    detail = _hermes(hermes_usage.session_detail, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return detail

@router.get("/cost/tools")
def get_cost_tools(days: int = 30):
    return _hermes(hermes_usage.top_tools, days=days)

@router.get("/cost/prompt-budget")
def get_cost_prompt_budget(days: int = 30):
    return _hermes(hermes_usage.prompt_budget, days=days)

@router.get("/cost/efficiency")
def get_cost_efficiency(days: int = 30):
    return _hermes(hermes_usage.efficiency, days=days)

@router.get("/cost/config")
def get_cost_config():
    return _hermes(hermes_usage.config_snapshot)

@router.get("/cost/summary")
def get_cost_summary(days: int = 30):
    hermes = _hermes(hermes_usage.summary, days=days)
    rate_row = fx.get_rate()
    rate = rate_row["usd_to_eur"] if rate_row else None
    rows = external_costs_store.list_rows()
    external = external_costs_store.totals(rows, rate)

    total_usd = hermes["cost_usd"] + external["monthly_usd"]
    # `external["incomplete"]` is True when a EUR-entered row could not be
    # converted because no FX rate has been fetched yet. That row contributes 0
    # to external["monthly_usd"], so the combined total is understated too --
    # propagate the flag rather than let the UI show a confident wrong number.
    total_incomplete = external["incomplete"]

    comparison = None
    flagged = external_costs_store.flagged_row()
    if flagged:
        # An invoice covers a billing month, so this always compares against the
        # 30-day figure regardless of the selected range (design spec 6.3).
        baseline = _hermes(hermes_usage.summary, days=30)["cost_usd"]
        actual = external_costs_store.amounts(flagged, rate)["usd"]
        if actual is not None and baseline:
            comparison = {
                "name": flagged["name"],
                "estimated_usd": baseline,
                "actual_usd": actual,
                "delta_pct": (actual - baseline) / baseline * 100,
            }

    return {
        "days": days,
        "hermes": hermes,
        "external": external,
        "rate": rate_row,
        "total_cost_of_ownership_usd": total_usd,
        "total_cost_of_ownership_eur": total_usd * rate if rate else None,
        "total_cost_of_ownership_incomplete": total_incomplete,
        "estimate_vs_actual": comparison,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui): cost dashboard read endpoints with 503 degradation"
```

---

## Task 12: Dashboard panels in the frontend

**Files:**
- Create: `openbrain-gui/frontend/src/CostSummary.jsx`
- Create: `openbrain-gui/frontend/src/CostTables.jsx`
- Create: `openbrain-gui/frontend/src/SessionDetail.jsx`
- Create: `openbrain-gui/frontend/src/CostConfig.jsx`
- Modify: `openbrain-gui/frontend/src/CostView.jsx`
- Modify: `openbrain-gui/frontend/src/api.js`
- Modify: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Add the API client methods**

In `openbrain-gui/frontend/src/api.js`, add to the `api` object:

```js
  getCostSummary: (days) => request(`/cost/summary?days=${days}`),
  getCostByModel: (days) => request(`/cost/by-model?days=${days}`),
  getCostByPlatform: (days) => request(`/cost/by-platform?days=${days}`),
  getCostBySession: (days, limit = 50) => request(`/cost/by-session?days=${days}&limit=${limit}`),
  getCostSession: (id) => request(`/cost/session/${encodeURIComponent(id)}`),
  getCostTools: (days) => request(`/cost/tools?days=${days}`),
  getCostPromptBudget: (days) => request(`/cost/prompt-budget?days=${days}`),
  getCostEfficiency: (days) => request(`/cost/efficiency?days=${days}`),
  getCostConfig: () => request('/cost/config'),
```

- [ ] **Step 2: Create a shared formatting helper**

Append to `openbrain-gui/frontend/src/format.js`:

```js
export function usd(value) {
  return value == null ? '—' : `$${Number(value).toFixed(2)}`
}

export function tokens(value) {
  if (value == null) return '—'
  const n = Number(value)
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

export function pct(value) {
  return value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`
}
```

- [ ] **Step 3: Create the header tiles**

Create `openbrain-gui/frontend/src/CostSummary.jsx`:

```jsx
import { usd, tokens, pct } from './format'

export default function CostSummary({ summary, unavailable }) {
  if (unavailable) {
    return <p className="cost-unavailable">Hermes data is not mounted — {unavailable}</p>
  }
  if (!summary) return <p className="cost-loading">Loading…</p>

  const h = summary.hermes
  const cmp = summary.estimate_vs_actual

  return (
    <div className="cost-tiles">
      <div className="cost-tile">
        <span className="cost-tile-label">Total cost of ownership</span>
        {/* Understated while a euro row has no rate -- mark it rather than
            present a confident wrong figure. */}
        <strong>
          {usd(summary.total_cost_of_ownership_usd)}
          {summary.total_cost_of_ownership_incomplete && <span className="cost-warning">*</span>}
        </strong>
        <span className="cost-tile-sub">
          {summary.total_cost_of_ownership_incomplete
            ? 'incomplete — a euro row needs an exchange rate'
            : (summary.total_cost_of_ownership_eur != null
                ? `€${summary.total_cost_of_ownership_eur.toFixed(2)}`
                : 'no rate')}
          {summary.external.onetime_usd > 0 &&
            ` · one-off ${usd(summary.external.onetime_usd)}`}
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">Hermes API cost <em>estimated</em></span>
        <strong>{usd(h.cost_usd)}</strong>
        <span className="cost-tile-sub">
          {cmp
            ? `invoice ${usd(cmp.actual_usd)} (${cmp.delta_pct >= 0 ? '+' : ''}${cmp.delta_pct.toFixed(1)}%, 30d)`
            : `${summary.days} days`}
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">API calls / tokens</span>
        <strong>{h.api_calls}</strong>
        <span className="cost-tile-sub">
          {tokens(h.cache_read_tokens + h.cache_write_tokens + h.input_tokens + h.output_tokens)} total
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">Cache hit rate</span>
        <strong>{pct(h.cache_hit_rate)}</strong>
        <span className="cost-tile-sub">
          {tokens(h.cache_write_tokens)} written · a write costs 12.5× a read
        </span>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create the breakdown tables**

Create `openbrain-gui/frontend/src/CostTables.jsx`:

```jsx
import { usd, tokens } from './format'

function Table({ title, rows, labelKey, labelHeader, onRowClick }) {
  const total = rows.reduce((sum, r) => sum + (r.cost_usd || 0), 0)
  return (
    <section className="cost-table-block">
      <h4>{title}</h4>
      <table className="cost-table">
        <thead>
          <tr>
            <th>{labelHeader}</th><th>Sessions</th><th>Calls</th>
            <th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th>
            <th>Cost</th><th>%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[labelKey] ?? r.session_id}
                className={onRowClick ? 'clickable' : ''}
                onClick={onRowClick ? () => onRowClick(r) : undefined}>
              <td>{r[labelKey] || '(none)'}</td>
              <td>{r.sessions}</td>
              <td>{r.api_calls}</td>
              <td>{tokens(r.input_tokens)}</td>
              <td>{tokens(r.output_tokens)}</td>
              <td>{tokens(r.cache_read_tokens)}</td>
              <td>{tokens(r.cache_write_tokens)}</td>
              <td>{usd(r.cost_usd)}</td>
              <td>{total ? `${((r.cost_usd / total) * 100).toFixed(0)}%` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export default function CostTables({ byModel, byPlatform, bySession, onSelectSession }) {
  return (
    <div className="cost-tables">
      <Table title="By model" rows={byModel || []} labelKey="model" labelHeader="Model" />
      <Table title="By platform" rows={byPlatform || []} labelKey="platform" labelHeader="Platform" />
      <Table title="Top spenders" rows={bySession || []} labelKey="title" labelHeader="Session"
             onRowClick={(r) => onSelectSession(r.session_id)} />
    </div>
  )
}
```

- [ ] **Step 5: Create the drill-down pane**

Create `openbrain-gui/frontend/src/SessionDetail.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { api } from './api'
import { usd, tokens } from './format'

export default function SessionDetail({ sessionId, onClose }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [showPrompt, setShowPrompt] = useState(false)

  useEffect(() => {
    setDetail(null); setError(null); setShowPrompt(false)
    api.getCostSession(sessionId).then(setDetail).catch((e) => setError(e.message))
  }, [sessionId])

  return (
    <div className="session-detail">
      <div className="session-detail-header">
        <h4>{detail?.title || sessionId}</h4>
        <button onClick={onClose}>Close</button>
      </div>

      {error && <p className="cost-unavailable">{error}</p>}
      {!detail && !error && <p className="cost-loading">Loading…</p>}

      {detail && (
        <>
          <dl className="session-detail-meta">
            <dt>Platform</dt><dd>{detail.platform}</dd>
            <dt>Messages</dt><dd>{detail.message_count}</dd>
            <dt>Tool calls</dt><dd>{detail.tool_call_count}</dd>
            <dt>cwd</dt><dd>{detail.cwd || '—'}</dd>
            <dt>Branch</dt><dd>{detail.git_branch || '—'}</dd>
            <dt>Profile</dt><dd>{detail.profile_name || '—'}</dd>
            <dt>Compression fallbacks</dt><dd>{detail.compression_fallback_streak ?? 0}</dd>
            <dt>Compression error</dt><dd>{detail.compression_failure_error || 'none'}</dd>
            <dt>System prompt</dt>
            <dd>
              {detail.system_prompt_chars.toLocaleString()} chars{' '}
              <button className="link-button" onClick={() => setShowPrompt((v) => !v)}>
                {showPrompt ? 'hide' : 'show'}
              </button>
            </dd>
          </dl>

          {showPrompt && <pre className="session-prompt">{detail.system_prompt}</pre>}

          <table className="cost-table">
            <thead>
              <tr>
                <th>Model</th><th>Task</th><th>Calls</th><th>In</th><th>Out</th>
                <th>Cache read</th><th>Cache write</th><th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {detail.models.map((m) => (
                <tr key={`${m.model}-${m.task}`}>
                  <td>{m.model}</td>
                  <td>{m.task || '—'}</td>
                  <td>{m.api_calls}</td>
                  <td>{tokens(m.input_tokens)}</td>
                  <td>{tokens(m.output_tokens)}</td>
                  <td>{tokens(m.cache_read_tokens)}</td>
                  <td>{tokens(m.cache_write_tokens)}</td>
                  <td>{usd(m.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 5b: Create the token composition bar and efficiency panel**

Create `openbrain-gui/frontend/src/CostEfficiency.jsx` (design spec §5.5 and §5.6):

```jsx
import { usd, tokens } from './format'

const SEGMENTS = [
  { key: 'cache_read_tokens', label: 'cache read', color: '#76b7b2' },
  { key: 'cache_write_tokens', label: 'cache write', color: '#e15759' },
  { key: 'input_tokens', label: 'input', color: '#4e79a7' },
  { key: 'output_tokens', label: 'output', color: '#f28e2b' },
]

function Composition({ hermes }) {
  const total = SEGMENTS.reduce((sum, s) => sum + (hermes[s.key] || 0), 0)
  if (!total) return <p className="cost-note">No token activity in this window.</p>

  let offset = 0
  return (
    <>
      <svg viewBox="0 0 100 6" className="composition-bar" preserveAspectRatio="none"
           role="img" aria-label="Token composition">
        {SEGMENTS.map((s) => {
          const width = ((hermes[s.key] || 0) / total) * 100
          const x = offset
          offset += width
          return (
            <rect key={s.key} x={x} y={0} width={width} height={6} fill={s.color}>
              <title>{`${s.label}: ${tokens(hermes[s.key])} (${width.toFixed(1)}%)`}</title>
            </rect>
          )
        })}
      </svg>
      <div className="cost-chart-legend">
        {SEGMENTS.map((s) => (
          <span key={s.key}>
            <i style={{ background: s.color }} /> {s.label} {tokens(hermes[s.key])}
          </span>
        ))}
      </div>
      <p className="cost-note">
        A cache <strong>write</strong> costs 12.5× a cache read — the red segment is where the
        money goes, not the green one.
      </p>
    </>
  )
}

export default function CostEfficiency({ hermes, efficiency }) {
  return (
    <div className="cost-efficiency">
      <section className="cost-table-block">
        <h4>Token composition</h4>
        {hermes ? <Composition hermes={hermes} /> : <p className="cost-loading">Loading…</p>}
      </section>

      <section className="cost-table-block">
        <h4>Efficiency</h4>
        <table className="cost-table">
          <thead>
            <tr>
              <th>Platform</th><th>Calls</th><th>Tokens / call</th>
              <th>Cache write / call</th><th>Cost / call</th><th>Msgs / session</th>
            </tr>
          </thead>
          <tbody>
            {(efficiency || []).map((r) => (
              <tr key={r.platform}>
                <td>{r.platform || '(none)'}</td>
                <td>{r.api_calls}</td>
                <td>{tokens(r.tokens_per_call)}</td>
                <td>{tokens(r.cache_write_per_call)}</td>
                <td>{usd(r.cost_per_call)}</td>
                <td>{Math.round(r.avg_messages_per_session ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
```

- [ ] **Step 6: Create the tools/prompt/config panel**

Create `openbrain-gui/frontend/src/CostConfig.jsx`:

```jsx
const CONFIG_NOTES = {
  'model.default': 'Opus costs roughly 2.5× Sonnet per token.',
  'compression.threshold': 'Fraction of the model context window before compaction runs.',
  'compression.threshold_tokens': 'Absolute token cap before compaction. Unset means only the fraction applies.',
  'tool_output.max_bytes': 'Cap on one tool result. 50,000 bytes is roughly 12k tokens.',
  'prompt_caching.cache_ttl': '5m writes cost 1.25× base; 1h writes cost 2×.',
  'agent.max_turns': 'Upper bound on tool-calling turns per task.',
  'agent.disabled_toolsets': 'Every enabled tool ships its JSON schema in each request prefix.',
  'sessions.auto_prune': 'Old sessions are deleted from state.db when on.',
  'sessions.retention_days': 'How long session history survives pruning.',
}

export default function CostConfig({ tools, promptBudget, config }) {
  return (
    <div className="cost-config">
      <section className="cost-table-block">
        <h4>Top tools</h4>
        {tools?.token_attribution_available === false && (
          <p className="cost-note">
            Call counts only — Hermes stores no per-message token counts, so tokens cannot be
            attributed to individual tools.
          </p>
        )}
        <table className="cost-table">
          <thead><tr><th>Tool</th><th>Calls</th></tr></thead>
          <tbody>
            {(tools?.tools || []).map((t) => (
              <tr key={t.tool_name}><td>{t.tool_name}</td><td>{t.calls}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="cost-table-block">
        <h4>Prompt budget</h4>
        <table className="cost-table">
          <thead>
            <tr><th>Platform</th><th>Sessions</th><th>Avg prompt</th><th>Largest</th></tr>
          </thead>
          <tbody>
            {(promptBudget || []).map((p) => (
              <tr key={p.platform}>
                <td>{p.platform}</td>
                <td>{p.sessions}</td>
                <td>{Math.round(p.avg_system_prompt_chars).toLocaleString()} chars</td>
                <td>{p.max_system_prompt_chars?.toLocaleString()} chars</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="cost-table-block">
        <h4>Config</h4>
        <table className="cost-table">
          <thead><tr><th>Key</th><th>Value</th><th>What it costs</th></tr></thead>
          <tbody>
            {Object.entries(config || {}).map(([key, value]) => (
              <tr key={key}>
                <td><code>{key}</code></td>
                <td>{JSON.stringify(value)}</td>
                <td className="cost-note">{CONFIG_NOTES[key] || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
```

- [ ] **Step 7: Wire everything into CostView**

Replace the contents of `openbrain-gui/frontend/src/CostView.jsx`:

```jsx
import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import CostSummary from './CostSummary'
import CostTables from './CostTables'
import CostEfficiency from './CostEfficiency'
import SessionDetail from './SessionDetail'
import CostConfig from './CostConfig'
import ExternalCostGrid from './ExternalCostGrid'

const RANGES = [7, 30, 90]

export default function CostView() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState({})
  const [unavailable, setUnavailable] = useState(null)
  const [selectedSession, setSelectedSession] = useState(null)

  const load = useCallback(async () => {
    setUnavailable(null)
    try {
      const [summary, byModel, byPlatform, bySession, tools, promptBudget, efficiency, config] =
        await Promise.all([
          api.getCostSummary(days), api.getCostByModel(days), api.getCostByPlatform(days),
          api.getCostBySession(days), api.getCostTools(days),
          api.getCostPromptBudget(days), api.getCostEfficiency(days), api.getCostConfig(),
        ])
      setData({ summary, byModel, byPlatform, bySession, tools, promptBudget, efficiency, config })
    } catch (e) {
      // 503 = the mount is missing. Part 2 below still renders.
      setUnavailable(e.message)
      setData({})
    }
  }, [days])

  useEffect(() => { load() }, [load])

  return (
    <div className="cost-view">
      <div className="cost-range">
        {RANGES.map((d) => (
          <button key={d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>
            {d} days
          </button>
        ))}
      </div>

      <CostSummary summary={data.summary} unavailable={unavailable} />

      {!unavailable && (
        <>
          <CostTables
            byModel={data.byModel}
            byPlatform={data.byPlatform}
            bySession={data.bySession}
            onSelectSession={setSelectedSession}
          />
          {selectedSession && (
            <SessionDetail sessionId={selectedSession} onClose={() => setSelectedSession(null)} />
          )}
          <CostEfficiency hermes={data.summary?.hermes} efficiency={data.efficiency} />
          <CostConfig tools={data.tools} promptBudget={data.promptBudget} config={data.config} />
        </>
      )}

      <ExternalCostGrid />
    </div>
  )
}
```

- [ ] **Step 8: Add styles**

Append to `openbrain-gui/frontend/src/index.css`:

```css
.cost-tiles { display: flex; flex-wrap: wrap; gap: 1rem; }
.cost-tile { display: flex; flex-direction: column; gap: 0.15rem; min-width: 12rem;
             padding: 0.6rem 0.9rem; border: 1px solid rgba(127,127,127,0.35); border-radius: 6px; }
.cost-tile strong { font-size: 1.5rem; }
.cost-tile-label { font-size: 0.8em; opacity: 0.75; }
.cost-tile-label em { font-style: normal; opacity: 0.7; font-size: 0.85em; }
.cost-tile-sub { font-size: 0.8em; opacity: 0.7; }

.cost-tables, .cost-config, .cost-efficiency { display: flex; flex-direction: column; gap: 1.25rem; }
.composition-bar { width: 100%; height: 1.5rem; border-radius: 3px; }
.cost-table-block h4 { margin: 0 0 0.35rem; }
.cost-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.cost-table th, .cost-table td { padding: 0.2rem 0.45rem; text-align: right; }
.cost-table th:first-child, .cost-table td:first-child { text-align: left; }
.cost-table tr.clickable { cursor: pointer; }
.cost-table tr.clickable:hover { background: rgba(127,127,127,0.12); }
.cost-note { opacity: 0.7; font-size: 0.85em; text-align: left; }

.session-detail { border: 1px solid rgba(127,127,127,0.35); border-radius: 6px; padding: 0.75rem; }
.session-detail-header { display: flex; justify-content: space-between; align-items: center; }
.session-detail-meta { display: grid; grid-template-columns: max-content 1fr;
                       gap: 0.15rem 1rem; margin: 0.5rem 0; }
.session-detail-meta dt { opacity: 0.7; }
.session-detail-meta dd { margin: 0; }
.session-prompt { max-height: 20rem; overflow: auto; white-space: pre-wrap;
                  font-size: 0.8em; background: rgba(127,127,127,0.1); padding: 0.5rem; }
.link-button { background: none; border: none; padding: 0; text-decoration: underline; cursor: pointer; }
.cost-unavailable { color: #c0392b; }
.cost-loading { opacity: 0.7; }
```

- [ ] **Step 9: Build and verify in the browser**

```bash
cd openbrain-gui/frontend && npm run build
```

Expected: build succeeds.

With the backend running **without** `/hermes-data` present, open the Cost page and verify:

1. A red "Hermes data is not mounted" line appears where the tiles would be.
2. The external cost grid below still loads, saves, and deletes normally.

Then point the backend at a copy of a real `state.db`:

```bash
cd openbrain-gui/backend && HERMES_DATA_DIR=/path/to/copied/hermes-data python -m uvicorn app.main:app --port 8000
```

Verify:

3. Four tiles populate with real numbers.
4. `By model`, `By platform` and `Top spenders` tables render, sorted by cost descending.
5. Clicking a `Top spenders` row opens the drill-down with metadata and the per-model table.
6. `show` next to System prompt reveals the prompt text in a scrollable block.
7. The `Token composition` bar renders four segments; hovering each shows its label, token count, and percentage.
8. The `Efficiency` table shows a visibly larger `Cache write / call` for whatsapp than for cli.
9. The `Top tools` panel shows the "call counts only" note.
10. The `Config` table shows no key containing `key` or `secret`.
11. Switching 7 / 30 / 90 days changes the numbers.

- [ ] **Step 10: Commit**

```bash
git add openbrain-gui/frontend/src/CostSummary.jsx openbrain-gui/frontend/src/CostTables.jsx openbrain-gui/frontend/src/CostEfficiency.jsx openbrain-gui/frontend/src/SessionDetail.jsx openbrain-gui/frontend/src/CostConfig.jsx openbrain-gui/frontend/src/CostView.jsx openbrain-gui/frontend/src/api.js openbrain-gui/frontend/src/format.js openbrain-gui/frontend/src/index.css
git commit -m "feat(gui): cost dashboard panels, composition bar, efficiency, and drill-down"
```

---

## Task 13: Ledger tick — seed and deltas

**Files:**
- Create: `openbrain-gui/backend/app/ledger_store.py`
- Test: `openbrain-gui/backend/tests/test_ledger_store.py`

- [ ] **Step 1: Write the failing test**

Create `openbrain-gui/backend/tests/test_ledger_store.py`:

```python
# tests/test_ledger_store.py
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
    """Without this rule day one records a fabricated spike representing all
    of Hermes' prior history (design spec section 4.3)."""
    db_path = _db(tmp_path)
    result = ledger_store.apply_tick([_row()], path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    assert result == {"seeded": True, "rows_written": 0}
    with get_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM usage_watermark").fetchone()[0] == 1


def test_second_tick_writes_only_the_difference(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row(api_call_count=13, cache_read_tokens=1600, estimated_cost_usd=2.0)],
        path=db_path, observed_at="2026-08-01T00:05:00+00:00",
    )
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM usage_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["d_api_calls"] == 3
    assert rows[0]["d_cache_read"] == 600
    assert rows[0]["d_cost_usd"] == pytest.approx(0.5)
    assert rows[0]["observed_at"] == "2026-08-01T00:05:00+00:00"


def test_unchanged_rows_write_nothing(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    result = ledger_store.apply_tick([_row()], path=db_path, observed_at="2026-08-01T00:05:00+00:00")
    assert result["rows_written"] == 0
    with get_conn(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 0


def test_new_session_after_seeding_counts_in_full(tmp_path):
    """Only the FIRST tick is suppressed. A session that appears later is
    genuinely new, so all of its tokens are new."""
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row("s1")], path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row("s1"), _row("s2")], path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT session_id, d_api_calls FROM usage_ledger").fetchall()
    assert [(r["session_id"], r["d_api_calls"]) for r in rows] == [("s2", 10)]


def test_decreasing_counter_clamps_to_zero(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row(api_call_count=10)], path=db_path,
                            observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=4, cache_read_tokens=1500)], path=db_path,
                            observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT d_api_calls, d_cache_read FROM usage_ledger").fetchone()
    assert row["d_api_calls"] == 0
    assert row["d_cache_read"] == 500


def test_same_session_different_models_tracked_separately(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick(
        [_row("s1", "claude-sonnet-5"), _row("s1", "claude-opus-4-8")],
        path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row("s1", "claude-sonnet-5", api_call_count=12),
         _row("s1", "claude-opus-4-8")],
        path=db_path, observed_at="2026-08-01T00:05:00+00:00")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT model, d_api_calls FROM usage_ledger").fetchall()
    assert [(r["model"], r["d_api_calls"]) for r in rows] == [("claude-sonnet-5", 2)]


def test_timeseries_buckets_by_day_and_group(tmp_path):
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row()], path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=12, estimated_cost_usd=2.0)],
                            path=db_path, observed_at="2026-08-01T10:00:00+00:00")
    ledger_store.apply_tick([_row(api_call_count=20, estimated_cost_usd=5.0)],
                            path=db_path, observed_at="2026-08-02T10:00:00+00:00")
    series = ledger_store.timeseries(path=db_path, days=30, group="model",
                                     now_iso="2026-08-03T00:00:00+00:00")
    assert series["collecting_since"] == "2026-08-01"
    points = {(p["day"], p["group"]): p["cost_usd"] for p in series["points"]}
    assert points[("2026-08-01", "claude-sonnet-5")] == pytest.approx(0.5)
    assert points[("2026-08-02", "claude-sonnet-5")] == pytest.approx(3.0)


def test_timeseries_can_group_by_platform(tmp_path):
    """Platform is stored on the ledger row, so this works with no state.db."""
    db_path = _db(tmp_path)
    ledger_store.apply_tick([_row("s1", platform="whatsapp"), _row("s2", platform="cli")],
                            path=db_path, observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [_row("s1", platform="whatsapp", estimated_cost_usd=4.5),
         _row("s2", platform="cli", estimated_cost_usd=2.5)],
        path=db_path, observed_at="2026-08-01T06:00:00+00:00")
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_ledger_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.ledger_store'`.

- [ ] **Step 3: Write minimal implementation**

Create `openbrain-gui/backend/app/ledger_store.py`:

```python
# app/ledger_store.py
"""Builds a real time series out of Hermes' lifetime-only counters.

`session_model_usage` holds ONE row per (session, model, task) covering that
session's whole life, so a live read cannot say what was spent on a given day.
This module samples: each tick diffs the current counters against a stored
watermark and appends only what changed, stamped with a real observation time.

The FIRST tick seeds watermarks and writes nothing. Without that rule, day one
records a fabricated spike representing all of Hermes' prior history."""
from datetime import datetime, timezone

from app.db import get_conn

_COUNTERS = (
    ("api_call_count", "d_api_calls"),
    ("input_tokens", "d_input"),
    ("output_tokens", "d_output"),
    ("cache_read_tokens", "d_cache_read"),
    ("cache_write_tokens", "d_cache_write"),
    ("reasoning_tokens", "d_reasoning"),
    ("estimated_cost_usd", "d_cost_usd"),
)

_KEY = ("session_id", "model", "task")


def apply_tick(rows: list[dict], *, path: str | None = None,
               observed_at: str | None = None) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        marks = {
            (r["session_id"], r["model"], r["task"]): dict(r)
            for r in conn.execute("SELECT * FROM usage_watermark")
        }
        seeding = not marks
        written = 0

        for row in rows:
            key = (row["session_id"], row["model"], row.get("task") or "")
            previous = marks.get(key)

            if not seeding:
                deltas = {}
                for source, target in _COUNTERS:
                    current = row.get(source) or 0
                    before = (previous or {}).get(source) or 0
                    # A counter that went backwards means the session was reset
                    # or replaced. Clamp rather than record negative spend.
                    deltas[target] = max(current - before, 0)
                if any(deltas.values()):
                    conn.execute(
                        f"""
                        INSERT INTO usage_ledger
                            (observed_at, session_id, model, task, platform,
                             {", ".join(t for _, t in _COUNTERS)})
                        VALUES (?, ?, ?, ?, ?, {", ".join("?" * len(_COUNTERS))})
                        """,
                        (observed_at, *key, row.get("platform") or "",
                         *(deltas[t] for _, t in _COUNTERS)),
                    )
                    written += 1

            conn.execute(
                f"""
                INSERT INTO usage_watermark
                    ({", ".join(_KEY)}, {", ".join(s for s, _ in _COUNTERS)})
                VALUES ({", ".join("?" * (len(_KEY) + len(_COUNTERS)))})
                ON CONFLICT(session_id, model, task) DO UPDATE SET
                    {", ".join(f"{s} = excluded.{s}" for s, _ in _COUNTERS)}
                """,
                (*key, *(row.get(s) or 0 for s, _ in _COUNTERS)),
            )
        conn.commit()
    return {"seeded": seeding, "rows_written": written}


def timeseries(*, path: str | None = None, days: int = 30,
               group: str = "model", now_iso: str | None = None) -> dict:
    if group not in ("model", "platform"):
        raise ValueError("group must be 'model' or 'platform'")
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
    cutoff = (now.timestamp() - days * 86400.0)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

    # `group` is validated against a two-item tuple immediately above, so
    # interpolating it into the SQL cannot inject anything.
    with get_conn(path) as conn:
        rows = conn.execute(
            f"""
            SELECT SUBSTR(observed_at, 1, 10) AS day, {group} AS grp,
                   SUM(d_api_calls)   AS api_calls,
                   SUM(d_input)       AS input_tokens,
                   SUM(d_output)      AS output_tokens,
                   SUM(d_cache_read)  AS cache_read_tokens,
                   SUM(d_cache_write) AS cache_write_tokens,
                   SUM(d_cost_usd)    AS cost_usd
            FROM usage_ledger
            WHERE observed_at >= ?
            GROUP BY day, grp
            ORDER BY day ASC
            """,
            (cutoff_iso,),
        ).fetchall()
        first = conn.execute("SELECT MIN(observed_at) AS first FROM usage_ledger").fetchone()

    points = [
        {"day": r["day"], "group": r["grp"], "cost_usd": r["cost_usd"],
         "api_calls": r["api_calls"], "cache_read_tokens": r["cache_read_tokens"],
         "cache_write_tokens": r["cache_write_tokens"]}
        for r in rows
    ]
    return {
        "points": points,
        "collecting_since": first["first"][:10] if first and first["first"] else None,
        "group": group,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_ledger_store.py -v
```

Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/ledger_store.py openbrain-gui/backend/tests/test_ledger_store.py
git commit -m "feat(gui): usage delta ledger with first-tick seeding"
```

---

## Task 14: Wire the poller into the app lifespan

**Files:**
- Modify: `openbrain-gui/backend/app/main.py`
- Test: `openbrain-gui/backend/tests/test_ledger_store.py`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_ledger_store.py`:

```python
def test_run_once_reads_hermes_and_applies_tick(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "read_usage_rows", lambda data_dir=None: [_row()])
    result = ledger_store.run_once(path=db_path)
    assert result["seeded"] is True


def test_run_once_swallows_missing_hermes_data(tmp_path, monkeypatch):
    """The poller must never crash the app because the mount is absent."""
    db_path = _db(tmp_path)
    import app.hermes_usage as hu

    def boom(data_dir=None):
        raise hu.HermesDataUnavailable("not mounted")

    monkeypatch.setattr(hu, "read_usage_rows", boom)
    assert ledger_store.run_once(path=db_path) == {"skipped": "not mounted"}


def test_run_once_swallows_unexpected_errors(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    import app.hermes_usage as hu

    def boom(data_dir=None):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(hu, "read_usage_rows", boom)
    result = ledger_store.run_once(path=db_path)
    assert "skipped" in result
```

Add `import sqlite3` to the top of `tests/test_ledger_store.py`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_ledger_store.py -v -k run_once
```

Expected: FAIL — `AttributeError: ... has no attribute 'run_once'`.

- [ ] **Step 3: Write minimal implementation**

Append to `openbrain-gui/backend/app/ledger_store.py`:

```python
import logging

from app import hermes_usage

_log = logging.getLogger(__name__)


def run_once(*, path: str | None = None, data_dir: str | None = None) -> dict:
    """One poll cycle. Never raises: a torn snapshot copy or an absent mount is
    an expected condition, and the poller must not take the app down with it."""
    try:
        rows = hermes_usage.read_usage_rows(data_dir)
    except hermes_usage.HermesDataUnavailable as exc:
        _log.info("ledger tick skipped: %s", exc)
        return {"skipped": str(exc)}
    except Exception as exc:
        _log.warning("ledger tick failed reading state.db: %s", exc)
        return {"skipped": str(exc)}
    try:
        return apply_tick(rows, path=path)
    except Exception as exc:
        _log.warning("ledger tick failed writing gui.db: %s", exc)
        return {"skipped": str(exc)}
```

Move `import logging` and `from app import hermes_usage` to the top of the file
with the other imports.

Replace `openbrain-gui/backend/app/main.py` with:

```python
# app/main.py
"""FastAPI app entrypoint: mounts /api routes, runs the usage-ledger poller,
and serves the built React frontend as static files (same-origin, single
container -- design spec section 4, "Architecture")."""
import asyncio
import contextlib
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import LEDGER_POLL_SECONDS
from app.db import init_db
from app import ledger_store
from app.routes import router as api_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_log = logging.getLogger(__name__)


async def _poll_forever() -> None:
    while True:
        # sqlite3 is blocking; keep it off the event loop.
        await asyncio.to_thread(ledger_store.run_once)
        await asyncio.sleep(LEDGER_POLL_SECONDS)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_poll_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="openbrain-gui-backend", lifespan=_lifespan)
    app.include_router(api_router)

    @app.get("/health")
    def health():
        return {"ok": True}

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
    return app


app = create_app()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: all PASS. The route tests use `TestClient` as a context-managed
fixture only where needed; the poller starts on lifespan and is cancelled on
teardown, and `run_once` swallows the missing mount, so no test hangs.

If `test_routes.py` tests begin emitting "Task was destroyed" warnings, change
the `client` fixture to yield inside a `with` block:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "GUI_DB_PATH", str(tmp_path / "gui.db"))
    with TestClient(create_app()) as test_client:
        yield test_client
```

- [ ] **Step 5: Commit**

```bash
git add openbrain-gui/backend/app/main.py openbrain-gui/backend/app/ledger_store.py openbrain-gui/backend/tests/test_ledger_store.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui): run the usage ledger poller on the app lifespan"
```

---

## Task 15: Timeseries route and the spend chart

**Files:**
- Modify: `openbrain-gui/backend/app/routes.py`
- Modify: `openbrain-gui/backend/tests/test_routes.py`
- Create: `openbrain-gui/frontend/src/CostChart.jsx`
- Modify: `openbrain-gui/frontend/src/CostView.jsx`
- Modify: `openbrain-gui/frontend/src/api.js`
- Modify: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Write the failing test**

Append to `openbrain-gui/backend/tests/test_routes.py`:

```python
def test_timeseries_endpoint_returns_points_and_collecting_since(client):
    from app import ledger_store
    ledger_store.apply_tick(
        [{"session_id": "s1", "model": "claude-sonnet-5", "task": "",
          "platform": "cli",
          "api_call_count": 1, "input_tokens": 1, "output_tokens": 1,
          "cache_read_tokens": 1, "cache_write_tokens": 1,
          "reasoning_tokens": 0, "estimated_cost_usd": 1.0}],
        observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick(
        [{"session_id": "s1", "model": "claude-sonnet-5", "task": "",
          "platform": "cli",
          "api_call_count": 5, "input_tokens": 1, "output_tokens": 1,
          "cache_read_tokens": 1, "cache_write_tokens": 1,
          "reasoning_tokens": 0, "estimated_cost_usd": 3.0}],
        observed_at="2026-08-01T06:00:00+00:00")
    body = client.get("/api/cost/timeseries?days=3650&group=model").json()
    assert body["collecting_since"] == "2026-08-01"
    assert body["points"][0]["group"] == "claude-sonnet-5"
    assert body["points"][0]["cost_usd"] == pytest.approx(2.0)


def test_timeseries_rejects_bad_group(client):
    assert client.get("/api/cost/timeseries?group=banana").status_code == 400


def test_timeseries_works_without_hermes_data(client, monkeypatch, tmp_path):
    """The ledger lives in gui.db, so this endpoint never needs the mount."""
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    assert client.get("/api/cost/timeseries?days=30&group=model").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k timeseries
```

Expected: FAIL with 404.

- [ ] **Step 3: Write minimal implementation**

In `openbrain-gui/backend/app/routes.py`, add `ledger_store` to the imports:

```python
from app import external_costs_store, fx, hermes_usage, ledger_store
```

Append the route. Note the annotation is `str`, **not** `Literal` — a `Literal`
would make FastAPI reject a bad value with 422 and its own error shape, whereas
every other `/api/cost/*` validation failure is a 400 carrying a plain `detail`
string. Validating in `ledger_store` keeps the two consistent:

```python
@router.get("/cost/timeseries")
def get_cost_timeseries(days: int = 30, group: str = "model"):
    try:
        return ledger_store.timeseries(days=days, group=group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: Create the chart component**

Create `openbrain-gui/frontend/src/CostChart.jsx`:

```jsx
import { usd } from './format'

const PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#b07aa1']
const WIDTH = 720
const HEIGHT = 220
const PAD = { top: 10, right: 10, bottom: 28, left: 48 }

export default function CostChart({ series, group, onGroupChange }) {
  if (!series) return <p className="cost-loading">Loading…</p>

  const days = [...new Set(series.points.map((p) => p.day))].sort()
  const groups = [...new Set(series.points.map((p) => p.group))].sort()

  if (!days.length) {
    return (
      <section className="cost-table-block">
        <h4>Spend over time</h4>
        <p className="cost-note">
          {series.collecting_since
            ? `Collecting since ${series.collecting_since} — no completed intervals yet.`
            : 'Collecting from now — the chart fills in as the poller records activity.'}
        </p>
      </section>
    )
  }

  const byDay = new Map(days.map((d) => [d, new Map()]))
  for (const p of series.points) byDay.get(p.day).set(p.group, p.cost_usd || 0)

  const dayTotals = days.map((d) => [...byDay.get(d).values()].reduce((a, b) => a + b, 0))
  const maxTotal = Math.max(...dayTotals, 0.01)

  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const barW = Math.max(2, (plotW / days.length) * 0.7)

  return (
    <section className="cost-table-block">
      <div className="cost-chart-header">
        <h4>Spend over time</h4>
        <div className="cost-chart-toggle">
          {['model', 'platform'].map((g) => (
            <button key={g} className={group === g ? 'active' : ''} onClick={() => onGroupChange(g)}>
              by {g}
            </button>
          ))}
        </div>
      </div>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="cost-chart" role="img"
           aria-label="Daily spend, stacked by group">
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + plotH} className="axis" />
        <line x1={PAD.left} y1={PAD.top + plotH} x2={PAD.left + plotW} y2={PAD.top + plotH} className="axis" />
        <text x={PAD.left - 6} y={PAD.top + 8} className="tick" textAnchor="end">{usd(maxTotal)}</text>
        <text x={PAD.left - 6} y={PAD.top + plotH} className="tick" textAnchor="end">$0</text>

        {days.map((day, i) => {
          const x = PAD.left + (plotW / days.length) * i + (plotW / days.length - barW) / 2
          let cursor = PAD.top + plotH
          return (
            <g key={day}>
              {groups.map((g, gi) => {
                const value = byDay.get(day).get(g) || 0
                if (!value) return null
                const h = (value / maxTotal) * plotH
                cursor -= h
                return (
                  <rect key={g} x={x} y={cursor} width={barW} height={h}
                        fill={PALETTE[gi % PALETTE.length]}>
                    <title>{`${day} · ${g} · ${usd(value)}`}</title>
                  </rect>
                )
              })}
              {i % Math.ceil(days.length / 8) === 0 && (
                <text x={x + barW / 2} y={HEIGHT - 8} className="tick" textAnchor="middle">
                  {day.slice(5)}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      <div className="cost-chart-legend">
        {groups.map((g, gi) => (
          <span key={g}>
            <i style={{ background: PALETTE[gi % PALETTE.length] }} /> {g}
          </span>
        ))}
      </div>
    </section>
  )
}
```

- [ ] **Step 6: Wire it into CostView**

In `openbrain-gui/frontend/src/api.js` add:

```js
  getCostTimeseries: (days, group) => request(`/cost/timeseries?days=${days}&group=${group}`),
```

In `openbrain-gui/frontend/src/CostView.jsx`, add the import:

```jsx
import CostChart from './CostChart'
```

Add state and loading. Replace the `load` callback and add a second effect:

```jsx
  const [chartGroup, setChartGroup] = useState('model')
  const [series, setSeries] = useState(null)

  useEffect(() => {
    // The ledger lives in gui.db, so this loads even when /hermes-data is absent.
    api.getCostTimeseries(days, chartGroup).then(setSeries).catch(() => setSeries(null))
  }, [days, chartGroup])
```

Render it directly below `<CostSummary .../>`, outside the `!unavailable` guard:

```jsx
      <CostChart series={series} group={chartGroup} onGroupChange={setChartGroup} />
```

- [ ] **Step 7: Add styles**

Append to `openbrain-gui/frontend/src/index.css`:

```css
.cost-chart { width: 100%; height: auto; }
.cost-chart .axis { stroke: currentColor; opacity: 0.35; }
.cost-chart .tick { fill: currentColor; opacity: 0.7; font-size: 10px; }
.cost-chart-header { display: flex; align-items: center; justify-content: space-between; }
.cost-chart-toggle button.active { font-weight: 600; text-decoration: underline; }
.cost-chart-legend { display: flex; flex-wrap: wrap; gap: 0.75rem; font-size: 0.85em; }
.cost-chart-legend i { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 2px; }
```

- [ ] **Step 8: Build and verify**

```bash
cd openbrain-gui/frontend && npm run build
```

With the backend running, verify on the Cost page:

1. Before any poll has recorded a delta, the chart area shows the "Collecting from now" note — **not** an empty box or a zero line.
2. After two poll cycles with activity, bars appear.
3. The `by model` / `by platform` toggle switches the grouping.
4. Hovering a bar segment shows a tooltip with day, group, and dollar amount.

- [ ] **Step 9: Commit**

```bash
git add openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py openbrain-gui/frontend/src/CostChart.jsx openbrain-gui/frontend/src/CostView.jsx openbrain-gui/frontend/src/api.js openbrain-gui/frontend/src/index.css
git commit -m "feat(gui): daily spend chart from the usage ledger"
```

---

## Task 16: Deploy

**Files:**
- Modify: `deploy/docker-compose.openbrain.yml`

- [ ] **Step 1: Add the read-only mount**

In `deploy/docker-compose.openbrain.yml`, under the `openbrain-gui` service, change:

```yaml
    volumes:
      - openbrain_gui_data:/data
```

to:

```yaml
    volumes:
      - openbrain_gui_data:/data
      # Hermes' own data dir, read-only: state.db (usage/cost) and config.yaml
      # (cost-relevant knobs). :ro means a GUI bug cannot alter Hermes' state.
      # Mounting state.db alone is impossible -- its WAL sidecar files are
      # created and destroyed dynamically. See design spec section 4.4.
      - /docker/hermes-agent-7qpk/data:/hermes-data:ro
```

- [ ] **Step 2: Run the full test suite before deploying**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: all PASS. Do not deploy on a red suite.

- [ ] **Step 3: Commit**

```bash
git add deploy/docker-compose.openbrain.yml
git commit -m "feat(deploy): mount Hermes data read-only into openbrain-gui"
```

- [ ] **Step 4: Deploy to the VPS**

Follow the existing VPS update procedure (push, pull on the VPS, rebuild):

```bash
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain && git pull && docker compose -f deploy/docker-compose.openbrain.yml up -d --build openbrain-gui"
```

- [ ] **Step 5: Verify in production**

```bash
ssh root@srv1608402.hstgr.cloud "docker exec \$(docker ps -qf name=openbrain-gui) ls -la /hermes-data/state.db"
```

Expected: the file is listed.

Then open `https://gui.srv1608402.hstgr.cloud` and confirm:

1. The `Cost` button appears next to `Show keyword graph`.
2. Header tiles show real numbers — Hermes API cost near **$94** for 30 days.
3. `By platform` shows whatsapp as the largest share (~61%).
4. `Top spenders` lists sessions; clicking one opens the drill-down.
5. `Config` shows `model.default`, `compression.threshold`, `tool_output.max_bytes` — and **no** key containing `key`, `secret`, or `token`.
6. The external cost grid saves a row and it survives a page reload.
7. `docker logs` for the GUI container shows no repeating ledger errors:

```bash
ssh root@srv1608402.hstgr.cloud "docker logs --tail 50 \$(docker ps -qf name=openbrain-gui)"
```

- [ ] **Step 6: Confirm the ledger is recording**

Wait ~10 minutes (two poll cycles), then:

```bash
ssh root@srv1608402.hstgr.cloud "docker exec \$(docker ps -qf name=openbrain-gui) python -c \"import sqlite3; c=sqlite3.connect('/data/gui.db'); print('watermarks', c.execute('select count(*) from usage_watermark').fetchone()); print('ledger', c.execute('select count(*) from usage_ledger').fetchone())\""
```

Expected: `watermarks` > 0 immediately; `ledger` grows only once Hermes has been used since the first tick.

---

## Verification summary

After all 16 tasks:

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
cd openbrain-gui/frontend && npm run build
```

Both must be green before the deploy in Task 16.
