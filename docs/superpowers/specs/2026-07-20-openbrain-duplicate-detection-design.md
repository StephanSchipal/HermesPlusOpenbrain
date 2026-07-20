# OpenBrain — Embedding-Based Duplicate Detection — Design Spec

**Date:** 2026-07-20
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-19/20)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add a **read-only** MCP tool that finds pairs of captures whose *meaning* is
near-duplicate, even when their wording differs enough that the existing
`fingerprint` dedup (exact URL/text match, computed in `save`) doesn't catch
them.

This is capability 1 of 4 planned additions to `openbrain-mcp`, in this
order: (1) embedding-based duplicate detection — this spec, (2) an explicit
fingerprint/duplicate-check tool, (3) clustering, (4) classification.

## 2. Context & constraints

- Existing system is complete and live (see
  [`2026-06-30-hermes-openbrain-memory-design.md`](2026-06-30-hermes-openbrain-memory-design.md)):
  six MCP tools (`save`, `search`, `list_recent`, `stats`, `delete`,
  `update`), `store.py` is the only file with SQL, `server.py` only wraps
  `store.py` calls as `@mcp.tool()`s.
- `fingerprint` dedup (SHA-256 of normalized URL or text) already prevents
  *exact* duplicates at `save` time — this feature is complementary, not a
  replacement: it catches near-duplicates that already made it into the
  table with different wording (e.g. two manually-typed notes about the same
  event, or a link sent once as a URL and once as pasted text).
- Data volume is small (single-user capture tool) — an `O(n²)` self-join is
  acceptable; no need for an HNSW-based nearest-neighbor approach at this
  scale.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Scope | **Full-corpus pairwise scan**, not a single-id lookup | The use case is periodic cleanup ("show me what's redundant"), not "is this new note a duplicate of something specific." |
| Mutation | **Read-only** — reports pairs + similarity score only | Keeps blast radius zero; deleting is a deliberate, separate action via the existing `delete(id)` tool. No auto-merge. |
| Default threshold | **0.95** cosine similarity | High enough to avoid false positives on merely-related notes; adjustable per call via a parameter. |
| Implementation | **SQL self-join in `store.py`** (`a.id < b.id` to avoid mirrored pairs and self-pairs) | Matches the existing pattern — `store.py` owns all SQL; reuses the same `<=>` operator and score convention (`1 - distance`) as `search_captures`. Rejected: Python/NumPy pairwise comparison (pulls all embeddings into the app, unnecessary given pgvector can do it in-database) and per-row HNSW nearest-neighbor lookups (real complexity win only at a scale this project doesn't have). |
| Validation | **None added** — `threshold`/`limit` passed straight to SQL | Consistent with `search`'s `k` parameter, which is likewise unvalidated. An out-of-range threshold just yields an empty or unbounded-by-threshold result, not an error. |

## 4. Design

### 4.1 `store.py`

```python
def find_near_duplicates(conn: psycopg.Connection, *, threshold: float = 0.95,
                         limit: int = 50) -> list[dict]:
    """Pairs of captures whose summaries are near-duplicates by cosine similarity.
    Read-only -- caller decides what to do (e.g. the existing `delete` tool)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.summary, b.id, b.summary,
                   1 - (a.embedding <=> b.embedding) AS similarity
            FROM captures a
            JOIN captures b ON a.id < b.id
            WHERE 1 - (a.embedding <=> b.embedding) > %s
            ORDER BY similarity DESC
            LIMIT %s
            """,
            (threshold, limit),
        )
        rows = cur.fetchall()
    return [
        {"id_a": str(r[0]), "summary_a": r[1], "id_b": str(r[2]), "summary_b": r[3],
         "similarity": float(r[4])}
        for r in rows
    ]
```

### 4.2 `server.py`

```python
@mcp.tool()
def find_near_duplicates(threshold: float = 0.95, limit: int = 50) -> list[dict]:
    """Find near-duplicate capture pairs by embedding similarity (cosine).
    Read-only -- use the existing `delete` tool to remove one side of a pair."""
    with get_conn() as conn:
        return store.find_near_duplicates(conn, threshold=threshold, limit=limit)
```

No schema change, no migration — reuses the existing `embedding` column and
`hnsw`/cosine index already present on `captures`.

### 4.3 Test plan (TDD, real Postgres+pgvector, following `tests/test_store.py` conventions)

1. `test_find_near_duplicates_detects_similar_but_not_identical_summaries` —
   save two captures with near-identical meaning (different wording) plus
   one unrelated capture → exactly one pair returned, unrelated capture
   doesn't appear in either side.
2. `test_find_near_duplicates_respects_threshold` — two dissimilar captures,
   high threshold → empty list.
3. `test_find_near_duplicates_respects_limit` — more qualifying pairs than
   `limit` → result length capped at `limit`.

## 5. Non-goals (this iteration)

- No auto-delete/auto-merge of detected duplicates.
- No UI/dashboard for reviewing pairs — consumed via the MCP tool directly
  (WhatsApp/Hermes or Claude Desktop/Code), same as the six existing tools.
- No change to `save`'s exact-fingerprint dedup behavior.
- No indexing/performance work beyond the existing HNSW index — deferred
  until data volume actually warrants it.

## 6. Success criteria

- `find_near_duplicates()` on a corpus with two near-identical (but not
  identical) summaries and N unrelated ones returns exactly the near-
  identical pair at the default threshold.
- Raising the threshold above the actual similarity of a pair excludes it.
- `limit` caps the number of returned pairs.
- All three new tests pass against a real Postgres+pgvector instance
  alongside the existing 18 tests (no regressions).

## 7. Implementation outline (to be expanded into a plan)

1. Add `find_near_duplicates` to `store.py`.
2. Add the three tests to `tests/test_store.py`; run against
   `DATABASE_URL`-configured Postgres.
3. Add the `find_near_duplicates` MCP tool to `server.py`.
4. Update `README.md`'s tool table.
5. Manual smoke test via a laptop MCP client (Claude Desktop/Code) against
   the local compose stack.
