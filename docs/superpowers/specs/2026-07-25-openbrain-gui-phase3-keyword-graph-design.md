# OpenBrain Web GUI — Phase 3 — Keyword Graph — Design Spec

**Date:** 2026-07-25
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-25)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add a "Keyword Graph" view to `openbrain-gui`: a visual, clustered map of the corpus's keywords,
inspired by [graphify.com](https://graphify.com)'s code-knowledge-graph view (auto-detected
communities, node size by importance, filter-by-cluster sidebar). This is **Phase 3** of the
three-phase plan in `planGUI.md` — **Phase 2 (word cloud + AND/OR keyword search) is explicitly
skipped** in favor of this graph, per the user's decision. Powered entirely by the existing
`cluster_captures` MCP tool — **no new `openbrain-mcp` capability is needed** for this phase.

## 2. Context & constraints

- `openbrain-mcp` already exposes `cluster_captures()` (k-Means over embeddings, k optional/
  auto-selected via silhouette score, returns cluster membership with a `central` flag on up to 3
  centroid-nearest members per cluster) and `list_recent(n)` (captures with their `keywords`).
  Neither needs to change for this phase.
- `cluster_captures()` requires at least 4 captures (`_MIN_CAPTURES_TO_CLUSTER` in
  `openbrain-mcp/app/store.py`); with fewer, it returns `{"error": "..."}` instead of clusters.
- The existing `openbrain-gui` frontend is deliberately minimal — plain JS/JSX, no TypeScript, very
  few dependencies. This phase adds exactly three: `d3-force`, `d3-zoom`, `d3-selection` (small,
  focused D3 sub-packages — not the full `d3` bundle).
- Established project lesson (from the Phase 1 subject-line simplification): don't add a live LLM
  call where a deterministic computation already answers the question. Cluster *labels* here are
  derived the same way subject lines are — no model call.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Graph nodes | **Keywords**, not individual captures | User's explicit request — a keyword-level map, not a per-capture graph. |
| Node relationships | **No edges** — keywords sit in a colored "cluster bubble" region, sized by frequency | User chose this ("Option B") over a co-occurrence network graph after seeing both side by side — simpler, calmer, no edge computation. |
| Cluster source | **`cluster_captures` only** (auto k, zero setup) | `classify_captures` needs caller-supplied categories (a whole extra management UI) — deferred; `cluster_captures` matches graphify's "auto-detected community" with no user setup at all. |
| Where it lives | **New toggle view**, like "Show delete log" | Full-width, dedicated space for a graph; the existing PromptBar and flat keyword-filter list stay untouched and visible above it. |
| Click on a keyword node | **Insert into the search prompt** | Reuses the exact same `handleKeywordClick` already wired to the existing keyword chips — one consistent behavior everywhere. |
| Zoom/pan | **Yes** — scroll/pinch to zoom, drag to pan, plus explicit +/− buttons | User's explicit ask; `d3-zoom` is the standard, well-tested solution — not worth hand-rolling. |
| Label color | **White text with a dark stroke halo**, not theme-dependent `--fg` | User's explicit fix — stays readable against any bubble color in either theme. |
| Hover tooltips | **Two flavors**: hovering a keyword shows captures containing it; hovering a cluster in the legend shows every capture in that cluster | Directly answers "what's in this cluster," not just "what's this one keyword." |
| Central captures | **Highlighted (★) in tooltips** | `cluster_captures` already returns a `central` flag (centroid-nearest captures) that nothing currently surfaces — free, graphify's "god node" idea applied to clusters instead of code symbols. |
| Keyword filter box | **Yes**, above the graph, client-side only | Mirrors the user's own Phase 1 request for the flat keyword list; no extra network round-trip since the full dataset is already in memory after one fetch. |
| Cluster legend toggle | **Client-side only** (dim/hide, no re-fetch) | Instant, free — the full graph payload is already local after the initial load. |

## 4. Architecture

```
  Browser
      │
      ▼
  ┌─────────────────────────────┐
  │ KeywordGraph.jsx (NEW)       │  d3-force (cluster + collision layout, no link force)
  │  - filter textbox            │  d3-zoom (pan/zoom on the SVG)
  │  - cluster legend + toggles  │
  │  - hover tooltips            │
  └─────────────────────────────┘
      │  GET /api/graph
      ▼
  ┌─────────────────────────────┐
  │ openbrain-gui-backend        │
  │  app/graph.py (NEW)          │──MCP──▶ list_recent(n=GRAPH_MAX_CAPTURES)  (id, keywords)
  │  build_keyword_graph()       │──MCP──▶ cluster_captures()                (id → cluster_id, central)
  └─────────────────────────────┘
```

One new backend module, one new route, one new frontend component, one new toggle button next to
the existing "Show delete log". No `openbrain-mcp`, database schema, or deployment changes.

## 5. Components

### 5.1 `openbrain-gui-backend`

**`app/graph.py`** (new, pure functions — no I/O, easy to unit test):

```python
def build_keyword_graph(captures: list[dict], clusters: list[dict]) -> dict:
    """captures: list_recent()'s output ({"id", "keywords", ...} per capture).
    clusters: cluster_captures()'s "clusters" list
        ([{"cluster_id", "size", "members": [{"id", "summary", "central"}]}]).
    Returns {"clusters": [...], "keywords": [...]} -- see §5.2 for exact shape.
    Captures present in `captures` but absent from the cluster membership (or vice
    versa -- possible if a capture was added/deleted between the two MCP calls,
    an accepted race at this tool's single-user scale) are simply skipped."""
```

Algorithm:
1. Build `capture_id -> cluster_id` and `capture_id -> central` maps from `clusters`.
2. For each capture (from `list_recent`), look up its `cluster_id`; skip captures with no match.
3. For each `(cluster_id, keyword)` pair, tally occurrences. A keyword's overall `cluster_id` is
   the cluster where it occurs most often (ties broken by the lowest `cluster_id`).
4. A cluster's `label` is the keyword with the highest occurrence count *restricted to that
   cluster's own captures* (ties broken alphabetically).
5. Each cluster's `captures` list holds `{"id", "subject_line", "central"}` — `subject_line` via
   the existing `subject_line.make_subject_line()`, for display consistency with the rest of the
   GUI (tooltips, not the graph nodes themselves, show capture text). `cluster_captures` already
   returns members nearest-centroid-first, so `central: true` entries naturally come first in this
   list too — no extra sorting needed.

**`app/config.py`**: add `GRAPH_MAX_CAPTURES = 100_000` — effectively unbounded for a personal-
scale corpus; named so it isn't a bare magic number in `routes.py`.

**`app/routes.py`**: add `GET /api/graph`:
- Calls `list_recent` (with `n=GRAPH_MAX_CAPTURES`) and `cluster_captures` (no `k` — auto-select).
- If `cluster_captures` returns `{"error": ...}` (fewer than 4 captures), respond `200` with
  `{"error": "not_enough_captures", "count": <total captures>}` — this is an expected small-corpus
  state, not a failure, so it does **not** raise a 502 or show the error banner.
- Otherwise, respond with `build_keyword_graph(...)`'s output directly.
- MCP unreachable → existing 502 pattern (same as every other route).

### 5.2 Response shape

```json
{
  "clusters": [
    {"cluster_id": 0, "label": "claude", "size": 4,
     "captures": [{"id": "...", "subject_line": "...", "central": true}, ...]}
  ],
  "keywords": [
    {"keyword": "claude", "count": 4, "cluster_id": 0}
  ]
}
```

Or, when there's not enough data yet: `{"error": "not_enough_captures", "count": 2}`.

### 5.3 `openbrain-gui-frontend`

- **`KeywordGraph.jsx`** (new) — fetches `GET /api/graph` on mount; renders:
  - An SVG canvas with one circle per keyword (radius ∝ √count, matching graphify's area-based
    sizing), positioned by a `d3-force` simulation: a per-cluster centroid-attraction force
    (centroids arranged in a simple circle around the canvas center, one per cluster) plus a
    collision force to keep bubbles from overlapping. No link force — there are no edges.
  - `d3-zoom` attached to the SVG: scroll-wheel/pinch zoom, drag-to-pan, plus on-screen +/− buttons
    that call the zoom behavior's `scaleBy` programmatically.
  - Keyword labels: white fill, dark stroke halo (`paint-order: stroke`), always readable.
  - A fixed, cycling color palette (~10 distinct colors) keyed by `cluster_id % palette.length` —
    not hardcoded to 3 colors.
  - A filter textbox above the canvas (debounced like the existing `KeywordPanel`, 200ms) that
    dims/hides non-matching bubbles by substring match — purely client-side, no new request.
  - A cluster legend sidebar: one row per cluster (color dot, label, capture count, checkbox).
    Unchecking dims/hides that cluster's bubbles — client-side only.
  - Hover tooltips: hovering a keyword bubble shows its capture list (subject lines, first 3 +
    "+N more"); hovering a legend row shows the whole cluster's capture list the same way, with
    `central: true` captures prefixed with a star.
  - A "Refresh" button that re-fetches `/api/graph` and re-runs the simulation from scratch — a
    full recluster can reshuffle which `cluster_id` means what, which is expected, not a bug.
  - Fewer than 4 captures → renders a plain message ("Not enough captures yet to build a graph —
    need at least 4, you have `{count}`.") instead of the canvas.
  - Click on a keyword bubble calls the same `onKeywordClick` handler `App.jsx` already passes to
    the existing `KeywordPanel` — identical insert-at-cursor behavior in the still-visible prompt
    textarea.

- **`App.jsx`**: replace the boolean `showDeleteLog` with a three-way `view` state
  (`'results' | 'deleteLog' | 'graph'`) so only one alternate view is ever active. Add a
  "Show keyword graph" button to `.grid-actions` (next to "Show delete log"), rendering
  `<KeywordGraph onKeywordClick={handleKeywordClick} />` when active.

- **`api.js`**: add `getGraph: () => request('/graph')`.

- New `package.json` dependencies: `d3-force`, `d3-zoom`, `d3-selection` (small, focused
  sub-packages, not the full `d3` bundle — consistent with the existing lean frontend).

## 6. Data flow

- **Open the graph:** click "Show keyword graph" → `KeywordGraph` mounts → `GET /api/graph` →
  simulation settles, legend populates.
- **Explore:** zoom/pan the canvas; type in the filter box (dims non-matching bubbles instantly,
  no request); toggle cluster checkboxes (instant, no request); hover a bubble or legend row for a
  tooltip.
- **Act:** click a bubble → keyword inserted into the prompt textarea above (still visible,
  identical to today's keyword-chip behavior) → user clicks Search as usual.
- **Recompute:** "Refresh" → re-fetch `/api/graph`, discard and rebuild the simulation.
- **Leave:** switching to "Back to results" or "Show delete log" unmounts `KeywordGraph` — no
  state (filter text, toggled clusters, zoom level) persists across a re-open, matching the
  explicit non-goal below.

## 7. Error handling

- `openbrain-mcp` unreachable during `/api/graph` → existing 502 + error banner pattern, same as
  every other route.
- Fewer than 4 captures → **not** an error banner; a plain inline message in the graph view area
  (§5.3), since this is an expected state for a small/young corpus, not a failure.
- A capture id present in one MCP response but not the other (race between the two calls) →
  silently skipped when building the graph (§5.1) — acceptable at this tool's single-user scale.

## 8. Testing

- **`app/graph.py`** — unit tests, no MCP/DB involved: fixed captures+clusters input →
  correct per-keyword counts and dominant-cluster assignment (including a documented tie), correct
  cluster labels (including a documented alphabetical tie-break), `central` passthrough, and a
  capture present in one input but not the other being skipped without error.
- **`GET /api/graph`** route — mocked MCP responses, mirroring `test_routes.py` conventions:
  success path, the `not_enough_captures` path, and the MCP-unreachable 502 path.
- **Frontend** — no automated suite, per the established Phase 1 precedent (single-user, one
  screen); verified by manual exercise in a real browser against the live corpus, same as every
  other GUI feature so far.

## 9. Deployment

No new secrets, env vars, database schema, or `docker-compose.openbrain.yml` changes. The
multi-stage Dockerfile's existing `npm ci` picks up the three new frontend dependencies
automatically. Deploys the same way every prior GUI change has (`git pull` on the VPS, rebuild
`openbrain-gui`).

## 10. Non-goals (this iteration)

- No edges/lines between keywords (bubble-cluster only, per the user's explicit choice).
- No editing or deleting captures from the graph view — read-only exploration plus search-insert.
- No manual cluster-count (`k`) selection — always auto-selected, matching the underlying tool's
  own default behavior.
- No `classify_captures` / user-defined categories (deferred; `cluster_captures` alone covers this
  phase).
- No persisted graph state (filter text, toggled clusters, zoom level, scroll position) across a
  view close/reopen.
- No draggable nodes — pan/zoom of the whole canvas only.
- No changes to `openbrain-mcp` — this phase is entirely additive within `openbrain-gui`.

## 11. Success criteria

- Opening "Show keyword graph" against the real production corpus renders keyword bubbles grouped
  into `cluster_captures`'s auto-detected clusters, with sizes proportional to keyword frequency.
- Clicking a bubble inserts that keyword into the search prompt, identically to the existing
  keyword chips.
- Hovering a bubble or a legend row shows an accurate capture list via tooltip, with central
  captures starred.
- The filter textbox visibly dims/hides non-matching bubbles with no network request.
- Zoom (scroll/pinch/buttons) and pan (drag) both work smoothly.
- A corpus with fewer than 4 captures shows the friendly message, not an error.
- All existing Phase 1 functionality (search, change, delete, delete log, saved prompts) continues
  to work unaffected.

## 12. Implementation outline (to be expanded into a plan)

1. `app/graph.py` + `GRAPH_MAX_CAPTURES` in `config.py`, TDD from scratch (§8).
2. `GET /api/graph` route, TDD (§8).
3. `api.js`: add `getGraph()`.
4. Add `d3-force`, `d3-zoom`, `d3-selection` to `frontend/package.json`.
5. `KeywordGraph.jsx`: fetch + not-enough-data state + static (non-simulated) render first.
6. Wire up the `d3-force` cluster/collision simulation and `d3-zoom` pan/zoom.
7. Filter textbox, cluster legend with toggles, hover tooltips (keyword and cluster flavors).
8. `App.jsx`: three-way `view` state, new "Show keyword graph" button, mount/unmount wiring.
9. Manual end-to-end verification against the local dev stack, then the real production corpus.
10. Update `README.md` and `OpenbrainAddition.md` with the new view.
