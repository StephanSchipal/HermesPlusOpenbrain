# OpenBrain Web GUI — Search Filters, Sorting & "Find Similar" — Design Spec

**Date:** 2026-07-26
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-26)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Extend `openbrain-gui`'s search so results can be narrowed by source, date range, and keywords
(combinable with AND/OR), sorted by date in addition to relevance, and — per an in-session scope
addition — let the user jump from any result to "notes similar to this one." This is explicitly a
**search-quality** improvement (as opposed to the analysis-tooling gap around `find_near_duplicates`/
`classify_captures`, or further overview/dashboard work — both deferred, see §10).

## 2. Context & constraints

- `openbrain-mcp/app/store.py`'s `search_captures()` and `fetch_recent()` currently take **no
  filter parameters at all** — no `WHERE` clause beyond the vector ordering (`search_captures`) or
  `ORDER BY created_at DESC` (`fetch_recent`). Filtering client-side in `openbrain-gui` after a
  small top-k fetch was considered and **rejected**: e.g. asking for "whatsapp entries from July"
  by post-filtering the top 25 semantic matches could show zero results even when matching entries
  exist further down the ranked list. Filters must live in the SQL `WHERE` clause itself, in
  `openbrain-mcp`.
- `openbrain-mcp` is consumed by other clients besides `openbrain-gui` (the Hermes WhatsApp agent,
  Claude Desktop/Code as a remote MCP server) — any signature change must be backward compatible.
  All new parameters are optional with `None`/default-preserving behavior, mirroring the existing
  `cluster_captures(k: int | None = None)` pattern.
- Keywords are stored **case-preserving** on `captures.keywords` (an array column); only
  `list_keywords()` lowercases when aggregating across captures (`app/store.py`'s
  `list_keywords()` docstring). A keyword filter must replicate that case-insensitivity itself —
  the underlying array column doesn't guarantee consistent casing across captures.
- The existing keyword panel (`KeywordPanel.jsx`) and the keyword graph (`KeywordGraph.jsx`,
  Phase 3) both wire a bubble/chip click to inserting the keyword as **text** into the prompt
  textarea (`handleKeywordClick` in `App.jsx`). This is a live, already-shipped behavior — this
  design does **not** change it. The new keyword filter is a **separate** UI element with its own
  state, decided explicitly during brainstorming (Option A: "keep separate") specifically to avoid
  a regression risk on that shared handler (which Phase 3's own postmortem flagged as a source of
  a real stale-closure bug once already).
- No frontend automated test framework exists in `openbrain-gui/frontend` (plain Vite + React, no
  test runner in `package.json`) — consistent with Phase 1/3 precedent, verification there stays
  manual/live-browser.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Where filters live | **`openbrain-mcp`**, in the SQL `WHERE` clause | Post-filtering a small top-k in the GUI can miss real matches (§2); this was explicitly raised and confirmed with the user. |
| API shape | **Extend existing `search`/`list_recent`** with optional params, not a new `search_advanced` tool | Smallest API diff, no duplicated query-building/embedding/row-mapping logic, fully backward compatible for Hermes/Claude Desktop. User confirmed after seeing the two-tool alternative. |
| Keyword-chip behavior | **Unchanged** — existing panel/graph keep "click inserts text"; the new keyword filter is a separate UI element with its own chips | Avoids touching a shared, already-fragile handler (Phase 3 postmortem: a stale-closure bug there was only caught by a whole-branch review). User's explicit choice (Option A) over repurposing the existing chips or adding a modifier-key interaction. |
| Keyword filter logic | **Toggleable AND/OR**, not fixed to one | User wants both "must have all of these" and "any of these" depending on the moment — a small toggle next to the filter chips covers both without forcing a choice. |
| Date filter UI | **From/To fields plus quick-range buttons** (7 days / 1 month / this year) | Quick buttons just set the From/To fields — faster for common cases, no separate code path. |
| Result sorting | **Relevance (default) or date (asc/desc)**, client-side re-sort of the already-fetched rows | No `openbrain-mcp` change needed — `created_at`/`source` are already present on every returned row; this is pure re-ordering in the frontend. |
| Browsing without a search term | **`GET /api/recent` (new) backed by `fetch_recent`**, same filters, no relevance column | Confirmed explicitly: filters must work standalone ("show me all whatsapp entries from July" with no prompt text), not only as a refinement after a semantic search. |
| "Find similar" (scope addition) | **`search`'s `query` parameter becomes optional; an alternative `capture_id` parameter reuses the capture's already-stored embedding** (no re-embedding), excludes the source capture from results, and **keeps currently-active filters applied** | Added mid-session after the user asked to bring back a previously-deferred idea. Reusing `search`'s existing filter/sort plumbing (rather than a dedicated new tool) means "similar to X, but only 2026 WhatsApp entries" falls out for free. User confirmed filters should stay active by default (not reset) when jumping into a "find similar" search. |

## 4. Architecture

```
  Browser
      │
      ▼
  ┌───────────────────────────────┐
  │ FilterBar.jsx (NEW)            │  source dropdown, date range + quick buttons,
  │ ResultGrid.jsx (extended)      │  keyword-filter chips + AND/OR toggle, sort dropdown,
  │  - sort dropdown                │  hit count, "Find similar" button per row
  │  - hit count                    │
  │  - "Find similar" button        │
  └───────────────────────────────┘
      │  POST /api/search (query OR capture_id + filters)
      │  GET  /api/recent  (filters only, no query/capture_id)
      ▼
  ┌───────────────────────────────┐
  │ openbrain-gui-backend          │
  │  routes.py (extended)          │──MCP──▶ search(query?, capture_id?, k, source?,
  │                                 │           date_from?, date_to?, keywords?, keyword_mode?)
  │                                 │──MCP──▶ list_recent(n, source?, date_from?, date_to?,
  │                                 │           keywords?, keyword_mode?)
  └───────────────────────────────┘
      │
      ▼
  ┌───────────────────────────────┐
  │ openbrain-mcp                  │
  │  store.py: search_captures(),  │  dynamic WHERE clause, case-insensitive keyword
  │            fetch_recent()      │  array match, capture_id → embedding subquery
  └───────────────────────────────┘
```

Two services touched (`openbrain-mcp` and `openbrain-gui`), one cohesive user-facing feature — kept
as a single spec since the two sides are tightly coupled (the GUI feature has no meaning without
the MCP-side filter support), matching how this project has scoped multi-file features before.

## 5. Components

### 5.1 `openbrain-mcp`

**`app/store.py`** — extended signatures:

```python
def search_captures(conn, *, query: str | None = None, capture_id: str | None = None,
                     k: int = 5, source: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     keywords: list[str] | None = None, keyword_mode: str = "or"
                     ) -> list[dict] | dict:
    """Exactly one of query/capture_id must be given, else {"error": ...}.
    query -> embed_query(query) as usual.
    capture_id -> embedding looked up from the stored row (no re-embedding);
        that capture is excluded from its own results via `id != %s`.
        A capture_id with no matching row -> {"error": "capture not found"}.
    keyword_mode must be "and" or "or", else {"error": ...}.
    """

def fetch_recent(conn, *, n: int = 10, source: str | None = None,
                  date_from: str | None = None, date_to: str | None = None,
                  keywords: list[str] | None = None, keyword_mode: str = "or"
                  ) -> list[dict] | dict:
    """Same filter semantics as search_captures, no query/capture_id/ranking --
    stays ORDER BY created_at DESC."""
```

Filter clause construction (both functions share the approach):
- Only active filters are appended to `WHERE`; all values parameterized (no injection surface).
- `source`: `source = %s`.
- Date range: `created_at >= %s` / `created_at <= %s` (ISO date/datetime strings).
- Keywords, case-insensitive: lowercase both the stored array and the incoming filter list before
  comparing (`ARRAY(SELECT lower(k) FROM unnest(keywords) AS k)` against the lowercased filter
  list) — OR via array-overlap (`&&`), AND via array-contains (`@>`).
- `capture_id` mode's embedding source: `(SELECT embedding FROM captures WHERE id = %s)` as a
  subquery in place of the query embedding; existing capture excluded via `id != %s` in `WHERE`.

**`app/server.py`** — `search`/`list_recent` MCP tool signatures extended with the same optional
parameters, docstrings updated to describe the new `capture_id` mode and filters. Every existing
call (Hermes, Claude Desktop, this project's own smoke tests) that only passes `query`/`k` or
`n` continues to behave identically.

### 5.2 `openbrain-gui-backend`

**`app/routes.py`**:
- `SearchRequest` (Pydantic model) extended: `query: str | None = None`, `capture_id: str | None
  = None`, `source: str | None = None`, `date_from: str | None = None`, `date_to: str | None =
  None`, `keywords: list[str] | None = None`, `keyword_mode: Literal["and", "or"] = "or"`. Invalid
  `keyword_mode` values are rejected by Pydantic (422) before reaching `openbrain-mcp`.
- `POST /api/search` passes all fields through to the `search` MCP tool. If the tool's result is
  an error dict (`{"error": ...}`, e.g. both/neither of `query`/`capture_id` given, or an unknown
  `capture_id`), the route raises `HTTPException(400, detail=...)` — a caller-side problem, distinct
  from the existing `HTTPException(502, ...)` reserved for "`openbrain-mcp` unreachable" (mirrors
  the established `if "error" in cluster_data` pattern from `GET /api/graph`).
- New `GET /api/recent` — same filter query params, calls `fetch_recent`, same 400-vs-502 error
  split.
- No new endpoint needed for available source values — the frontend already has `stats.by_source`
  from the existing `GET /api/stats` call at mount.

### 5.3 `openbrain-gui-frontend`

- **`FilterBar.jsx`** (new) — renders under the existing Prompt/Keyword-Panel row, untouched by
  this design:
  - Source `<select>`, options built from `stats.by_source` keys plus "All".
  - Date-From/Date-To inputs, plus three quick-range buttons (7 days / 1 month / this year) that
    just set those two fields.
  - Its own keyword-filter chip list (separate state from `KeywordPanel`'s existing chips) with an
    "add keyword" control (reuses `GET /api/keywords` for suggestions) and an AND/OR toggle.
  - "Reset filters" button.
  - Emits a single `filters` object upward (`{source, dateFrom, dateTo, keywords, keywordMode}`) —
    `App.jsx` owns the actual state.
- **`ResultGrid.jsx`** (extended):
  - Sort `<select>` (Relevance / Date newest-first / Date oldest-first) — pure client-side re-sort
    of `rows`, no re-fetch. Defaults to Relevance when a `query` search was run, Date newest-first
    when browsing via `/api/recent` (no relevance score to sort by).
  - Hit-count line above the grid ("37 results (filtered from 142)") — "filtered from N" only shown
    when at least one filter is active; N comes from the existing `stats.total`.
  - "Find similar" button per row — calls `api.search({capture_id: row.id, ...currentFilters})`,
    replacing the grid contents like any other search, and switches the sort dropdown back to
    Relevance (a similarity search has a meaningful relevance score again).
- **`App.jsx`**:
  - New state: `filters` (as above), `sortBy`.
  - `handleSearch` (prompt text present) calls `api.search({query: prompt, k, ...filters})` as
    before, now filter-aware.
  - New `handleBrowse` — the existing Search button now checks the prompt text: empty → calls
    `api.getRecent(filters)` (browsing, filters optional — zero filters just shows recent
    captures); non-empty → the existing `POST /api/search` flow with `query` + filters. No new
    button.
  - New `handleFindSimilar(row)` calls `api.search({capture_id: row.id, k, ...filters})`.
  - `FilterBar` and the sort dropdown are new children; existing `PromptBar`/`KeywordPanel`/
    `ResultGrid` action-button wiring (Summary/Change/Delete/view toggles) is untouched.
- **`api.js`**: `search()` extended to accept the full filter/`capture_id` payload; new
  `getRecent(filters)` calling `GET /api/recent` with the same query params.

## 6. Data flow

- **Filtered search:** type a prompt, set filters in `FilterBar` → Search → `POST /api/search`
  with `query` + active filters → grid shows results, sorted by relevance by default, hit count
  shows "N results (filtered from `stats.total`)".
- **Filter-only browse:** leave the prompt empty, set filters, click Search → `GET /api/recent`
  with the same filters → grid shows results sorted by date (no relevance column), same hit count
  treatment.
- **Find similar:** click "Find similar" on any result row → `POST /api/search` with `capture_id`
  (that row's id) + whatever filters are currently active → grid replaced with the neighbor set,
  sort resets to Relevance, the clicked row's own id is guaranteed absent from the new results.
- **Sort change:** picking a different sort option re-orders the already-loaded `rows` in place —
  no network request.
- **Reset filters:** clears `FilterBar`'s state; does not automatically re-run the last search (user
  clicks Search again, consistent with how the rest of the search flow already works).

## 7. Error handling

- `search`/`list_recent` given an invalid parameter combination (both/neither of `query`/
  `capture_id`; unknown `capture_id`; bad `keyword_mode`) → clean `{"error": ...}` dict from
  `openbrain-mcp` (never a raw exception/stack trace), translated by the GUI backend to
  **HTTP 400** with that message.
- `openbrain-mcp` unreachable → existing **HTTP 502** pattern, unchanged.
- Malformed date strings or an out-of-enum `keyword_mode` in the request body → **HTTP 422** via
  Pydantic validation, before any MCP call is made.
- Filters that simply match nothing → an ordinary empty result list (not an error); hit count shows
  "0 results (filtered from `stats.total`)".

## 8. Testing

- **`openbrain-mcp`**: new tests per filter dimension in isolation (source, date range, keywords
  AND, keywords OR, case-insensitivity of keyword matching) and combined; `capture_id` mode
  (excludes itself, unknown id → error, mutually-exclusive-with-`query` validation, filters still
  apply); `server.py` signature/passthrough tests for both `search` and `list_recent`.
- **`openbrain-gui/backend`**: route tests for `POST /api/search` (with each filter, with
  `capture_id`, the 400 error-translation path) and the new `GET /api/recent` (filters, empty
  result, 400/502 paths), mirroring existing `test_routes.py` conventions (mocked MCP responses).
- **Frontend**: no automated suite (per established precedent, §2) — manual verification against a
  real corpus: combined filters return correct results, filter-only browse with no prompt text,
  each sort option, "Find similar" (including that active filters carry over and the source row is
  excluded), filter reset, and a regression pass confirming the existing keyword panel/graph
  click-to-insert behavior is unaffected.

## 9. Deployment

No new secrets, env vars, or `docker-compose.openbrain.yml` changes. `openbrain-mcp` gets a
signature-only change to two existing tools (no new dependency, no migration) — deploys via the
existing "rebuild `openbrain-mcp`" procedure. `openbrain-gui` deploys via its existing rebuild
procedure. Both services should be redeployed together since the GUI's new request shape depends
on the updated MCP tool signatures being live.

## 10. Non-goals (this iteration)

- No changes to the existing keyword panel (`KeywordPanel.jsx`) or keyword graph
  (`KeywordGraph.jsx`) click behavior — both keep inserting keyword text into the prompt, unrelated
  to the new keyword *filter*.
- No surfacing of `find_near_duplicates` or `classify_captures` in the GUI — explicitly deferred to
  a separate, later design (this session's scope is search, not analysis tooling).
- No persisted filter/sort state across a page reload or view switch.
- No full-text (non-semantic) search mode — filters narrow the existing semantic
  search/browse, they don't add a separate keyword-only search path.
- No relevance score for `/api/recent` (browse) results — there is no query to score against.

## 11. Success criteria

- Searching with a prompt and active source/date/keyword filters returns only matching results,
  correctly narrowed at the SQL level (verified against cases where post-filtering a small top-k
  would have wrongly returned zero results).
- Leaving the prompt empty with filters active and clicking Search returns filtered recent
  captures via `/api/recent`, sorted by date.
- Toggling keyword-filter AND vs OR visibly changes the result set when more than one keyword
  filter chip is active.
- Sort dropdown re-orders currently-loaded results with no network request.
- "Find similar" on a result row returns a different, relevant set of neighbors, excludes that row
  itself, and respects any filters that were active at the time of the click.
- Hit count accurately reflects "N results (filtered from `stats.total`)" whenever a filter is
  active.
- Existing keyword panel and keyword graph click-to-insert behavior verified unchanged.
- All existing Phase 1/3 functionality (search without filters, change, delete, delete log, saved
  prompts, keyword graph) continues to work unaffected.

## 12. Implementation outline (to be expanded into a plan)

1. `openbrain-mcp/app/store.py`: extend `search_captures()` and `fetch_recent()` with filter
   params + `capture_id` mode, TDD from scratch (§8).
2. `openbrain-mcp/app/server.py`: extend `search`/`list_recent` tool signatures + docstrings.
3. `openbrain-gui/backend/app/routes.py`: extend `SearchRequest`, `POST /api/search`'s filter/
   `capture_id` passthrough and 400-error translation; add `GET /api/recent`.
4. `openbrain-gui/frontend/src/api.js`: extend `search()`, add `getRecent()`.
5. `FilterBar.jsx`: source dropdown, date range + quick buttons, keyword-filter chips + AND/OR
   toggle, reset button (static UI first, no wiring).
6. `App.jsx`: `filters`/`sortBy` state, `handleSearch`/`handleBrowse`/`handleFindSimilar` wiring,
   mount `FilterBar`.
7. `ResultGrid.jsx`: sort dropdown (client-side re-sort), hit-count line, "Find similar" button.
8. Manual end-to-end verification against the local dev stack (each filter, combined filters,
   browse-only, both sort orders, find-similar, reset, and the keyword-panel/graph regression
   check), then the real production corpus.
9. Update `README.md` and `OpenbrainAddition.md` with the new search capabilities.
