# OpenBrain Web GUI — Phase 1 — Design Spec

**Date:** 2026-07-24
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-24)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Build a single-page web GUI for browsing, searching, editing, and deleting captures stored in
`openbrain-db` — a friendlier alternative to querying via Claude Desktop/Code or WhatsApp. This is
**Phase 1** of a three-phase plan (see `planGUI.md`):

- **Phase 1 (this spec):** search/browse/change/delete captures, saved search prompts, delete log.
- **Phase 2 (future, separate spec):** a dynamic word cloud over keywords, with AND/OR search by
  keyword selection.
- **Phase 3 (future, separate spec):** surfacing the vector database's clustering/classification
  capabilities in the GUI.

Source of truth for the requested layout: `planGuiProposal.png` (Phase 1 wireframe) and
`Wordcloud.jpg` (Phase 2 reference, out of scope here).

## 2. Context & constraints

- `openbrain-db` (Postgres + pgvector) is network-isolated — reachable **only** from
  `openbrain-mcp`, enforced at the Docker network layer (see `README.md`'s security model). The
  GUI backend must not be added to that network; it accesses captures exclusively through
  `openbrain-mcp`'s MCP tools, the same way Claude Desktop/Code already do (remote MCP over
  Streamable HTTP, bearer token).
- `openbrain-mcp` currently exposes 10 tools (`save`, `search`, `list_recent`, `stats`, `delete`,
  `update`, `find_near_duplicates`, `compute_fingerprint`, `cluster_captures`,
  `classify_captures`). None of them return corpus-wide keyword frequency — Phase 1 needs a new
  **11th tool**, `list_keywords()`.
- This is a **single-user** tool (confirmed during brainstorming) — no login screen, no per-user
  data model. Traefik basic-auth in front of the whole GUI is the access control boundary, same
  posture as the existing `brain.<vps-host>.hstgr.cloud` MCP endpoint and Hermes' own dashboard.
- Hermes-Agent is already configured with an Anthropic API key (confirmed by the user) — the GUI
  backend reuses that same key/provider directly for its own LLM calls, rather than shelling out to
  the Hermes CLI (which would load full agent context — tools/skills/memory — for a task that needs
  none of that).
- Frontend: React. Backend: Python. Both explicitly requested in `planGUI.md`.
- Deployment target: the same Hostinger VPS (`srv1608402.hstgr.cloud`), alongside the existing
  `hermes-agent`, `openbrain-db`, `openbrain-mcp`, and `traefik` containers.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| User model | **Single user, Traefik basic-auth** | Confirmed explicitly — this is a personal tool, like the rest of the project. No user table, no app-level login. |
| Captures DB access | **Always through `openbrain-mcp`, never direct Postgres** | `openbrain-db` is deliberately unreachable except from `openbrain-mcp`; the GUI backend is just another authenticated MCP client. |
| Small project DB (saved prompts + delete log) | **SQLite, owned directly by the GUI backend** | Personal-scale data, no need for a second Postgres instance to run/back up. Explicitly separate from `openbrain-db` — "all DB access via OpenBrain_MCP" refers to the captures data, not this GUI-local bookkeeping. |
| Subject-line generation | **Real LLM call per row, using Hermes' existing Anthropic API key directly (not the Hermes CLI)** | User confirmed "slow is fine for Phase 1." Calling the model directly (not `hermes chat`) avoids loading full agent tools/skills/memory for a plain text-in/text-out task, and avoids side effects from a supposedly read-only GUI action. |
| Initial page load | **`stats()` summary only; Result grid stays empty until Search** | User explicitly chose this over auto-populating the grid with recent captures. |
| Keyword panel scope | **Whole corpus, by frequency, with a filter textbox** | Confirmed — also lays groundwork for the Phase 2 word cloud, which needs the same data. Requires the new `list_keywords()` tool. |
| Saved prompts | **No separate name field — dropdown label is the truncated prompt text** | Fastest to use for a single-user tool; avoids an extra step on every save. |
| Theme (light/dark) | **User-selectable, stored in browser `localStorage`** | Requested during the visual review; purely a client display preference, no backend round-trip needed. |
| Deletion audit trail | **Snapshot-then-delete ordering**: the GUI backend writes the delete-log entry into SQLite *before* calling `delete()` on `openbrain-mcp` | Guarantees no silent deletion with zero log entry; worst case is an orphan log entry if the MCP call then fails, never the reverse. |

## 4. Architecture

```
  Browser (your laptop)
      │  HTTPS, Traefik basic-auth (single user)
      ▼
  ┌─────────────────────────────┐
  │ openbrain-gui-frontend       │   NEW — React SPA (Vite build)
  │ served as static files by ── │
  │ the backend container below  │
  └─────────────────────────────┘
      │  same-origin REST calls (/api/search, /api/keywords, ...)
      ▼
  ┌─────────────────────────────┐        ┌──────────────┐
  │ openbrain-gui-backend        │──MCP──▶│ openbrain-mcp │──▶ openbrain-db (Postgres)
  │ NEW — FastAPI (Python)       │ bearer │  (existing)   │     (existing, untouched)
  │  - holds OPENBRAIN_TOKEN     │ token  └──────────────┘
  │  - holds Anthropic API key   │
  │    (same one Hermes uses)    │──▶ Claude Haiku (subject lines)
  │  - owns gui.db (SQLite)      │
  │    saved prompts + delete log│
  └─────────────────────────────┘
```

One new container (frontend + backend built together, multi-stage Dockerfile), one new Traefik
hostname/route, one new Docker volume (for `gui.db`). Nothing about the existing
`hermes-agent`/`openbrain-db`/`openbrain-mcp`/`traefik` stack changes except `openbrain-mcp`
gaining its 11th tool.

The frontend never holds `OPENBRAIN_TOKEN` or the Anthropic key — only the backend does. The
frontend only ever talks to the GUI backend's own `/api/*` routes.

## 5. Components

### 5.1 New MCP tool — `openbrain-mcp`

`list_keywords()` — read-only, scans all captures, returns keyword frequency across the whole
corpus:

```python
@mcp.tool()
def list_keywords() -> list[dict]:
    """List every distinct keyword across all captures with its frequency,
    most-frequent first. Read-only."""
    with get_conn() as conn:
        return store.list_keywords(conn)
```

```python
def list_keywords(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT keyword, count(*) AS n
            FROM captures, unnest(keywords) AS keyword
            GROUP BY keyword
            ORDER BY n DESC, keyword ASC
            """
        )
        rows = cur.fetchall()
    return [{"keyword": r[0], "count": r[1]} for r in rows]
```

Built and reviewed following the same one-capability workflow as the last 4 additions
(`find_near_duplicates`, `compute_fingerprint`, `cluster_captures`, `classify_captures`): its own
spec section here is sufficient given its small scope (single query, no new dependency), but it
still gets its own tests in `openbrain-mcp/tests/` and its own entry in `README.md`'s tool table.

### 5.2 `openbrain-gui-backend` (FastAPI)

| Route | Behavior |
|---|---|
| `GET /api/stats` | Proxies `stats()`. Powers the initial-load view. |
| `GET /api/keywords?filter=` | Proxies `list_keywords()`; filters by case-insensitive substring match server-side. |
| `POST /api/search {query, k?}` | Proxies `search(query, k)`; `k` defaults to 25 if omitted (vs. the underlying tool's own default of 5 — a GUI browsing view wants more results per search than a chat-style single answer). For each returned row, calls Claude Haiku once to derive `subject_line` from `summary` (falls back to a truncation heuristic per-row on failure — see §7). |
| `POST /api/captures/{id}/delete` | Snapshots the row (subject line, keywords, source_url, created_at, deletion timestamp) into `gui.db`'s delete log, **then** calls `delete(id)`. |
| `PATCH /api/captures/{id} {summary?, keywords?}` | Proxies `update(id, summary, keywords)`. |
| `GET /api/prompts` / `POST /api/prompts {text}` / `DELETE /api/prompts/{id}` | CRUD on saved prompts in `gui.db`. No MCP call involved — this is GUI-local bookkeeping, not capture data. |
| `GET /api/delete-log` | Reads `gui.db`'s delete log, newest first. |

Talks to `openbrain-mcp` as an MCP client (Python `mcp` SDK, Streamable HTTP transport, same bearer
token pattern already used by Claude Desktop/Code).

`gui.db` (SQLite) schema:

```sql
CREATE TABLE prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL  -- ISO 8601
);

CREATE TABLE delete_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL,       -- the openbrain-db capture's uuid
    subject_line TEXT,
    keywords TEXT,                  -- JSON-encoded list
    source_url TEXT,
    captured_at TEXT,               -- the capture's original created_at, snapshotted
    deleted_at TEXT NOT NULL        -- ISO 8601, when the GUI delete action ran
);
```

### 5.3 `openbrain-gui-frontend` (React + Vite)

Single page, no router needed — everything fits on one screen per `planGuiProposal.png`:

- `PromptBar` — saved-prompt dropdown, 3-line prompt textarea, Search / Save prompt / Delete prompt
  buttons.
- `KeywordPanel` — filter textbox + scrollable keyword list; clicking a keyword inserts it at the
  current cursor position in the prompt textarea (pure frontend state, no API call).
- `ResultGrid` — one row per capture (radio button, id, subject line, source URL, created-at,
  keywords); Change/Delete buttons enable only when a row is selected.
- `DeleteLogView` — swapped in for `ResultGrid` when "Show delete log" is clicked; same row layout
  plus a deletion-datetime column; read-only (no radio/change/delete).
- `ChangePopup` — modal pre-filled with the selected row's summary/keywords; Save calls `PATCH
  /api/captures/{id}`.
- Theme toggle (light/dark) — top-level, `localStorage`-backed, no backend involvement.

## 6. Data flow

- **Load:** frontend calls `GET /api/stats` and `GET /api/keywords` (empty filter) on mount →
  renders the stats line and full keyword panel. Grid stays empty until a search runs.
- **Search:** user types or selects a saved prompt, clicks Search → `POST /api/search` → backend
  calls `search()`, then one Claude Haiku call per row for `subject_line` → grid renders.
- **Keyword click:** inserts the keyword into the prompt textarea at the cursor position; the user
  still has to click Search to act on it.
- **Keyword filter:** debounced `GET /api/keywords?filter=` re-renders the keyword panel.
- **Row selection:** radio selection is frontend-only state; enables Change/Delete.
- **Delete:** confirmation dialog → `POST /api/captures/{id}/delete` (snapshot-then-delete ordering,
  §3) → row removed from the grid.
- **Change:** popup pre-filled with current summary/keywords → Save → `PATCH
  /api/captures/{id}` → grid row updates in place, subject line recomputed.
- **Show delete log:** toggles the grid area to `GET /api/delete-log`.

## 7. Error handling

- **`openbrain-mcp` unreachable or returns 401** → backend returns 502 to the frontend; frontend
  shows a banner instead of a blank/silently-failing grid.
- **Claude Haiku call fails or times out for a given row** → that row falls back to a
  truncated-summary heuristic (first ~10 words); the rest of the grid still renders normally — one
  slow/failed row must not block the whole search.
- **Delete: MCP call fails after the log snapshot was written** → worst case is an orphan log entry
  with no matching deletion; logged server-side as a warning, not surfaced to the user as an error
  (best-effort audit trail, not a source of truth). The reverse — a deletion with no log entry —
  cannot happen given the snapshot-then-delete ordering.
- **Change/update validation** → empty summary or keyword list rejected client-side before hitting
  the API.
- **`gui.db` (SQLite) unreachable** (disk/permission issue) → saved-prompts and delete-log features
  degrade independently with an inline error in just those panels; search/change/delete against
  `openbrain-mcp` keep working since they don't depend on `gui.db`.

## 8. Testing

- **`list_keywords()`** — pytest against a real Postgres+pgvector instance (reuse
  `openbrain-test-db`), following `openbrain-mcp/tests/` conventions: empty corpus → `[]`; known
  keyword distribution → correct counts and descending sort order; case handling consistent with
  `normalize_keywords()`.
- **`openbrain-gui-backend`** — unit tests with the MCP client and Anthropic calls mocked: route
  behavior, snapshot-before-delete ordering, keyword filter logic, SQLite CRUD for prompts and the
  delete log. One thin integration test against a real local `openbrain-mcp` + test DB, mirroring
  the manual smoke tests done for the last 4 MCP capabilities.
- **`openbrain-gui-frontend`** — no automated test suite planned for Phase 1 (single-user, one
  screen) — verified by manual exercise against the local dev stack in a real browser, consistent
  with this project's existing "run it, click through it" verification norm for UI-facing work.

## 9. Deployment

- New Traefik hostname, e.g. `gui.<vps-host>.hstgr.cloud`, fronted by a Traefik basic-auth
  middleware (single credential pair).
- New service in `deploy/docker-compose.openbrain.yml` (or a new compose file alongside it):
  `openbrain-gui`, built from a new `openbrain-gui/` directory in the repo — multi-stage Dockerfile
  (build the React app, copy the static output into the FastAPI image).
- New env vars in `deploy/.env`: reuses the existing `OPENBRAIN_TOKEN` (the GUI backend is just
  another authenticated MCP client) + `ANTHROPIC_API_KEY` (same key Hermes uses) + Traefik
  basic-auth credentials.
- `gui.db` (SQLite) on its own small persistent Docker volume, separate from `openbrain-db`'s
  volume.

## 10. Non-goals (this iteration)

- No multi-user support, no login screen, no per-user data model.
- No word cloud, no AND/OR keyword search (Phase 2).
- No clustering/classification UI (Phase 3).
- No creating new captures from the GUI (`save` is not exposed) — captures are only ever created
  via Hermes/WhatsApp.
- No caching of generated subject lines — recomputed on every render, matching the user's explicit
  "slow is fine for Phase 1."
- No read/write token scoping on `OPENBRAIN_TOKEN` (the README already flags this as a deferred
  hardening item generally) — the GUI backend reuses the single existing token.
- No restore/undelete from the delete log.
- No automated frontend test suite.

## 11. Success criteria

- `list_keywords()` returns correct corpus-wide frequency counts, alongside the existing 33
  `openbrain-mcp` tests with no regressions.
- The GUI, running locally via Docker Compose, reproduces every interaction in `planGuiProposal.png`
  plus the two additions from visual review (theme toggle, keyword filter box): search, select,
  change, delete, save/delete prompt, show delete log.
- A capture deleted via the GUI disappears from subsequent searches and appears in the delete log
  with a correct snapshot and deletion timestamp.
- Deployed behind Traefik on the VPS at a new hostname, reachable only with valid basic-auth
  credentials, functioning end-to-end against the real `openbrain-mcp`/`openbrain-db`.

## 12. Implementation outline (to be expanded into a plan)

1. Add `list_keywords()` to `openbrain-mcp` (store.py + server.py + tests + README entry) — its own
   small TDD cycle, mirroring the last 4 capabilities.
2. Scaffold `openbrain-gui-backend` (FastAPI project, MCP client wiring, SQLite schema for
   `prompts`/`delete_log`).
3. Implement backend routes (§5.2) with unit tests (mocked MCP/Anthropic calls).
4. Scaffold `openbrain-gui-frontend` (Vite + React project, component skeletons from §5.3).
5. Wire frontend components to backend routes; implement theme toggle (`localStorage`).
6. Multi-stage Dockerfile combining frontend build + backend; local Docker Compose smoke test.
7. Manual end-to-end verification against a local stack (search, change, delete, delete log, saved
   prompts, keyword filter/click).
8. Add `openbrain-gui` service + Traefik basic-auth + new hostname to
   `deploy/docker-compose.openbrain.yml` / `deploy/.env.example`; deploy to the VPS.
9. Update `README.md` and `OpenbrainAddition.md` with the new tool and the new GUI.
