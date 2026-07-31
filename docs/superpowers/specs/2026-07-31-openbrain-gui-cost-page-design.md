# OpenBrain GUI — Cost & Token Usage Page (Spec A) — Design Spec

**Date:** 2026-07-31
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-31)
**Status:** Approved design — ready for implementation planning
**Source of truth for requirements:** `planCost.md`, plus the measured cost baseline gathered
2026-07-30/31 from the live VPS.

---

## 1. Goal

Add a **Cost** page to the existing OpenBrain GUI that answers two questions:

1. *Where do my Hermes tokens and dollars actually go?* — surfaced from Hermes' own `state.db`.
2. *What does the whole setup cost me per month?* — Hermes API spend plus manually-entered
   external costs (Hostinger, Anthropic invoice, …) in one figure.

The page is reached by a new **`Cost`** button placed next to the existing `Show keyword graph`
button, toggling the same `view` state (`App.jsx:288`).

### 1.1 Scope split

`planCost.md` describes four subsystems. They are split across two specs so the privileged work
gets its own review cycle:

- **Spec A (this document)** — read-only cost dashboard (Part 1) + external cost spreadsheet
  (Part 2). Touches nothing outside `openbrain-gui` and its compose entry. Ships a complete,
  useful page on its own.
- **Spec B (future, separate document)** — whitelisted action bridge (compaction settings,
  disabling toolsets/skills, session pruning, debug-log toggle) and a local request-logger
  Hermes plugin feeding a ZenMux-style per-API-call log view.

## 2. Measured baseline that motivates this work

Gathered from `state.db` on 2026-07-31, 30-day window. These numbers are the reason the panels in
§5 are the panels they are.

| Metric | Value |
|---|---|
| Total estimated cost | **$94.17** |
| Cache read tokens | 109.1 M |
| Cache write tokens | 12.8 M |
| Cache hit rate | **89.5%** |

| Platform | Sessions | API calls | Cost | Share |
|---|---|---|---|---|
| whatsapp | 10 | 712 | $57.50 | 61% |
| cli | 62 | 580 | $30.59 | 32% |
| tui | 23 | 208 | $5.69 | 6% |
| subagent | 1 | 29 | $0.39 | <1% |

Top 4 sessions accounted for **$65.86 — 70% of the month.** Caching is already working well; the
bill is driven by *prompt size × number of calls*, and by cache **writes** (a write costs 12.5× a
read). A dashboard that makes top-spender sessions and per-call token averages visible is
therefore worth more than one that reports totals.

## 3. Findings from the live environment

Established by probing the VPS during brainstorming. These constrain the design.

| Finding | Consequence |
|---|---|
| `/opt/data` in `hermes-agent-7qpk-hermes-agent-1` is a **host bind mount** at `/docker/hermes-agent-7qpk/data` | `openbrain-gui` can mount it read-only. No changes to Hermes at all. |
| `state.db` is in **WAL** journal mode | SQLite cannot open a WAL database on a read-only filesystem (it must create a `-shm`). The reader must copy the files and open the copy — see §4.2. |
| `messages.token_count` is **NULL in all 4,818 rows** | No per-message or per-tool token attribution is possible. Tool panels show call counts only, and must say so. |
| Billed tokens live **only** in `session_model_usage`, one row per (session, model, task) spanning the session's whole lifetime via epoch-float `first_seen`/`last_seen` | A live read cannot produce a daily spend chart. Hence the delta ledger (§4.3). |
| `sessions.system_prompt` **is** populated (113/137 rows, up to 25,991 chars) | A prompt-budget panel is possible in Spec A without command execution. WhatsApp averages 10,114 chars vs CLI's 17,541 — WhatsApp's cost is conversation length, not prompt bloat. |
| `session_model_usage.task` is empty except `title_generation`, which already costs **$0.00** | "Route auxiliary tasks to a cheap model" is *not* a real lever here. The dashboard must not imply otherwise. |
| `cost_status` is `estimated` (106 rows), `unknown` (3), NULL (14); `cost_source` is `official_docs_snapshot` | All displayed costs carry an **estimated** badge. This is what makes the estimate-vs-invoice check in §6 worth having. |
| Hermes ships `insights`, `prompt-size --json`, `logs`, `sessions stats`, and its own `dashboard` | Confirms the data exists; those CLI paths need command execution, so they belong to Spec B. `prompt-size`'s component split (skills index / tool schemas / memory) is deferred there. |
| Hermes has an LLM observability plugin API (`on_pre_llm_request` / `on_post_llm_call`) and a user plugin dir at `/opt/data/plugins` | The mechanism for Spec B's request logger. Noted here so Spec A's mount is designed to also serve it. |

## 4. Architecture

No changes to Hermes. Spec A is additive to `openbrain-gui` plus two lines of compose.

```
                Traefik (TLS + basic auth)
                          │
                   openbrain-gui
                   ├── FastAPI backend
                   │     ├── /api/cost/*          NEW
                   │     └── ledger poller        NEW, every 300 s
                   └── React frontend
                         └── CostView             NEW, "Cost" button

  reads  ──► /hermes-data   (ro bind of /docker/hermes-agent-7qpk/data)
  writes ──► /data/gui.db   (existing openbrain_gui_data volume)
```

### 4.1 Compose change

```yaml
  openbrain-gui:
    volumes:
      - openbrain_gui_data:/data
      - /docker/hermes-agent-7qpk/data:/hermes-data:ro   # NEW
```

### 4.2 Reading `state.db` safely

`state.db` is WAL-mode, so it cannot be opened directly off a read-only mount. The reader
therefore:

1. copies `state.db` and `state.db-wal` from `/hermes-data` into container scratch
   (~42 MB, ~50 ms);
2. opens the **copy** with `file:...?mode=ro&uri=true`, letting SQLite replay the WAL locally;
3. reads;
4. deletes the copy.

`state.db-shm` is deliberately **not** copied — it is derived state that SQLite rebuilds from the
WAL, and a stale copy would be worse than none.

This costs one file copy per read but buys two things: the GUI physically cannot write to Hermes'
data even through a bug, and each read is a point-in-time snapshot rather than a moving target
across several queries.

A copy taken mid-write can land torn. That is treated as a normal, expected failure: the read
raises, the previous snapshot is served, and the poller skips the tick.

### 4.3 Why a delta ledger exists

`session_model_usage` holds lifetime totals per session. A WhatsApp thread that ran Jul 25→30 is
**one row** holding 25 M tokens with no way to attribute them across those six days. Daily and
hourly spend charts are therefore impossible from a live read.

The poller solves this by sampling: every 300 s it diffs the current counters against a stored
watermark and appends only what changed, stamped with a real observation time. From install day
forward this yields a true time series — and it survives Hermes' session pruning
(`sessions.retention_days: 90`), which would otherwise erase history from the live source.

**The first tick seeds watermarks and emits no deltas.** Without this rule, day one records a
fabricated 104 M-token spike representing all prior history. The chart is instead honest about
starting from install.

### 4.4 Security note (accepted, recorded deliberately)

The read-only mount exposes the entire Hermes data directory to the GUI container — including
`.env` (the Anthropic API key), `auth.json`, and `config.yaml`. Backend code opens `state.db` and
`config.yaml` only, but the *capability* is present.

Mounting `state.db` alone is not possible: the WAL sidecar files are created and destroyed
dynamically, and Docker cannot bind-mount files that do not yet exist.

Mitigations: the mount is `:ro` (nothing in Hermes' data can be altered), the GUI already sits
behind Traefik basic auth, and no route ever returns arbitrary file contents — only parsed,
whitelisted fields.

## 5. Part 1 — the cost dashboard

Date-range selector at top: 7 / 30 / 90 days / custom, following the ZenMux reference
(`ZenMux1.png`).

**How the date range applies to lifetime rows.** `session_model_usage` rows span a session's whole
life, so a session that started before the window and ended inside it cannot be split. The rule is:
**a row is included when its `last_seen` falls inside the selected window, and the whole row is
counted.** This slightly over-attributes long-running sessions to their final day, and the panels
say so. It is the only rule that keeps totals exact — proportional splitting would invent numbers
the source does not contain. The `Spend over time` chart (§5.2) has no such limitation because it
reads the ledger, whose rows carry real observation times.

### 5.1 Header strip

Four tiles. USD primary, € underneath at the current rate.

| Tile | Content |
|---|---|
| **Total cost of ownership** | Hermes API estimate **+** recurring external monthly costs (§6.2). One-off costs are shown as a separate figure beside it, never folded in |
| **Hermes API cost** | Period total, `estimated` badge, and — if flagged (§6.3) — actual invoice beside it |
| **API calls / tokens** | Call count and total tokens for the period |
| **Cache hit rate** | `cache_read ÷ (cache_read + cache_write + input)` |

### 5.2 Spend over time

Stacked daily bars, toggleable between *by model* and *by platform*. Sourced from `usage_ledger`.
This is the only panel that starts empty; until it fills it shows **"collecting since
&lt;date&gt;"** rather than a blank chart or a misleading zero line.

### 5.3 Breakdown tables

Live from `state.db`, exact for all history Hermes still retains.

- **By model** — sessions, API calls, input / output / cache-read / cache-write, cost, % of total.
- **By platform** — same columns. Makes the 61% WhatsApp share immediately visible.
- **By session (top spenders)** — title, platform, model, messages, API calls, tokens, cost.
  Sortable, defaulting to cost descending. This is the panel that surfaces "4 sessions = 70% of
  the month" without anyone writing a query.

### 5.4 Session drill-down

Clicking a session row opens a detail pane — the Protokolldetails analogue (`ZenMux2.png`) at
session grain:

- per-model rows for that session (tokens split, calls, cost)
- message count, tool call count, first/last seen
- `cwd`, `git_branch`, `profile_name`
- compression state: `compression_fallback_streak`, `compression_failure_error`,
  `compression_failure_cooldown_until`
- the session's **actual `system_prompt`** with its character count

### 5.5 Token composition

A single stacked bar — input / output / cache-read / cache-write — annotated with the pricing
ratio that makes it actionable: a cache **write** costs 12.5× a cache **read**. Roughly half the
measured bill sits in that segment.

### 5.6 Efficiency panel

Per platform, the numbers that actually move the bill:

- average tokens per API call
- average **cache-write** tokens per API call (measured: WhatsApp ~20 k vs CLI ~3 k)
- cost per API call
- average messages per session

### 5.7 Prompt budget

Average `system_prompt` length per platform, plus the largest individual prompts. The
`hermes prompt-size` component split (skills index vs. tool schemas vs. memory) requires command
execution and is deferred to Spec B.

### 5.8 Top tools

Call counts by `messages.tool_name` (measured: `terminal` is 61% of all calls). **Counts only.**
The panel states explicitly that token attribution per tool is unavailable because
`messages.token_count` is NULL — it must not imply precision the data does not have.

### 5.9 Config snapshot

Read-only display of the cost-relevant knobs parsed from `config.yaml`, each with a one-line note
on what it costs:

`model.default` · `compression.threshold` · `compression.threshold_tokens` ·
`tool_output.max_bytes` · `prompt_caching.cache_ttl` · `agent.max_turns` ·
`agent.disabled_toolsets` · `sessions.auto_prune` · `sessions.retention_days`

Only these keys are read and returned. In Spec B they grow Edit buttons backed by the whitelisted
bridge.

### 5.10 Explicitly out of scope for Spec A

- Per-API-call log table and request/response bodies — needs the logger plugin (Spec B).
- `hermes prompt-size` component breakdown — needs command execution (Spec B).
- Any write action (compact, disable skills, toggle debug logging) — Spec B.
- A Sonnet-5 intro-pricing-cliff projection. It would mean maintaining our own pricing table
  alongside Hermes' `official_docs_snapshot`, which then silently drifts. Not worth it.

## 6. Part 2 — external cost table

A spreadsheet-style grid below Part 1 on the same page.

| ○ | Name | Period | $ | € | URL | Comments | ☑ |
|---|---|---|---|---|---|---|---|
| ● | Hostinger VPS | monthly | 12.99 | 11.18 | *link* | KVM2, renews 4th | ☐ |
| ○ | Anthropic | monthly | 94.17 | 81.05 | *link* | July invoice | ☑ |

Buttons: **Add row** · **Delete row** · **Save**.

- Delete acts on the radio-selected row and asks for confirmation. Radio (single selection) matches
  how View / Change / Delete already work on the capture grid (`App.jsx:276-284`).
- Save is **explicit, not autosave** — one request persisting all dirty rows.
- URL renders as an anchor opening in a new tab with `rel="noopener noreferrer"`.

### 6.1 Currency behaviour

Each row stores **one** amount plus the currency it was entered in; the other column is derived
for display.

- Enter `12.99` in **$** → row stores `(USD, 12.99)`. Refreshing the rate moves the € figure and
  never touches your 12.99.
- Enter into **€** instead → € becomes authoritative and $ derives.

Rate control above the grid: `Rate $ → €  [0.8607]  ⟳  ECB, 2026-07-31`.

The ⟳ button fetches from **frankfurter.app** (European Central Bank daily reference rates, free,
no API key, no signup). The field stays editable, so a manual override always works and the
network is never a hard dependency.

### 6.2 Period semantics

| `period` | Effect on totals |
|---|---|
| `yearly` | ÷ 12 into the monthly total |
| `monthly` | counted as-is |
| `onetime` | **excluded** from the recurring monthly total; summed into a separate "one-off" figure shown beside it |
| `none` | excluded entirely — a parked row for something tracked but not paid |

### 6.3 Estimate vs. actual

One extra column beyond `planCost.md`'s field list: a checkbox, **"compare to Hermes estimate"**.

Ticking it on the Anthropic row makes the header tile show
`est. $94.17 → actual $91.40 (−2.9%)`. This is worth having because every Hermes cost figure is
`cost_status: estimated` from a docs snapshot (§3) — the invoice is the only ground truth.

The comparison is always against the **30-day** Hermes estimate, regardless of the date range
selected above, and is labelled as such. An invoice covers a billing month; comparing it to a
7-day or 90-day figure would be meaningless.

The alternative — matching on the literal name "Anthropic" — breaks the moment the row is renamed.

**Invariant:** at most one row may carry the flag. Setting it on a row clears it on all others,
enforced in the store, not the UI.

## 7. Data model

`db.py` already applies its schema via `CREATE TABLE IF NOT EXISTS` inside an `executescript`
(`db.py:7-37`), so this is an append to `_SCHEMA`. The existing `gui.db` upgrades itself on next
`init_db()` — no migration machinery.

```sql
CREATE TABLE IF NOT EXISTS external_costs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL DEFAULT '',
    period              TEXT    NOT NULL DEFAULT 'monthly',   -- yearly|monthly|onetime|none
    amount              REAL,                                  -- the figure actually typed
    entered_currency    TEXT    NOT NULL DEFAULT 'USD',        -- USD|EUR
    url                 TEXT,
    comments            TEXT,
    compare_to_estimate INTEGER NOT NULL DEFAULT 0,            -- at most one row = 1
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fx_rate (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    usd_to_eur  REAL NOT NULL,
    source      TEXT NOT NULL,          -- 'frankfurter' | 'manual'
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT    NOT NULL,     -- ISO 8601 UTC, when the poller saw it
    session_id    TEXT    NOT NULL,
    model         TEXT    NOT NULL,
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
    session_id        TEXT NOT NULL,
    model             TEXT NOT NULL,
    task              TEXT NOT NULL DEFAULT '',
    api_call_count    INTEGER,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens  INTEGER,
    estimated_cost_usd REAL,
    PRIMARY KEY (session_id, model, task)
);
```

## 8. Backend modules

Each follows the existing `prompts_store` / `delete_log_store` pattern: one module, one purpose,
a plain function interface, no shared mutable state.

| Module | Responsibility | Depends on |
|---|---|---|
| `hermes_usage.py` | Copy + open `state.db` read-only; aggregate by model / platform / session; session detail; config-snapshot parse | `/hermes-data` |
| `ledger_store.py` | Poll tick: diff vs. watermark, append deltas, upsert watermark; query the time series | `hermes_usage`, `gui.db` |
| `external_costs_store.py` | Part 2 CRUD, period maths, currency derivation, single-flag invariant | `gui.db` |
| `fx.py` | Fetch/cache the USD→EUR rate, manual override | frankfurter.app, `gui.db` |
| `routes.py` | New `/api/cost/*` endpoints | all of the above |

### 8.1 Endpoints

```
GET    /api/cost/summary?days=30          header tiles + combined total
GET    /api/cost/by-model?days=30
GET    /api/cost/by-platform?days=30
GET    /api/cost/by-session?days=30&limit=50
GET    /api/cost/session/{session_id}     drill-down detail
GET    /api/cost/timeseries?days=30&group=model|platform
GET    /api/cost/tools?days=30
GET    /api/cost/config                   whitelisted config.yaml keys
GET    /api/cost/external                 list rows
PUT    /api/cost/external                 save all dirty rows
DELETE /api/cost/external/{id}
GET    /api/cost/fx
POST   /api/cost/fx/refresh
PUT    /api/cost/fx                       manual override
```

## 9. Error handling

Every failure degrades the page rather than breaking it. In particular, **Part 2 must remain
fully usable when Hermes' data is unreachable** — that is what makes local development possible
without a VPS.

| Failure | Behaviour |
|---|---|
| `/hermes-data` absent (local dev) | The `state.db`-backed endpoints — `summary`, `by-model`, `by-platform`, `by-session`, `session/{id}`, `tools`, `config` — return **503** with a plain message. `external`, `fx` and `timeseries` are unaffected, so Part 2 stays fully usable |
| Copy torn / database locked | Tick skipped, previous snapshot served, logged at WARNING |
| `config.yaml` missing or unparseable | Config snapshot panel shows "unavailable"; every other panel unaffected |
| frankfurter.app unreachable or slow | Last known rate kept; UI shows "rate is N days old"; **Save is never blocked** |
| Counter decreases between ticks | Delta clamped to 0 and logged — never writes negative spend |
| Two rows flagged `compare_to_estimate` | Impossible: setting the flag clears it elsewhere inside the store |

### 9.1 Write validation

- `period` ∈ {`yearly`, `monthly`, `onetime`, `none`}
- `entered_currency` ∈ {`USD`, `EUR`}
- `amount` ≥ 0 or NULL
- `url` must parse with scheme `http` or `https` — this stops a `javascript:` URL being stored
  and later clicked from the grid

## 10. Testing

Mirrors the existing `backend/tests/` layout (pytest, one test module per source module).

- **`test_hermes_usage.py`** — fixture `state.db` built with the real schema. Aggregation by
  model / platform / session; cache-hit-rate maths; session detail assembly. Explicitly covers
  **epoch-float timestamps**: `first_seen` is `1785435861.70`, not an ISO string. (A
  `datetime("now","-30 days")` comparison silently returns zero rows — this already cost one
  debugging round during research and is the single most likely implementation mistake.)
- **`test_ledger_store.py`** — first tick seeds watermarks and emits no deltas; second tick emits
  correct deltas; unchanged rows write nothing; decreasing counters clamp to 0.
- **`test_external_costs_store.py`** — CRUD; all four period rules; currency derivation in both
  directions; the single-flag invariant.
- **`test_fx.py`** — frankfurter response parsing; fallback to cached rate on network failure;
  manual override wins over a fetched value.
- **`test_routes.py`** (additions) — the 503 path when `/hermes-data` is absent; validation
  rejections; `PUT /api/cost/external` round-trip.

## 11. Frontend

`CostView.jsx`, toggled by the new button, composed of focused children in the same
file-per-concern style as the existing components:

| Component | Renders |
|---|---|
| `CostSummary.jsx` | §5.1 header tiles |
| `CostChart.jsx` | §5.2 stacked daily bars |
| `CostTables.jsx` | §5.3 by-model / by-platform / by-session |
| `SessionDetail.jsx` | §5.4 drill-down pane |
| `CostConfig.jsx` | §5.7–5.9 prompt budget, tools, config snapshot |
| `ExternalCostGrid.jsx` | §6 spreadsheet, rate control, totals |

Styling reuses `index.css`, so the existing light/dark toggle applies with no extra work. The
chart is drawn with plain SVG — the project already has d3 for the keyword graph, but stacked bars
do not justify pulling it into a second view.

## 12. Deployment

1. `docker compose -f deploy/docker-compose.openbrain.yml up -d --build openbrain-gui`
2. First start creates the four new tables in the existing `gui.db` and seeds ledger watermarks
   (emitting no deltas).
3. Verify: `Cost` button appears, header tiles populate from live `state.db`, `Spend over time`
   shows "collecting since &lt;today&gt;".

No Hermes restart. No change to `openbrain-mcp` or `openbrain-db`.

## 13. Open items deferred to Spec B

- Whitelisted action-bridge sidecar (holds the Docker socket, accepts only a fixed command list;
  reachable solely on the internal Docker network).
- Action buttons: compaction settings, `agent.disabled_toolsets`, skill opt-out,
  `sessions prune|archive|optimize`, debug-log toggle.
- Local request-logger Hermes plugin (`/opt/data/plugins`, using `on_pre_llm_request` /
  `on_post_llm_call`) writing JSONL into `/opt/data/logs`, read through the same mount.
- Per-API-call Protokolle table + request/response detail pane.
- `hermes prompt-size` component breakdown.
