# OpenBrain Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `find_near_duplicates` MCP tool to `openbrain-mcp` that surfaces pairs of captures whose *meaning* is near-identical (cosine similarity above a threshold), catching the near-duplicates that the existing exact-fingerprint dedup in `save` can't.

**Architecture:** A new function in `store.py` runs a self-join over the `captures` table using pgvector's `<=>` cosine-distance operator (`a.id < b.id` to avoid mirrored/self pairs), returning scored pairs above a threshold. A thin `@mcp.tool()` wrapper in `server.py` exposes it, following the exact pattern of the six existing tools. No schema change — reuses the existing `embedding` column and its HNSW index.

**Tech Stack:** Python 3.11, `psycopg` 3 + `pgvector` (already in use), `pytest` against a real Postgres+pgvector instance (`DATABASE_URL`-gated, same as the existing `test_store.py` suite).

**Reference:** Design spec at `docs/superpowers/specs/2026-07-20-openbrain-duplicate-detection-design.md`.

---

## File structure (files touched by this plan)

```
openbrain-mcp/
  app/
    store.py     # MODIFY: add find_near_duplicates() after search_captures()
    server.py    # MODIFY: add find_near_duplicates MCP tool after update()
  tests/
    test_store.py  # MODIFY: add 3 tests for find_near_duplicates
README.md         # MODIFY: add find_near_duplicates row to the tool table
```

---

### Task 1: Store layer — `find_near_duplicates`

**Files:**
- Modify: `openbrain-mcp/app/store.py` (insert after `search_captures`, which currently ends at line 61, before `fetch_recent`)
- Modify: `openbrain-mcp/tests/test_store.py` (append after the existing tests)

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
def test_find_near_duplicates_detects_similar_but_not_identical_summaries():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a",
                           summary="Sarah is considering leaving her job to start a consulting business.",
                           keywords=["career"], source="other")
        store.save_capture(conn, raw_text="b",
                           summary="Sarah is considering leaving her job to start a consulting company.",
                           keywords=["career"], source="other")
        store.save_capture(conn, raw_text="c", summary="A recipe for sourdough bread.",
                           keywords=["cooking"], source="other")
    with get_conn() as conn:
        pairs = store.find_near_duplicates(conn, limit=10)  # default threshold: 0.95
    assert len(pairs) == 1, f"expected exactly the Sarah/Sarah pair, got {pairs}"
    top = pairs[0]
    assert "sourdough" not in top["summary_a"] and "sourdough" not in top["summary_b"]
    assert "Sarah" in top["summary_a"] and "Sarah" in top["summary_b"]
    assert 0.95 < top["similarity"] <= 1.0

def test_find_near_duplicates_respects_threshold():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="A talk about memory systems.",
                           keywords=["memory"], source="other")
        store.save_capture(conn, raw_text="b", summary="A recipe for sourdough bread.",
                           keywords=["cooking"], source="other")
    with get_conn() as conn:
        # cosine similarity is capped at 1.0 -- no pair can ever clear this bar
        pairs = store.find_near_duplicates(conn, threshold=1.01, limit=10)
    assert pairs == []

def test_find_near_duplicates_respects_limit():
    _clean()
    with get_conn() as conn:
        for i in range(4):
            store.save_capture(
                conn, raw_text=f"t{i}",
                summary="Meeting notes: discussed the Q3 budget with the finance team.",
                keywords=["meeting"], source="other",
                source_url=f"https://example.com/meeting-{i}",
            )
    with get_conn() as conn:
        # 4 identical-summary captures -> C(4,2) = 6 qualifying pairs at threshold 0.0
        pairs = store.find_near_duplicates(conn, threshold=0.0, limit=3)
    assert len(pairs) == 3
```

> Note on `test_find_near_duplicates_detects_similar_but_not_identical_summaries`: the two
> "Sarah" summaries differ by exactly one word (`business` vs. `company`) specifically so their
> cosine similarity clears the default `0.95` threshold with margin — this is a best-effort
> estimate of what `intfloat/multilingual-e5-small` actually scores them at. This project's plan
> history (see `2026-06-30-hermes-openbrain-memory.md`, revision entries) shows e5 behavior has
> needed empirical correction before. If this test fails Step 2/4 with the *wrong* pair count
> (not a missing-function error), print `top["similarity"]` for the actual score: if it's below
> `0.95`, narrow the wording difference further (e.g. change only a single character) rather
> than lowering the threshold — the test is meant to validate the documented *default*, and
> `test_find_near_duplicates_respects_threshold` already covers threshold filtering separately.

- [ ] **Step 2: Run tests to verify they fail** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v -k find_near_duplicates`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'find_near_duplicates'` (or `pytest.skip`'d if `DATABASE_URL` is unset — export it first, pointing at a throwaway/dev Postgres with the schema from `migrations/001_init.sql` applied).

- [ ] **Step 3: Write the implementation** **[repo]**

Insert into `openbrain-mcp/app/store.py`, immediately after the `search_captures` function (which ends at line 61) and before `fetch_recent`:

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

No new imports needed — `psycopg` is already imported at the top of `store.py`.

- [ ] **Step 4: Run tests to verify they pass** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: PASS — all previous tests plus the 3 new ones (8 total in this file).

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(store): add find_near_duplicates (embedding-based dedup scan)"
```

---

### Task 2: MCP tool — `find_near_duplicates`

**Files:**
- Modify: `openbrain-mcp/app/server.py` (insert after `update`, which currently ends at line 63, before `class BearerAuthMiddleware`)

- [ ] **Step 1: Add the tool** **[repo]**

Insert into `openbrain-mcp/app/server.py`, immediately after the `update` tool function (ends at line 63) and before `class BearerAuthMiddleware`:

```python
@mcp.tool()
def find_near_duplicates(threshold: float = 0.95, limit: int = 50) -> list[dict]:
    """Find near-duplicate capture pairs by embedding similarity (cosine).
    Read-only -- use the existing `delete` tool to remove one side of a pair."""
    with get_conn() as conn:
        return store.find_near_duplicates(conn, threshold=threshold, limit=limit)
```

This follows the exact wrapping pattern of the six existing tools (e.g. `search`, `update`) —
no dedicated server-level test is added, consistent with those tools: `tests/test_server.py`
only covers auth/health/host-header behavior of the Starlette app, not per-tool logic (that's
`test_store.py`'s job).

- [ ] **Step 2: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — all tests, including `tests/test_server.py`'s auth/health tests (unaffected by this change) and the 8 tests in `tests/test_store.py`.

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(mcp): expose find_near_duplicates as an MCP tool"
```

---

### Task 3: Document the new tool in `README.md`

**Files:**
- Modify: `README.md` (the "The six MCP tools" table, around line 120-127)

- [ ] **Step 1: Add a row to the tool table and rename the section** **[repo]**

Change the heading `## The six MCP tools` to `## The seven MCP tools`, and add this row to the
end of the table (after the `update` row):

```markdown
| `find_near_duplicates(threshold=0.95, limit=50)` | Read-only. Lists capture pairs whose summaries are near-duplicates by embedding cosine similarity — catches near-duplicates the exact-fingerprint dedup in `save` misses. Delete one side of a pair via the existing `delete` tool. |
```

Also update the sentence just below the table that currently reads:

```markdown
All except `save`/`update`'s pass-through of `metadata` are exercised by the test suite
(`openbrain-mcp/tests/`, 18 tests, run against a real Postgres+pgvector instance).
```

to:

```markdown
All except `save`/`update`'s pass-through of `metadata` are exercised by the test suite
(`openbrain-mcp/tests/`, 21 tests, run against a real Postgres+pgvector instance).
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add README.md
git commit -m "docs: document find_near_duplicates in the MCP tool table"
```

---

### Task 4: Manual smoke test against the local compose stack

**Files:** none (verification only)

- [ ] **Step 1: Bring up the local stack** **[repo]**

Follow the existing "Running it locally" section of `README.md` if it isn't already running:

```bash
cd deploy
docker compose -f docker-compose.openbrain.yml up -d --build
docker compose -f docker-compose.openbrain.yml ps   # both services should show (healthy)
```

- [ ] **Step 2: Seed two near-duplicate captures and one unrelated capture via `psql`** **[repo]**

This directly exercises the new tool's SQL against live data without needing a full MCP client
round-trip:

```bash
docker compose -f docker-compose.openbrain.yml exec openbrain-db \
  psql -U openbrain -d openbrain -c \
  "SELECT count(*) FROM captures;"
```

(If the table is non-empty from earlier manual testing, note the count — this step is just
confirming connectivity before the next one.)

- [ ] **Step 3: Call the tool from a connected MCP client** **[repo]**

From Claude Code or Claude Desktop (already configured per README Phase 6), with the local
server's bearer token, ask:

```
"Use openbrain's find_near_duplicates tool with default settings and tell me what it finds."
```

Expected: either an empty list (if no near-duplicates exist yet in the local DB) or a list of
`{id_a, summary_a, id_b, summary_b, similarity}` objects. If the corpus is currently empty,
first `save` two deliberately similar notes (e.g. reuse the "Sarah" wording from Task 1's test)
via the same client, then re-run the `find_near_duplicates` request and confirm the pair shows
up.

- [ ] **Step 4: Confirm read-only behavior** **[repo]**

Run `stats` before and after the `find_near_duplicates` call above; the `total` count must be
unchanged — the new tool must never mutate data.
