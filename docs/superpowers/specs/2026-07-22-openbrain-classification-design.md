# OpenBrain — Zero-Shot Classification — Design Spec

**Date:** 2026-07-22
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-22)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add a **read-only** MCP tool that classifies captures into caller-supplied
categories by embedding similarity, without requiring any pre-labeled
training data. This is capability 4 of 4 planned additions to
`openbrain-mcp`, in this order: (1) embedding-based duplicate detection —
done, (2) explicit fingerprint introspection — done, (3) clustering — done,
(4) classification — this spec. This is the last of the originally-scoped
four capabilities.

The tool returns, per capture, the best-matching category and a similarity
score; it does **not** persist the result anywhere. A caller who wants to
keep a classification result calls the already-existing `update(id,
metadata={...})` tool separately — no new persistence logic is needed for
that.

## 2. Context & constraints

- Existing system has nine MCP tools (`save`, `search`, `list_recent`,
  `stats`, `delete`, `update`, `find_near_duplicates`, `compute_fingerprint`,
  `cluster_captures`); `store.py` owns all SQL/DB access, `server.py` only
  wraps `store.py` calls as `@mcp.tool()`s — except `compute_fingerprint`,
  a documented exception since it needs no DB access. This new tool *does*
  need DB access (it reads capture embeddings), so it follows the normal
  `server → store` pattern.
- `scikit-learn` is already a dependency (added for capability 3) — this
  feature reuses `sklearn.metrics.pairwise.cosine_similarity` rather than
  adding a new library or hand-rolling dot-product math.
- `captures.metadata` (`jsonb`) is currently write-only: `save`/`update`
  persist it, but no tool selects it back (`search`/`list_recent` don't
  include it in their `SELECT`, there's no get-by-id tool). This capability
  does **not** change that — `classify_captures` neither reads nor writes
  `metadata`; it only compares capture embeddings to caller-supplied
  category-example embeddings. Making `metadata` queryable (e.g. to later
  filter "show me all my Entscheidung notes") is an explicitly deferred,
  separate concern, not part of this spec.
- Every capture's `summary` embedding already exists (`embed_passage` at
  `save` time). Category examples are embedded the same way
  (`embed_passage`, not `embed_query`) for symmetry — both sides of the
  comparison are "document-like" text being compared to each other, the
  same reasoning `find_near_duplicates` uses when comparing two capture
  embeddings directly (as opposed to `search`'s asymmetric query→passage
  comparison).

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Classification method | **Zero-shot**: similarity to one caller-supplied example sentence per category | Works immediately on the existing corpus with no pre-labeled data required, unlike a nearest-neighbor-among-already-categorized approach (which has a bootstrapping/cold-start problem). User explicitly chose this over nearest-neighbor or "both". |
| Category source | **Caller-supplied per call**, not hardcoded | Consistent with `cluster_captures`'s philosophy: the tool does the embedding math, the calling LLM client brings the domain knowledge (here: category names + example sentences). Keeps `openbrain-mcp` domain-agnostic — no code change needed to add/rename/remove a category. |
| Persistence | **None — pure read, no DB writes** | Consistent with all three prior capabilities. Persisting a classification is a separate, already-solved problem: the existing `update(id, metadata={"category": ...})` tool. Keeping `classify_captures` read-only avoids introducing this project's first mutating capability-rollout tool, and the batch-partial-failure questions that would come with it. |
| Metadata read-back | **Out of scope for this capability** | `classify_captures` itself needs no `metadata` access at all (it only compares embeddings). Whether/how `metadata` becomes queryable later (for filtering by a persisted category) is a separate, deferred concern — not needed to make this tool useful on its own. |
| Scope of captures classified | **Optional `ids` filter; defaults to the full corpus** | Mirrors `cluster_captures`'s "no filter = everything" default, while still supporting "classify just this one specific note" without adding date-range parsing complexity for a "last 30 days" style filter. |
| Similarity computation | **`sklearn.metrics.pairwise.cosine_similarity`** over a `(captures × categories)` matrix, then per-row argmax | Reuses the `scikit-learn` dependency already introduced for `cluster_captures` rather than adding a new library or hand-rolling normalized dot products. |

## 4. Design

### 4.1 `store.py`

```python
from sklearn.metrics.pairwise import cosine_similarity

def classify_captures(conn: psycopg.Connection, *, categories: list[dict],
                      ids: list[str] | None = None) -> list[dict] | dict:
    """Classify captures into caller-supplied categories by embedding
    similarity to one example sentence per category. Read-only -- does not
    write to metadata; use the existing `update` tool to persist a result.

    categories: [{"name": str, "example": str}, ...], at least one required.
    ids: optional subset of capture ids to classify; omit to classify all.
    """
    if not categories:
        return {"error": "categories must be a non-empty list of {name, example} dicts"}

    query = "SELECT id, summary, embedding FROM captures"
    params: tuple = ()
    if ids is not None:
        query += " WHERE id = ANY(%s::uuid[])"
        params = (ids,)
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        return []

    capture_embeddings = [r[2].to_list() for r in rows]
    category_names = [c["name"] for c in categories]
    category_embeddings = [embed_passage(c["example"]) for c in categories]

    sims = cosine_similarity(capture_embeddings, category_embeddings)

    results = []
    for i, row in enumerate(rows):
        best_idx = max(range(len(categories)), key=lambda j: sims[i][j])
        results.append({
            "id": str(row[0]),
            "summary": row[1],
            "category": category_names[best_idx],
            "score": float(sims[i][best_idx]),
        })
    return results
```

`embed_passage` is already imported at the top of `store.py` (used by
`save_capture`/`update_capture`). The `r[2].to_list()` conversion mirrors
`cluster_captures`'s existing handling of `pgvector.psycopg.Vector` rows.

### 4.2 `server.py`

```python
@mcp.tool()
def classify_captures(categories: list[dict], ids: list[str] | None = None) -> list[dict] | dict:
    """Classify captures into caller-supplied categories by embedding
    similarity. Each category is {"name": str, "example": str} -- provide
    at least one. Read-only: does not persist the result. To keep a
    classification, call `update(id, metadata={"category": ...})`
    separately. Omit `ids` to classify every capture, or pass specific ids
    to classify only those."""
    with get_conn() as conn:
        return store.classify_captures(conn, categories=categories, ids=ids)
```

### 4.3 Test plan (TDD, real Postgres+pgvector, following `tests/test_store.py` conventions)

1. `test_classify_captures_assigns_expected_category` — save captures from
   2-3 clearly distinct topics (reusing the near-duplicate-wording fixture
   pattern from capabilities 1/3 for topic separation), call with matching
   category examples for each topic → every capture's returned `category`
   matches its actual topic, and `score` is a float in a sane range
   (`> 0`, since `embed_passage` output is normalized so cosine similarity
   to a same-topic example should be clearly positive).
2. `test_classify_captures_respects_ids_filter` — same fixture, call with
   `ids` limited to a subset → only those captures appear in the result,
   in no particular guaranteed order requirement (assert by comparing
   returned id set, not list equality).
3. `test_classify_captures_rejects_empty_categories` — call with
   `categories=[]` → returns `{"error": "categories must be a non-empty
   list of {name, example} dicts"}`, no exception.
4. `test_classify_captures_returns_empty_list_when_nothing_to_classify` —
   empty DB (or `ids` referencing nothing), valid categories → returns
   `[]`, not an error.

> Note on topic-fixture reuse: the same "vary one or two words per capture
> within a topic" technique used in capabilities 1 and 3 keeps within-topic
> embeddings tight and reduces the risk of a capture's nearest category
> example being a different topic's example by chance. If test 1 proves
> flaky, narrow the wording further per topic rather than lowering the
> assertion's strictness — the same resolution path documented in the
> clustering spec.

## 5. Non-goals (this iteration)

- No nearest-neighbor-among-already-categorized classification mode.
- No hardcoded/built-in category list.
- No writes to `metadata` (or anywhere) — persistence is the caller's job
  via the existing `update` tool.
- No `metadata` read-back / filter-by-category support added to `search`,
  `list_recent`, or any other existing tool.
- No date-range ("last 30 days") filter — only an explicit `ids` list.
- No validation of category dict shape beyond "non-empty list" — a
  malformed `{name, example}` dict (missing key) raises a natural
  `KeyError` rather than a custom friendly error, consistent with this
  project's general "don't over-validate caller-supplied structure" stance
  outside of the one case (`cluster_captures`'s `k`) where a wrong value
  was specifically likely and worth a clean error message.

## 6. Success criteria

- `classify_captures(categories=[...])` on a corpus with clearly distinct
  topics assigns each capture to its actual topic's category.
- The `ids` filter correctly restricts which captures are classified.
- An empty `categories` list returns a clear error dict, not an exception.
- No captures to classify (empty DB or empty-matching `ids`) returns `[]`.
- All four new tests pass against a real Postgres+pgvector instance
  alongside the existing 29 tests (no regressions).
- Manual smoke test confirms the tool is read-only (capture count and
  `metadata` unchanged before/after) against a live local Docker Compose
  deployment.

## 7. Implementation outline (to be expanded into a plan)

1. Add `classify_captures` to `store.py` (no new dependency — reuses
   `scikit-learn`, already present).
2. Add the four tests to `tests/test_store.py`; run against
   `DATABASE_URL`-configured Postgres.
3. Add the `classify_captures` MCP tool to `server.py`.
4. Update `README.md`'s tool table.
5. Manual smoke test via a laptop MCP client (Claude Desktop/Code) against
   the local compose stack, confirming read-only behavior.
