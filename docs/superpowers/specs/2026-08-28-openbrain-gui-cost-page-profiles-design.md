# OpenBrain GUI — Cost page: per-bot (per-profile) view — Design

**Date:** 2026-08-28
**Status:** Draft — awaiting user sign-off
**Builds on:** `docs/superpowers/specs/2026-07-31-openbrain-gui-cost-page-design.md` (Spec A)

## Problem

Hermes now runs seven profiles on the VPS, each its own agent with its own
`state.db`:

| Profile | Role |
|---|---|
| `default` | the main Hermes-Agent (voice, CLI) |
| `openbrain` | the WhatsApp workflow (see `CaptureBotDocu.md`) |
| `master` | future orchestrator for the specialist bots |
| `coder`, `designer`, `researcher`, `writer` | specialist bots |

The Cost page reads exactly one `state.db` (`/hermes-data/state.db`, the
`default` profile's). So six of the seven bots' spend is invisible, and the page
silently under-reports the total.

## Goal

A **profile selector** on the Cost page:

- a dropdown listing every bot, `default` labelled **"Hermes-Agent"**;
- default selection **"All"** — every panel shows the summed-across-all-bots
  numbers, plus a new **per-bot breakdown table** and a **per-bot config table**;
- selecting one bot scopes every panel to that bot's `state.db`.

Fleet-wide figures that don't belong to any one bot — the total cost of
ownership and the estimate-vs-invoice check — stay fleet-wide regardless of the
dropdown.

## Non-goals

- Any change to Hermes, `openbrain-mcp`, `openbrain-db`, or the compose file.
  Named-profile `state.db` files are already inside the existing `/hermes-data`
  read-only mount (`/hermes-data/profiles/<name>/state.db`).
- Part 2 (the external-cost spreadsheet) — it is inherently fleet-wide and
  unchanged.
- A chart grouping mode `by profile` (stacked bars per bot). Cheap to add later
  off the same ledger column; left out to keep scope tight.
- Merging or de-duplicating sessions across profiles — each `state.db` is its
  own partition; there is nothing to de-dupe.
- Backfilling ledger history for the six new bots. Their `state.db` files are
  days old and near-empty; the per-profile seed rule (§3) handles them honestly.

## Findings that shape the design

| Finding | Consequence |
|---|---|
| `hermes_usage.py` already takes `data_dir` on **every** function, and `snapshot(data_dir)` reads `Path(data_dir)/"state.db"` | A single-bot view needs no new query code — just the right `data_dir`. |
| Each profile has its own `state.db`; `sessions.profile_name` in `default`'s db is `(null)` for 218/219 rows | The **db file is the partition**, not a column. Don't filter by `profile_name`. |
| `default`'s `state.db` is ~90 MB; the six others are 0.26–0.85 MB | Looping all seven per request/tick is dominated by the one `default` copy (~50 ms). Acceptable. |
| `usage_ledger` already carries a denormalised `platform` column; `usage_watermark` PK is `(session_id, model, task)` | Add a `profile` column to both; rebuild `usage_watermark`'s PK. |
| `db.py` has no migration machinery — `_SCHEMA` is `CREATE TABLE IF NOT EXISTS` in one `executescript` | Add a tiny explicit migration in `init_db()` for the `profile` column + watermark rebuild. |
| `main.py` poller: `asyncio.to_thread(ledger_store.run_once)` every `LEDGER_POLL_SECONDS` | Poller calls a new `run_all()` that fans out over discovered profiles. |
| The GUI backend already `cp`s `state.db` per read (WAL-on-`:ro` workaround) | The "all" path takes N copies per dashboard load; the `dashboard()` "one snapshot, all panels" optimisation is preserved **per profile**. |

## Architecture

```
                       CostView.jsx
             ┌──────────── profile <select>  (All | Hermes-Agent | coder | …)
             │            days buttons
             ▼
   GET /api/cost/profiles                       → profiles.list_profiles()
   GET /api/cost/dashboard?days=&profile=        ┐
   GET /api/cost/summary?days=&profile=          │  profile == "all"
   GET /api/cost/config?profile=                 ├─►  cost_merge.*  ── loops ──┐
   GET /api/cost/session/{id}?profile=           │                            │
   GET /api/cost/timeseries?days=&group=&profile=┘  else ───────────────────┐ │
                                                                            ▼ ▼
                                     hermes_usage.<fn>(data_dir=<profile dir>)
                                     ledger_store.timeseries(profile=…)
```

### 1. `profiles.py` (new module)

```python
def list_profiles() -> list[dict]:
    """[{key, label, data_dir}], always starting with the root profile.
    Order: 'default' first, then named profiles alphabetically."""
```

- Root: `key="default"`, `label="Hermes-Agent"`, `data_dir=HERMES_DATA_DIR`.
- Named: each `HERMES_DATA_DIR/profiles/<name>/` that contains a `state.db` →
  `key=<name>`, `label=<name>`, `data_dir=<that dir>`.
- `resolve(key) -> data_dir | None` — used by the routes to turn `?profile=` into
  a directory; unknown key → 404.
- Discovery is filesystem-only (no Hermes call). If `HERMES_DATA_DIR` is absent
  (local dev), returns `[]` and the profile-scoped routes 503 exactly as today.

New route:

```
GET /api/cost/profiles → [{"key": "default", "label": "Hermes-Agent"}, {"key": "coder", "label": "coder"}, …]
```

The frontend prepends the synthetic `{"key": "all", "label": "All"}`.

### 2. `cost_merge.py` (new module) — the "All" path

One function per merged shape. Each loops `profiles.list_profiles()`, calls the
existing `hermes_usage` function per profile, and **combines raw counters, then
recomputes derived metrics** — never averages an average.

| Function | Merge rule |
|---|---|
| `dashboard_all(days, limit)` | Per profile: `hermes_usage.dashboard(data_dir=…)`. Then: `summary` = sum every integer/float counter, recompute `cache_hit_rate = cache_read / (cache_read + cache_write + input)`, merge `unpriced` (sum `api_calls`/`tokens`, union+sort `models`). `by_model` / `by_platform` = group rows by key, sum columns, re-sort by `cost_usd` desc. `by_session` = concat all rows, add `"profile"` to each, sort by `cost_usd` desc, take `limit`. `efficiency` = per `platform`, sum raw `_SUM_COLUMNS` across profiles then recompute `tokens_per_call` / `cache_write_per_call` / `cost_per_call`; `avg_messages_per_session` = Σ(row.avg·row.sessions) / Σ(row.sessions). `top_tools` = sum `calls` per `tool_name`, re-sort, top 15, `token_attribution_available: false`. `prompt_budget` = per `platform`, `avg_system_prompt_chars` = Σ(row.avg·row.sessions)/Σ(row.sessions), `max_system_prompt_chars` = max. |
| `per_bot_breakdown(days)` | Per profile: `hermes_usage.summary(data_dir=…, days=days)` → `{key, label, sessions, api_calls, tokens (= input+output+cache_read+cache_write), cost_usd}`. Add `pct = cost_usd / Σcost_usd`. Sorted by `cost_usd` desc. A profile whose `state.db` is unreadable contributes a row with `"unavailable": true` and zeros (it must not fail the whole page). |
| `config_all()` | Per profile: `hermes_usage.config_snapshot(data_dir=…)` → `{key, label, **snapshot}`. `config.yaml` missing/unparseable → `{key, label, "unavailable": true}`. |

`weighted_mean(pairs)` and `sum_counters(dicts, keys)` are small private helpers,
unit-tested directly.

### 3. Ledger — `profile` column + per-profile polling

**Schema (`db.py`).** `_SCHEMA` gains `profile TEXT NOT NULL DEFAULT 'default'`
on both `usage_ledger` and `usage_watermark`, and `usage_watermark`'s PK becomes
`(profile, session_id, model, task)`.

**Migration (new `_migrate(conn)` called from `init_db` **after** `executescript`).**
`_SCHEMA` gets the final shape (column present, new PK). On a fresh `gui.db`,
`executescript` builds it correctly and `_migrate` finds the `profile` column
already there → both branches no-op. On an existing `gui.db`, `executescript`'s
`CREATE TABLE IF NOT EXISTS` skips the pre-existing tables, and `_migrate` closes
the delta:

```
if 'profile' not in columns(usage_ledger):
    ALTER TABLE usage_ledger   ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'
if 'profile' not in columns(usage_watermark):
    CREATE TABLE usage_watermark_new (… , PRIMARY KEY (profile, session_id, model, task))
    INSERT INTO usage_watermark_new SELECT 'default', session_id, model, task, api_call_count, …
      FROM usage_watermark
    DROP TABLE usage_watermark
    ALTER TABLE usage_watermark_new RENAME TO usage_watermark
```

Existing rows are all `default`'s — the constant `'default'` backfill is correct,
not a guess. `usage_watermark` is pure derived state; even if the rebuild were
ever skipped, the worst case is one seed tick that emits no deltas.

**`ledger_store.py`:**

- `apply_tick(rows, *, profile, path=None, observed_at=None)` — new required
  `profile` kwarg. `seeding` becomes **per-profile**:
  `seeding = conn.execute("SELECT 1 FROM usage_watermark WHERE profile=? LIMIT 1", (profile,)).fetchone() is None`.
  The `usage_ledger` INSERT and `usage_watermark` UPSERT both include `profile`;
  the UPSERT conflict target becomes `(profile, session_id, model, task)`.
- `run_once(*, profile, data_dir, path=None)` — `profile` and `data_dir` both
  required now (callers already have them).
- **`run_all(*, path=None)` (new)** — `for p in profiles.list_profiles(): run_once(profile=p["key"], data_dir=p["data_dir"], path=path)`. Never raises (same contract as `run_once`); logs per-profile skips.
- `timeseries(*, path=None, days=30, group="model", profile="all", now_iso=None)`
  — adds `AND profile = ?` to the WHERE unless `profile == "all"`. `collecting_since`
  is likewise scoped (`MIN(observed_at)` filtered by profile unless "all").

**`main.py`:** poller calls `ledger_store.run_all` instead of `run_once`.

### 4. Routes (`routes.py`)

`_profile_dir(profile: str) -> str` helper: `"all"` → sentinel handled by the
caller; a real key → `profiles.resolve(key)` or `HTTPException(404)`.

| Route | Change |
|---|---|
| `GET /api/cost/profiles` | **new** — `profiles.list_profiles()` stripped to `{key,label}` |
| `GET /api/cost/dashboard` | `+ profile: str = "all"`. `all` → `cost_merge.dashboard_all(days, limit)`; else `_hermes(hermes_usage.dashboard, data_dir=dir, days=days, limit=limit)` |
| `GET /api/cost/config` | `+ profile: str = "all"`. `all` → `cost_merge.config_all()`; else `_hermes(hermes_usage.config_snapshot, data_dir=dir)` |
| `GET /api/cost/session/{id}` | `+ profile: str`. **Required, not "all"** — a session id only resolves inside one `state.db`; the frontend always passes the row's own `profile`. `_hermes(hermes_usage.session_detail, id, data_dir=dir)` |
| `GET /api/cost/timeseries` | `+ profile: str = "all"` → `ledger_store.timeseries(days=, group=, profile=)` |
| `GET /api/cost/summary` | `+ profile: str = "all"`. See §5. |
| `GET /api/cost/by-bot` | **new** — `days: int = 30` → `_hermes(cost_merge.per_bot_breakdown, days=days)` |

`_hermes()` stays the 503 wrapper; `cost_merge.*` raise `HermesDataUnavailable`
only when **no** profile is readable (a single bad `state.db` degrades to one
`unavailable` row, page still renders).

### 5. `/api/cost/summary` — what stays fleet-wide

The response keeps its current shape and adds `hermes_cost_usd_selected`:

- `total_cost_of_ownership_usd/eur`, `total_cost_of_ownership_incomplete`,
  `comparison` (estimate-vs-invoice), and the 30-day baseline behind it —
  **always computed from the all-bots figure** (`cost_merge` sum), because the
  Anthropic invoice and Hostinger line are fleet-wide. Unchanged when a specific
  bot is selected.
- `cost_usd`, `api_calls`, token counts, `cache_hit_rate`, `unpriced` — scoped
  to `?profile=` (the "Hermes API cost", "API calls / tokens", "Cache hit rate"
  tiles reflect the selected bot; `all` = the sum).

Frontend labels the TCO tile "(all bots + external)" so the two scopes are not
confused.

## Frontend

| File | Change |
|---|---|
| `api.js` | `getCostProfiles()`; `getCostByBot(days)`; add `profile` arg to `getCostDashboard`, `getCostSummary`, `getCostConfig`, `getCostSession`, `getCostTimeseries` (append `&profile=`) |
| `CostView.jsx` | `const [profile, setProfile] = useState('all')`; fetch `getCostProfiles()` once on mount, prepend `{key:'all',label:'All'}`; a `<select>` in the `.cost-range` header row; `load` + timeseries effect gain `profile` in deps and pass it; render `<CostByBot>` when `profile === 'all'` |
| `CostByBot.jsx` | **new** — table: Bot · Sessions · API calls · Tokens · Cost (USD, € under) · % of total. Row click sets `profile` to that bot (quick drill-in). Hidden unless `profile === 'all'` |
| `CostConfig.jsx` | accept `config` as either a dict (one bot) or a list of `{key,label,…}` (all); in the list case render a table — Bot · model.default · compression.threshold · compression.threshold_tokens · prompt_caching.cache_ttl · agent.max_turns · agent.disabled_toolsets · sessions.retention_days; `unavailable` rows show "—" |
| `CostTables.jsx` | by-session table: add a leading **Bot** column, rendered only when `profile === 'all'`, from each row's `profile`; pass the clicked row's `profile` up alongside its `session_id` |
| `SessionDetail.jsx` | take a `profile` prop, pass to `getCostSession(id, profile)` |
| `CostView` (reports) | `reportName(days, isToday, profileKey)` → `CostReport_<profileKey>_<code>_<date>`; the saved payload already carries whatever `data`/`profile` is shown. Old-named reports still load |
| `format.js` | `reportName` signature + the doc comment above it |

No CSS work — the `<select>` and new tables reuse `index.css` tokens, so
light/dark just works.

## Data flow

`profile` selection → every `api.getCost*` call carries `&profile=` → route
either dispatches to `cost_merge` (all) or resolves a `data_dir` and calls the
unchanged `hermes_usage` fn → merged/selected dict → same React components,
one extra table when `all`.

Ledger: poller every 300 s → `run_all()` → per profile `run_once` → per-profile
seed-or-delta into `usage_ledger`/`usage_watermark` keyed by `profile` →
`timeseries(profile=)` reads it back.

## Error handling

| Failure | Behaviour |
|---|---|
| `/hermes-data` absent (local dev) | `list_profiles()` → `[]`; `/api/cost/profiles` → `[]`; the frontend shows only "All", and every state.db-backed route 503s exactly as today. Part 2 unaffected. |
| One profile's `state.db` torn / unreadable, `profile=all` | That profile contributes an `unavailable` row (by-bot / config) or is skipped (merged panels), logged WARNING; the page renders. |
| One profile's `state.db` unreadable, that profile selected | 503 for the state.db-backed panels, same as Spec A. |
| Unknown `?profile=` key | 404 `{"detail": "unknown profile: <key>"}` |
| `?profile=all` on `/cost/session/{id}` | 400 — a session id is not resolvable without a specific profile |
| Ledger migration runs twice | Idempotent — column-existence check guards both branches |
| A profile dir with no `state.db` yet (bot created, never run) | Not listed by `list_profiles()` until its `state.db` exists |

## Testing

New/extended pytest modules, mirroring `backend/tests/`:

- **`test_profiles.py`** — discovery with a tmp `/hermes-data` tree: root only;
  root + named; a `profiles/x/` dir *without* `state.db` is excluded; ordering
  (`default` first, then alphabetical); `resolve()` unknown key → None; empty
  when the dir is absent.
- **`test_cost_merge.py`** — two synthetic `state.db` fixtures (reuse
  `test_hermes_usage.py`'s builder). `dashboard_all` sums `summary` counters and
  recomputes `cache_hit_rate` from the sum (not the mean of the two rates);
  `by_model` merges same-model rows; `by_session` tags rows with `profile` and
  respects `limit` across the pool; `efficiency`/`prompt_budget` weighted means;
  `per_bot_breakdown` percentages sum to 1.0; one unreadable db → `unavailable`
  row, others still counted.
- **`test_ledger_store.py`** (extend) — `apply_tick(profile=…)` seeds per
  profile (profile A's first tick emits nothing even after profile B has ticked);
  deltas are attributed to the right `profile`; `timeseries(profile="coder")`
  excludes `default` rows; `timeseries(profile="all")` includes both;
  `run_all()` fans out and never raises when one profile's read fails.
- **`test_db_migration.py`** (new) — build a `gui.db` on the *old* schema
  (no `profile` column, old watermark PK), run `init_db()`, assert: `profile`
  column present on both tables, watermark rows preserved with `profile='default'`,
  new PK enforced, second `init_db()` is a no-op.
- **`test_routes.py`** (extend) — `/api/cost/profiles` shape; `?profile=` plumbed
  through dashboard/summary/config/timeseries; `?profile=all` on `/session/{id}`
  → 400; unknown profile → 404; `/api/cost/by-bot` shape; `/api/cost/summary`
  TCO/comparison identical for `?profile=all` and `?profile=openbrain` while the
  per-bot tiles differ.

## Rollout

1. `docker compose -f deploy/docker-compose.openbrain.yml up -d --build openbrain-gui`.
2. First start runs the ledger migration on the existing `gui.db` (adds the
   column, rebuilds the watermark — existing history preserved as `default`).
3. The next poll tick seeds watermarks for the six new profiles (emits no
   deltas); their `Spend over time` shows "collecting since <today>".
4. Verify: the profile dropdown lists all seven bots + "All"; "All" shows the
   per-bot table and a higher total than before; selecting `openbrain` scopes
   every panel; TCO tile unchanged across selections.

No Hermes restart, no compose change, no `openbrain-mcp`/`openbrain-db` change.

## Docs

- This spec.
- `costpage.md` — new "Per-bot view" section: the dropdown, what "All" merges,
  why TCO stays fleet-wide, the `profile` ledger column.
