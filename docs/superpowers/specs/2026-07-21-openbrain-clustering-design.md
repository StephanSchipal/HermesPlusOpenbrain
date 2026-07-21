# OpenBrain — Embedding-Based Clustering — Design Spec

**Date:** 2026-07-21
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-21)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add a **read-only** MCP tool that groups all captured notes into thematic
clusters using their existing embeddings, without requiring the user to
define categories up front. This is capability 3 of 4 planned additions to
`openbrain-mcp`, in this order: (1) embedding-based duplicate detection —
done, (2) explicit fingerprint introspection — done, (3) clustering — this
spec, (4) classification.

The tool returns structured cluster data (membership + which entries are
most representative of each cluster); it deliberately does **not** generate
human-readable cluster labels itself — that's left to whichever LLM client
called the tool (Claude Desktop/Code, or Hermes-Agent), consistent with
`openbrain-mcp` never making its own outbound LLM calls anywhere in the
codebase today.

## 2. Context & constraints

- Existing system has eight MCP tools (`save`, `search`, `list_recent`,
  `stats`, `delete`, `update`, `find_near_duplicates`, `compute_fingerprint`);
  `store.py` owns all SQL/DB access, `server.py` only wraps `store.py` calls
  as `@mcp.tool()`s — except `compute_fingerprint`, which is a documented,
  narrow exception since it needs no DB access. This new tool *does* need DB
  access (it reads every capture's embedding), so it follows the normal
  `server → store` pattern, not `compute_fingerprint`'s exception.
- No clustering library exists in the project yet (`pyproject.toml` has no
  `scikit-learn`/`numpy`/`hdbscan`). `sentence-transformers` already
  transitively depends on `numpy` via `torch`, but this feature needs
  `scikit-learn` explicitly for `KMeans` and `silhouette_score`.
- Data volume is small (single-user capture tool, same assumption capability
  1 made for its `O(n²)` self-join) — running `KMeans` multiple times (once
  per candidate `k` during auto-selection) over the full corpus is cheap at
  this scale; no need for incremental/streaming clustering.
- The `captures.embedding` column is `vector(384)`, already populated for
  every row (computed at `save` time via `embed_passage`). `pgvector.psycopg`'s
  `register_vector(conn)` (already called in `app/db.py`) means reading the
  `embedding` column back from a `SELECT` yields the vector as a value
  `scikit-learn` can consume directly — no manual parsing needed.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Labeling | **Tool returns raw cluster data only; no LLM call inside `openbrain-mcp`** | The calling client is already an LLM (that's the whole point of MCP) — it can generate a label from the returned summaries itself. Adding an outbound LLM call inside `openbrain-mcp` would require its own API key, secrets management, cost, and new failure modes, breaking the project's existing pattern of `openbrain-mcp` never calling out to another LLM. |
| Cluster count (`k`) | **Optional parameter; auto-selected via silhouette score when omitted** | User explicitly chose "both": callers who know what they want can pass `k` directly (same pattern as `threshold`/`limit` on `find_near_duplicates`), but the useful default doesn't require the caller to already know how many topics exist in their notes. |
| Auto-`k` method | **Try `k` = 2..min(10, n-1), pick the `k` with the best `silhouette_score`** | Standard, well-understood technique; bounded search space (max 9 `KMeans` fits) keeps it cheap at this project's data scale. Upper bound of 10 is a fixed internal constant, not a parameter — YAGNI; can be exposed later if ever needed. |
| Persistence | **None — pure read, no DB writes** | Consistent with `find_near_duplicates` and `compute_fingerprint`. Persisting a cluster assignment (e.g. into `metadata`) would overlap with capability 4 (classification), which already owns the "read/write `metadata`" scope — keeping clustering read-only avoids a scope collision between the two capabilities. |
| Cluster membership in response | **Full membership (every member's `id`+`summary`) per cluster, not just a sample** | Matches `find_near_duplicates`'s precedent of returning full data rather than a truncated sample. Each member additionally carries a `central: bool` flag (true for the 3 members nearest their cluster's centroid) so the calling LLM knows which entries best represent the cluster's theme without needing a second tool call. |
| Distance computation | **`KMeans.transform()`**, not manual NumPy | `scikit-learn`'s fitted `KMeans` model already exposes `.transform(X)` returning each sample's distance to every cluster centroid — reuses the library instead of duplicating linear-algebra code. |

## 4. Design

### 4.1 `pyproject.toml`

Add to `dependencies`:
```
"scikit-learn>=1.4.0",
```

### 4.2 `store.py`

```python
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

_MIN_CAPTURES_TO_CLUSTER = 4
_MAX_AUTO_K = 10

def cluster_captures(conn: psycopg.Connection, *, k: int | None = None) -> dict:
    """Group all captures into k thematic clusters by embedding similarity.
    Read-only -- returns cluster membership and which entries are most
    representative of each cluster; does not generate cluster labels itself
    (leaves that to the calling LLM client) and writes nothing to the DB."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, summary, embedding FROM captures")
        rows = cur.fetchall()

    if len(rows) < _MIN_CAPTURES_TO_CLUSTER:
        return {"error": f"need at least {_MIN_CAPTURES_TO_CLUSTER} captures to cluster, have {len(rows)}"}

    ids = [str(r[0]) for r in rows]
    summaries = [r[1] for r in rows]
    embeddings = [r[2] for r in rows]

    if k is None:
        k = _auto_select_k(embeddings)

    model = KMeans(n_clusters=k, random_state=0, n_init=10).fit(embeddings)
    distances = model.transform(embeddings)  # shape (n, k): distance to every centroid

    clusters = []
    for cluster_id in range(k):
        member_indices = [i for i, label in enumerate(model.labels_) if label == cluster_id]
        member_indices.sort(key=lambda i: distances[i][cluster_id])  # nearest-to-centroid first
        central_ids = {ids[i] for i in member_indices[:3]}
        members = [
            {"id": ids[i], "summary": summaries[i], "central": ids[i] in central_ids}
            for i in member_indices
        ]
        clusters.append({"cluster_id": cluster_id, "size": len(members), "members": members})

    return {"k": k, "clusters": clusters}

def _auto_select_k(embeddings: list, max_k: int = _MAX_AUTO_K) -> int:
    upper = min(max_k, len(embeddings) - 1)
    best_k, best_score = 2, -1.0
    for candidate_k in range(2, upper + 1):
        labels = KMeans(n_clusters=candidate_k, random_state=0, n_init=10).fit_predict(embeddings)
        score = silhouette_score(embeddings, labels)
        if score > best_score:
            best_k, best_score = candidate_k, score
    return best_k
```

### 4.3 `server.py`

```python
@mcp.tool()
def cluster_captures(k: int | None = None) -> dict:
    """Group all captures into thematic clusters by embedding similarity.
    Read-only. If k is omitted, the number of clusters is chosen
    automatically via silhouette score. Returns each cluster's full
    membership (id + summary), with a `central` flag marking the 3 entries
    closest to that cluster's centroid -- use those to label the cluster's
    theme, since this tool does not generate labels itself."""
    with get_conn() as conn:
        return store.cluster_captures(conn, k=k)
```

### 4.4 Test plan (TDD, real Postgres+pgvector, following `tests/test_store.py` conventions)

1. `test_cluster_captures_with_explicit_k_groups_by_theme` — save 3 tightly-
   worded captures per topic across 3 distinct topics (9 total; reuse the
   "near-duplicate wording within a topic" trick from capability 1's test to
   keep within-group similarity high and cross-group similarity low), call
   with `k=3` → exactly 3 clusters returned, each with `size == 3`, and each
   cluster's members all belong to the same topic (checked via a keyword
   unique to that topic appearing in every member's summary).
2. `test_cluster_captures_auto_k_picks_a_reasonable_cluster_count` — same 9
   captures, `k=None` → returned `k` is between 2 and 9 inclusive, and the
   same per-cluster topic-purity check as test 1 holds for whatever grouping
   was chosen (not asserting `k == 3` exactly, to avoid over-fitting the test
   to this embedding model's specific silhouette behavior — see note below).
3. `test_cluster_captures_marks_centroid_nearest_members_as_central` — using
   the same fixture, for each cluster returned, exactly `min(3, size)`
   members have `central: true`, and (spot-checked for one cluster) they are
   the 3 members with the smallest actual distance to that cluster's mean
   embedding.
4. `test_cluster_captures_reports_error_below_minimum` — save only 2
   captures total, call `cluster_captures` → returns
   `{"error": "need at least 4 captures to cluster, have 2"}`, no exception
   raised.

> Note on auto-`k` test flakiness risk: like capability 1's near-duplicate
> test, this depends on `intfloat/multilingual-e5-small`'s actual embedding
> behavior, not just the algorithm. Keep the 3 topics' within-group wording
> as close to identical as possible (varying only 1-2 words per capture, the
> same technique the "Sarah" test used) to maximize between-group
> separation and keep the silhouette-based `k` selection stable. If test 2
> proves flaky in practice, loosen its assertion further (e.g. just assert
> `k >= 2` and skip the topic-purity check) rather than lowering
> `_MIN_CAPTURES_TO_CLUSTER` or changing the algorithm.

## 5. Non-goals (this iteration)

- No LLM call inside `openbrain-mcp` to generate cluster labels.
- No persistence of cluster assignments (no `metadata` writes) — deferred to
  capability 4's scope if ever wanted.
- No `max_k` parameter — the auto-selection upper bound (10) is an internal
  constant.
- No incremental/streaming clustering — recomputed from scratch on every
  call, consistent with `find_near_duplicates`'s full-corpus-scan approach.
- No change to any existing tool or to the `captures` schema.

## 6. Success criteria

- `cluster_captures(k=3)` on a corpus with 3 well-separated topic groups
  returns exactly 3 clusters, each internally coherent by topic.
- `cluster_captures()` (auto-`k`) on the same corpus returns a sensible `k`
  or reasonable groupings without crashing.
- Fewer than 4 captures in the DB → a clear error dict, not an exception.
- `central` flags correctly identify the (up to) 3 centroid-nearest members
  per cluster.
- All four new tests pass against a real Postgres+pgvector instance
  alongside the existing 24 tests (no regressions).
- Manual smoke test confirms the tool is read-only (capture count unchanged
  before/after) against a live local Docker Compose deployment.

## 7. Implementation outline (to be expanded into a plan)

1. Add `scikit-learn` to `pyproject.toml`.
2. Add `cluster_captures` and `_auto_select_k` to `store.py`.
3. Add the four tests to `tests/test_store.py`; run against
   `DATABASE_URL`-configured Postgres.
4. Add the `cluster_captures` MCP tool to `server.py`.
5. Update `README.md`'s tool table.
6. Manual smoke test via a laptop MCP client (Claude Desktop/Code) against
   the local compose stack, confirming read-only behavior.
