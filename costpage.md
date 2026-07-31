# Cost & Token Usage Page

A page in the OpenBrain GUI that answers two questions: **where do my Hermes tokens and dollars
actually go**, and **what does the whole setup cost me per month**.

Live since 2026-07-31 at `https://gui.<vps-host>.hstgr.cloud` — reachable from the **`Cost`**
button next to `Show keyword graph`.

- Design spec — [`docs/superpowers/specs/2026-07-31-openbrain-gui-cost-page-design.md`](docs/superpowers/specs/2026-07-31-openbrain-gui-cost-page-design.md)
- Implementation plan — [`docs/superpowers/plans/2026-07-31-openbrain-gui-cost-page.md`](docs/superpowers/plans/2026-07-31-openbrain-gui-cost-page.md)
- Original requirements — [`planCost.md`](planCost.md)

---

## 1. What it shows

### Part 1 — Hermes token spend

Read live from Hermes Agent's own `state.db`. A date-range selector (7 / 30 / 90 days) drives
everything below it.

| Panel | What it tells you |
|---|---|
| **Total cost of ownership** | Hermes API estimate **+** recurring external costs. One-off costs are shown beside it, never folded in |
| **Hermes API cost** | Period total, marked `estimated`, with your real invoice next to it when you flag one (§2.3) |
| **API calls** | Call count and total tokens for the period |
| **Cache hit rate** | `cache_read ÷ (cache_read + cache_write + input)` |
| **Spend over time** | Stacked daily bars, toggleable by model or platform (§3.2) |
| **By model / By platform** | Sessions, calls, in/out/cache-read/cache-write, cost, % of total |
| **Top spenders** | Per-session costs, sorted. Click a row for the drill-down |
| **Session drill-down** | Per-model rows, message/tool counts, cwd, git branch, compression state, and the session's **actual system prompt** |
| **Token composition** | One stacked bar: input / output / cache-read / cache-write |
| **Efficiency** | Per platform: tokens per call, **cache-write per call**, cost per call, messages per session |
| **Prompt budget** | Average and largest system prompt per platform |
| **Top tools** | Call counts by tool name |
| **Config** | The cost-relevant knobs from Hermes' `config.yaml`, each with a note on what it costs |

### Part 2 — External costs

A spreadsheet for everything Hermes doesn't know about — the VPS bill, the Anthropic invoice,
anything else. Columns: Name, Period, `$`, `€`, URL, Comments, and a "compare to estimate"
checkbox. Buttons: **Add row**, **Delete row** (acts on the radio-selected row), **Save**.

---

## 2. How to read it

### 2.1 Every Hermes cost is an estimate

Hermes computes cost from a pricing snapshot bundled with the agent, not from your invoice
(`cost_status: "estimated"`). Treat the figures as close, not exact. That is precisely why §2.3
exists.

### 2.2 Cache writes are where the money goes

A cache **write** costs 12.5× a cache **read**. A high cache hit rate is good news, but it is not
the whole story — the **cache-write per call** column in the Efficiency panel is the number that
actually tracks your bill.

Measured on this deployment, 30 days:

```
whatsapp   12,621 cache-write tokens/call    $58.78   (62% of spend)
tui         6,117                             $5.96
cli         4,729                            $29.78
subagent    4,601                             $0.69
```

WhatsApp's system prompt is *smaller* than CLI's (≈10k chars vs ≈17.5k). Its cost comes from
conversation length, not prompt bloat — long threads re-write more cache every turn.

### 2.3 Estimate vs. invoice

Tick **"compare to estimate"** (the `≈` column) on the row holding your real Anthropic invoice.
The Hermes tile then shows `invoice $91.40 · −2.9% vs 30d estimate`.

At most one row may carry the flag — setting it on a row clears it everywhere else. This is
enforced in the backend, not the UI, so it holds no matter what writes to the table.

The comparison **always** uses the 30-day figure regardless of the selected range. An invoice
covers a billing month; comparing it against a 7- or 90-day estimate would be meaningless.

### 2.4 Currency

A row stores **one** amount plus which currency you typed it in. The other column is derived.

Type `12.99` into `$` and the row remembers "USD, 12.99" forever — hitting **⟳** later moves the
`€` figure and never touches your 12.99. Type into `€` instead and it flips.

The rate comes from **frankfurter.dev** (European Central Bank daily reference rates — free, no
API key). The box stays editable, so a manual override always works and the network is never a
hard dependency. A failed refresh keeps the previous rate and says so.

> The service used to live at `frankfurter.app`, which now 301-redirects to `frankfurter.dev/v1`.
> httpx does not follow redirects by default and treats a 3xx as an error, so the old host broke
> every refresh with a confusing *"Redirect response '301 Moved Permanently'"*. The default URL now
> points at the new host and redirects are followed, so a future move degrades rather than breaks.

**The rate is not saved by the External costs Save button.** It lives in its own `fx_rate` table,
separate from the grid rows, and is written the moment you leave the box or press Enter — the
`manual, <date>` note beside it is the confirmation. **Save** persists only the spreadsheet rows.
The two are independent on purpose: changing the rate re-derives every row's converted column, so
it is not an edit to any particular row.

**If a euro row has no rate yet**, it contributes 0 to the dollar totals. Rather than print a
confidently wrong number, the page marks the figure with a red `*` and says *"understated — a euro
row needs an exchange rate"*. Press **⟳** once and it resolves.

### 2.5 Where the money figure actually comes from

Also available in the app: the **?** button on the Total cost of ownership tile opens this same
explanation. Keep the two in sync — `CostExplainPopup.jsx` mirrors this section.

Nothing on the page is calculated from token counts. The cost is a straight sum of a column
**Hermes itself writes**:

```
total_cost_of_ownership_usd = hermes.cost_usd + external.monthly_usd

hermes.cost_usd = SUM(estimated_cost_usd)
                  FROM session_model_usage
                  WHERE last_seen falls inside the selected range
```

Running that SQL directly against `state.db` returns the identical figure the API serves — the
page adds nothing of its own. Hermes computes `estimated_cost_usd` per (session, model) from a
pricing table bundled with the agent: `cost_source: official_docs_snapshot`,
`cost_status: estimated`.

Hermes also has an `actual_cost_usd` column, but never populates it — it reads `0.0` on every row.
That is precisely why §2.3's invoice comparison exists.

**With no external rows entered, "Total cost of ownership" is just the API estimate.** It only
becomes a real total once you add the VPS bill and anything else you pay for.

### 2.6 The total is a lower bound

Hermes' pricing table does not cover every model it can talk to. Rows for an unpriced model carry
real API calls and real tokens at `estimated_cost_usd = 0` — silently counted as free.

Measured on this deployment over 30 days: **17 of 114 rows contributed $0.00**, and three of them
were not idle — 20 API calls carrying **1.87 million tokens** across `claude-fable-5` and
`moonshotai/kimi-k3`.

Some of that may genuinely be free (a self-hosted model costs nothing per token). The rest is
simply unknown. Either way the honest reading is *at least* the figure shown, not exactly it.

The page says so rather than hiding it: when any row in the range is unpriced, the Hermes tile
shows **≥** before the amount, the total is marked with a red `*`, and the sub-line reports how
many tokens went unpriced with a link to the full explanation.

### 2.7 Period rules

| Period | Effect |
|---|---|
| `yearly` | ÷ 12 into the recurring monthly total |
| `monthly` | counted as-is |
| `onetime` | excluded from recurring; summed into a separate one-off figure |
| `none` | excluded from both — a parked row you're tracking but not paying |

---

## 3. Honest limits

These are properties of Hermes' data, not bugs. The page states each one rather than papering
over it.

### 3.1 Tokens cannot be attributed to individual tools

`messages.token_count` is `NULL` in **every** row of the live database (0 of 4,818). The Top Tools
panel therefore shows call counts only, and says so on screen.

### 3.2 Daily history starts when the page was installed

`session_model_usage` holds **one row per (session, model, task) covering that session's whole
life**. A WhatsApp thread that ran Jul 25→30 is a single row holding 25M tokens — there is no way
to split it across those six days.

So a background poller samples every 5 minutes, diffs the counters against a stored watermark, and
appends the difference to `gui.db` with a real timestamp. From install day onward you get a true
daily series — and it outlives Hermes' own session pruning.

**The very first tick seeds the watermarks and writes no deltas.** Without that, day one would
record a fabricated ~104M-token, ~$95 spike representing all prior history. Until the poller has
collected something, the chart says *"Collecting from now"* rather than drawing an empty axis that
reads as "you spent nothing".

### 3.3 Date ranges attribute a session to its last day

Because a usage row cannot be split, a row is included when its `last_seen` falls inside the
window, and the **whole** row is counted. This slightly over-attributes long-running sessions to
their final day. Totals stay exact; proportional splitting would invent numbers the source does
not contain.

### 3.4 Pruned sessions keep their spend

If Hermes prunes an old session (`sessions.auto_prune`), its usage row survives and still counts.
Such rows appear as `(pruned)` — the money was real even though the session record is gone. They
are not clickable, since there is no drill-down left to open.

---

## 4. Architecture

No changes to Hermes. Everything is additive to `openbrain-gui` plus one compose line.

```
                Traefik (HTTPS, basic-auth)
                          │
                   openbrain-gui
                   ├── FastAPI  ── /api/cost/*
                   │              └── ledger poller, every 300s
                   └── React    ── CostView

  reads  ──► /hermes-data   (:ro bind of /docker/hermes-agent-7qpk/data)
  writes ──► /data/gui.db   (existing volume)
```

### 4.1 Why reads work from a copy

`state.db` is WAL-mode. Reading a WAL database makes SQLite create and maintain a **`-shm`
coordination file next to the database**, which needs write access to the containing *directory*.
The mount is `:ro`, so that fails — and were it writable, we would be depositing files into
Hermes' own data directory.

So every read copies `state.db` (+ `state.db-wal`) into a `TemporaryDirectory` and opens the
**copy**, letting SQLite build its `-shm` in scratch space. Hermes' files are only ever read.
`state.db-shm` is deliberately not copied: it is derived state SQLite rebuilds from the WAL, and a
stale copy is worse than none.

A copy taken mid-checkpoint can land torn. That is expected, not exceptional — it surfaces as a
503 and the next request or tick retries.

### 4.2 One snapshot per page load

`state.db` is ~42 MB. If each panel opened its own snapshot, a single page load would copy it
seven or eight times for identical data. `GET /api/cost/dashboard` runs every panel off **one**
snapshot instead.

### 4.3 Security

The `:ro` mount exposes Hermes' whole data directory to the GUI container, including `.env` and
`auth.json`. Mounting `state.db` alone is impossible — its WAL sidecar files are created and
destroyed dynamically, and Docker cannot bind-mount files that do not yet exist.

Mitigations: the mount is read-only; the GUI sits behind Traefik basic-auth; and the config panel
reads `config.yaml` through a strict **per-`(section, key)` whitelist**, never a blanket dump, so
a future edit cannot start leaking credentials. A test asserts a planted secret does not appear.

Only these keys are ever returned:

```
model.default              compression.threshold      compression.threshold_tokens
tool_output.max_bytes      prompt_caching.cache_ttl   agent.max_turns
agent.disabled_toolsets    sessions.auto_prune        sessions.retention_days
```

### 4.4 Backend modules

| Module | Responsibility |
|---|---|
| `hermes_usage.py` | Snapshot + read `state.db`; all aggregations; config whitelist |
| `ledger_store.py` | Poll tick (diff vs. watermark), time-series queries |
| `external_costs_store.py` | Part 2 CRUD, period maths, currency derivation, single-flag invariant |
| `fx.py` | USD→EUR rate: fetch, cache, manual override |

### 4.5 gui.db tables

`external_costs` · `fx_rate` (single row) · `usage_ledger` · `usage_watermark`

Applied via `CREATE TABLE IF NOT EXISTS` in `init_db()`, so a deployed `gui.db` upgrades itself in
place on next start. Existing saved prompts and delete-log rows are untouched.

---

## 5. API

```
GET    /api/cost/dashboard?days=30&limit=50   every Part 1 panel, one snapshot
GET    /api/cost/summary?days=30              combined TCO + estimate-vs-invoice
GET    /api/cost/session/{id}                 drill-down (404 if unknown)
GET    /api/cost/config                       whitelisted config.yaml keys
GET    /api/cost/timeseries?days=30&group=model|platform

GET    /api/cost/external                     rows + totals + rate
PUT    /api/cost/external                     upsert (never deletes — see below)
DELETE /api/cost/external/{id}
GET    /api/cost/fx
POST   /api/cost/fx/refresh                   503 if the fetch failed; cache kept
PUT    /api/cost/fx                           manual override
```

**`PUT /api/cost/external` is upsert-only** and deliberately does not delete rows absent from the
payload — the Save button always sends the whole visible grid, and removing a row is a separate
`DELETE`. Making it full-replace would silently drop any row not currently rendered.

**Status codes.** `400` = your input (bad period, bad currency, negative amount, a `url` that is
not `http`/`https`). `404` = unknown row or session. `503` = Hermes' data is unreachable — the
mount is missing or a snapshot copy was unreadable. `503` is not an error you need to act on
unless it persists.

### 5.1 Degradation

When `/hermes-data` is unavailable, the Part 1 endpoints return 503 and the page shows a single
red line. **Part 2 stays fully usable**, and so does the spend chart — the ledger lives in
`gui.db` and never needs the mount. This is what makes local development possible without a VPS.

---

## 6. Operations

### Deploy

```bash
ssh root@<vps-host> "cd /root/HermesPlusOpenbrain && git pull && docker compose -f deploy/docker-compose.openbrain.yml up -d --build openbrain-gui"
```

The compose entry needs this volume — without it every Part 1 panel returns 503:

```yaml
    volumes:
      - openbrain_gui_data:/data
      - /docker/hermes-agent-7qpk/data:/hermes-data:ro
```

No Hermes restart. No change to `openbrain-mcp` or `openbrain-db`.

### Verify

```bash
docker exec deploy-openbrain-gui-1 ls -la /hermes-data/state.db
docker exec deploy-openbrain-gui-1 python -c "import sqlite3;c=sqlite3.connect('/data/gui.db');print('watermarks',c.execute('select count(*) from usage_watermark').fetchone()[0]);print('ledger',c.execute('select count(*) from usage_ledger').fetchone()[0])"
```

After the first start expect **watermarks > 0 and ledger = 0** — that is the seeding rule working.
Ledger rows appear once Hermes has been used since that first tick.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_DATA_DIR` | `/hermes-data` | Where Hermes' data dir is mounted |
| `LEDGER_POLL_SECONDS` | `300` | Poller interval |
| `FRANKFURTER_URL` | frankfurter.app latest USD→EUR | FX source |

### Troubleshooting

| Symptom | Cause |
|---|---|
| Every Part 1 panel red, Part 2 fine | Mount missing — check the compose volume |
| Chart says "Collecting from now" | Normal before the poller has recorded a change |
| Total marked `*` understated | A euro row has no exchange rate — press **⟳** |
| Hermes cost shows **≥** | Some usage is unpriced — see §2.6, click through for the models |
| Rate refresh: `Redirect response '301'` | Old `frankfurter.app` URL. Fixed in the default; check `FRANKFURTER_URL` if overridden |
| `snapshot copy is unreadable` in logs | A copy raced Hermes' WAL checkpoint. Harmless if occasional; the next tick retries |
| Cost differs slightly from your invoice | Expected — Hermes estimates from a pricing snapshot (§2.1) |

---

## 7. Not included

Deferred to a future **Spec B**, deliberately kept separate so the privileged work gets its own
review cycle:

- A **whitelisted action bridge** — a sidecar holding the Docker socket that accepts only a fixed
  list of `hermes` commands, so the page can offer buttons for compaction settings, disabling
  toolsets or skills, session pruning, and toggling debug logging.
- A **local request-logger plugin** for Hermes (`/opt/data/plugins`, using its `on_pre_llm_request`
  / `on_post_llm_call` hooks) writing one JSONL record per API call, giving a ZenMux-style
  per-request log with full request and response bodies — switchable on and off, nothing leaving
  the VPS.
- The `hermes prompt-size` component breakdown (skills index vs. tool schemas vs. memory), which
  needs command execution.

Also deliberately out of scope: a pricing-cliff projection. It would mean maintaining a pricing
table alongside Hermes' own, which then silently drifts.

---

## 8. Testing

162 backend tests (`openbrain-gui/backend/tests/`). The ones worth knowing about, because they
each pin a bug that was actually found and fixed:

- `test_window_uses_epoch_floats_not_iso_strings` — `first_seen`/`last_seen` are epoch floats. A
  `datetime("now","-30 days")` comparison silently returns zero rows and looks like "no activity".
- `test_first_tick_seeds_watermarks_and_emits_no_deltas` — the fake-spike guard.
- `test_summary_still_counts_spend_from_pruned_sessions` — an inner `JOIN` used to delete real
  money from the totals.
- `test_torn_copy_surfaces_as_hermes_data_unavailable` — degrades to 503, not 500.
- `test_snapshot_leaves_the_source_directory_untouched` — asserts on the whole directory, since a
  stray `-shm` beside Hermes' database is the failure mode.
- `test_efficiency_avg_not_skewed_by_duplicate_rows_on_one_of_several_sessions` — sessions with a
  `title_generation` row were counted twice.
- `test_config_snapshot_excludes_unlisted_keys_inside_listed_sections` — proves the whitelist is
  per-key, not per-section.

There is no frontend test framework in this project; the React side is verified by `npm run build`
plus manual checks.
