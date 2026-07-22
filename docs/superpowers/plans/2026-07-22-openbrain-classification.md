# OpenBrain Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `classify_captures` MCP tool to `openbrain-mcp` that classifies captures into caller-supplied categories (`{"name", "example"}` pairs) by embedding similarity — zero-shot, no pre-labeled training data needed, no persistence (callers use the existing `update` tool to save a result if they want to keep it).

**Architecture:** A new function in `store.py` reads capture embeddings (optionally filtered to specific `ids`), embeds each caller-supplied category's example sentence via the existing `embed_passage`, and uses `sklearn.metrics.pairwise.cosine_similarity` to score every capture against every category, picking the best match per capture. A thin `@mcp.tool()` wrapper in `server.py` exposes it, following the normal `server → store` pattern (unlike `compute_fingerprint`, which is a documented DB-free exception). No new dependency — reuses `scikit-learn`, already added for `cluster_captures`. No schema change.

**Tech Stack:** Python 3.11, `scikit-learn` (already a dependency), `psycopg` 3 + `pgvector` (already in use), `pytest` against a real Postgres+pgvector instance (`DATABASE_URL`-gated, same as `test_store.py`'s existing suite).

**Reference:** Design spec at `docs/superpowers/specs/2026-07-22-openbrain-classification-design.md`.

---

## File structure (files touched by this plan)

```
openbrain-mcp/
  app/
    store.py     # MODIFY: add classify_captures() after cluster_captures()/_auto_select_k()
    server.py    # MODIFY: add classify_captures MCP tool after cluster_captures()
  tests/
    test_store.py  # MODIFY: add 4 tests for classify_captures
README.md         # MODIFY: add classify_captures row to the tool table, bump counts
```

---

### Task 1: Store layer — `classify_captures`

**Files:**
- Modify: `openbrain-mcp/app/store.py` (add an import; insert `classify_captures` after `_auto_select_k`, which currently ends at line 139, before `fetch_recent`)
- Modify: `openbrain-mcp/tests/test_store.py` (append after the existing tests)

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
_CLASSIFY_TOPICS = {
    "career": [
        "Sarah is considering leaving her job to start a consulting business.",
        "Sarah is considering leaving her job to start a consulting company.",
        "Sarah is considering leaving her job to start a consulting firm.",
    ],
    "cooking": [
        "A recipe for sourdough bread using a rye starter.",
        "A recipe for sourdough bread using a wheat starter.",
        "A recipe for sourdough bread using a spelt starter.",
    ],
}

_CLASSIFY_CATEGORIES = [
    {"name": "career", "example": "Someone is thinking about changing jobs or careers."},
    {"name": "cooking", "example": "A recipe or cooking technique."},
]

def _save_classify_fixture() -> dict:
    """Saves 6 captures (2 topics x 3 near-duplicate-worded captures each).
    Returns {capture_id: topic_name}."""
    id_to_topic = {}
    with get_conn() as conn:
        for topic, summaries in _CLASSIFY_TOPICS.items():
            for i, summary in enumerate(summaries):
                r = store.save_capture(
                    conn, raw_text=f"{topic}-{i}", summary=summary,
                    keywords=[topic], source="other",
                    source_url=f"https://example.com/classify-{topic}-{i}",
                )
                id_to_topic[r["id"]] = topic
    return id_to_topic

def test_classify_captures_assigns_expected_category():
    _clean()
    id_to_topic = _save_classify_fixture()
    with get_conn() as conn:
        results = store.classify_captures(conn, categories=_CLASSIFY_CATEGORIES)
    assert len(results) == 6
    by_id = {r["id"]: r for r in results}
    for capture_id, topic in id_to_topic.items():
        assert by_id[capture_id]["category"] == topic, (
            f"expected {capture_id} ({by_id[capture_id]['summary']!r}) to classify as "
            f"{topic!r}, got {by_id[capture_id]['category']!r}"
        )
        assert by_id[capture_id]["score"] > 0.0

def test_classify_captures_respects_ids_filter():
    _clean()
    id_to_topic = _save_classify_fixture()
    career_ids = [cid for cid, topic in id_to_topic.items() if topic == "career"]
    with get_conn() as conn:
        results = store.classify_captures(
            conn, categories=_CLASSIFY_CATEGORIES, ids=career_ids)
    assert {r["id"] for r in results} == set(career_ids)

def test_classify_captures_rejects_empty_categories():
    _clean()
    _save_classify_fixture()
    with get_conn() as conn:
        result = store.classify_captures(conn, categories=[])
    assert result == {"error": "categories must be a non-empty list of {name, example} dicts"}

def test_classify_captures_returns_empty_list_when_nothing_to_classify():
    _clean()
    with get_conn() as conn:
        result = store.classify_captures(conn, categories=_CLASSIFY_CATEGORIES)
    assert result == []
```

> Note on `test_classify_captures_assigns_expected_category`: this uses the same
> "vary one or two words per capture within a topic" technique as the `find_near_duplicates` and
> `cluster_captures` tests to keep within-topic embeddings tight. If this test fails with the
> *wrong* category assigned (not a missing-function error), print `by_id` and check which capture
> misclassified: narrow the topic wording or the category `example` sentences further rather than
> lowering the assertion's strictness -- the same resolution path documented in the clustering plan.

- [ ] **Step 2: Run tests to verify they fail** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v -k classify_captures`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'classify_captures'` (or `pytest.skip`'d if `DATABASE_URL` is unset — export it first, pointing at a throwaway/dev Postgres with the schema from `migrations/001_init.sql` applied).

- [ ] **Step 3: Write the implementation** **[repo]**

At the top of `openbrain-mcp/app/store.py`, change:

```python
# app/store.py
import psycopg
from psycopg.types.json import Json
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from app.keywords import normalize_keywords
from app.embeddings import embed_passage, embed_query
from app.fingerprint import content_fingerprint
```

to:

```python
# app/store.py
import psycopg
from psycopg.types.json import Json
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from app.keywords import normalize_keywords
from app.embeddings import embed_passage, embed_query
from app.fingerprint import content_fingerprint
```

Insert into `openbrain-mcp/app/store.py`, immediately after the `_auto_select_k` function (ends at line 139) and before `fetch_recent`:

```python
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

`embed_passage` is already imported at the top of `store.py` (used by `save_capture`/
`update_capture`). The `r[2].to_list()` conversion mirrors `cluster_captures`'s existing handling
of `pgvector.psycopg.Vector` rows (`app/store.py:109`).

- [ ] **Step 4: Run tests to verify they pass** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: PASS — all previous tests plus the 4 new ones (17 total in this file).

- [ ] **Step 5: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — 33 tests total (29 existing + 4 new), 0 failed.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(store): add classify_captures (zero-shot embedding classification)"
```

---

### Task 2: MCP tool — `classify_captures`

**Files:**
- Modify: `openbrain-mcp/app/server.py` (insert after `cluster_captures`, which currently ends at line 94, before `class BearerAuthMiddleware`)

- [ ] **Step 1: Add the tool** **[repo]**

Insert into `openbrain-mcp/app/server.py`, immediately after the `cluster_captures` tool function
(ends at line 94) and before `class BearerAuthMiddleware`:

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

This follows the `server → store` pattern of eight of the nine existing tools (`save`, `search`,
`list_recent`, `stats`, `delete`, `update`, `find_near_duplicates`, `cluster_captures`) —
`compute_fingerprint` is the one documented exception that bypasses `store.py`, since it needs no
DB access; this tool does need DB access, so it follows the normal pattern. No dedicated
server-level test is added, consistent with `find_near_duplicates`/`compute_fingerprint`/
`cluster_captures`: `tests/test_server.py` only covers auth/health/host-header behavior of the
Starlette app, not per-tool logic (that's `test_store.py`'s job).

- [ ] **Step 2: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — all 33 tests.

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(mcp): expose classify_captures as an MCP tool"
```

---

### Task 3: Document the new tool in `README.md`

**Files:**
- Modify: `README.md` (tool table, count references, repo-layout comments)

- [ ] **Step 1: Rename the tool-count heading and table** **[repo]**

Change the heading `## The nine MCP tools` to `## The ten MCP tools`, and add this row to the end
of the table (after the `cluster_captures` row):

```markdown
| `classify_captures(categories, ids?)` | Read-only. Classifies captures into caller-supplied `{name, example}` categories by embedding similarity (zero-shot, no pre-labeled data needed). Omit `ids` to classify everything. Persist a result via the existing `update` tool if desired — this tool writes nothing itself. |
```

- [ ] **Step 2: Update test-count references** **[repo]**

Change:
```markdown
(`openbrain-mcp/tests/`, 29 tests, mostly run against a real Postgres+pgvector instance;
`compute_fingerprint`'s tests are the exception and need no database).
```
to:
```markdown
(`openbrain-mcp/tests/`, 33 tests, mostly run against a real Postgres+pgvector instance;
`compute_fingerprint`'s tests are the exception and need no database).
```

Change:
```markdown
  tests/                     # 29 tests, pytest
```
to:
```markdown
  tests/                     # 33 tests, pytest
```

- [ ] **Step 3: Update the remaining "nine" references and repo-layout comments** **[repo]**

Change:
```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes nine tools over the
```
to:
```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes ten tools over the
```

Change:
```markdown
`http://localhost:8080/mcp` with that bearer token and call the nine tools directly — useful for
```
to:
```markdown
`http://localhost:8080/mcp` with that bearer token and call the ten tools directly — useful for
```

Change the repo-layout comment:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates/cluster_captures -- the only file with SQL
```
to:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates/cluster_captures/classify_captures -- the only file with SQL
```

Change the repo-layout comment:
```
    server.py             # the 9 MCP tools + bearer auth + /health
```
to:
```
    server.py             # the 10 MCP tools + bearer auth + /health
```

**IMPORTANT — do NOT touch this line:** the "Status" table's early Phase-2 row says
"6 MCP tools, TDD, 18 tests" — this is an intentional historical point-in-time snapshot and must
stay unchanged (same rule as the previous three capabilities).

- [ ] **Step 4: Commit** **[repo]**

```bash
git add README.md
git commit -m "docs: document classify_captures in the MCP tool table"
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

No new dependency was added in this capability (`scikit-learn` was already added for
`cluster_captures`), so this rebuild should be fast if the image from that capability is still
cached — but rebuild anyway to make sure the current source is what's running.

- [ ] **Step 2: Seed captures and call the tool from a connected MCP client** **[repo]**

From Claude Code or Claude Desktop (already configured per README Phase 6), with the local
server's bearer token:

```
"Save these 4 notes to openbrain, then call classify_captures with categories
[{\"name\": \"career\", \"example\": \"Someone is thinking about changing jobs or careers.\"},
 {\"name\": \"cooking\", \"example\": \"A recipe or cooking technique.\"}]
and tell me what comes back:
1. Sarah is considering leaving her job to start a consulting business.
2. Sarah is considering leaving her job to start a consulting company.
3. A recipe for sourdough bread using a rye starter.
4. A recipe for sourdough bread using a wheat starter."
```

Expected: a list of 4 `{id, summary, category, score}` objects — the two "Sarah" notes classified
as `"career"`, the two sourdough notes classified as `"cooking"`, each with a positive `score`.

- [ ] **Step 3: Confirm the `ids` filter and read-only behavior** **[repo]**

Run `stats`, then ask the client to call `classify_captures` again with the same `categories` but
`ids` restricted to just one of the four saved capture ids. Expected: the result contains exactly
one entry, for that capture. Then run `stats` again — the `total` count must be identical to
before this step, confirming `classify_captures` never mutates data (and, since it doesn't touch
`metadata` either, a capture's `metadata` field is untouched by this tool — there's no direct MCP
tool to inspect `metadata` today to verify this beyond code inspection, which the code review
already covers).

- [ ] **Step 4: Confirm the empty-categories error case** **[repo]**

Ask the client to call `classify_captures` with `categories=[]`. Expected:
`{"error": "categories must be a non-empty list of {name, example} dicts"}`, not a crash.

- [ ] **Step 5: Tear down any throwaway local resources** **[repo]**

If the local compose stack was brought up only for this smoke test (not already running for other
reasons), bring it back down: `docker compose -f docker-compose.openbrain.yml down`.
