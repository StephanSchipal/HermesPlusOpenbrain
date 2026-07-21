# OpenBrain Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `cluster_captures` MCP tool to `openbrain-mcp` that groups all captures into thematic clusters by embedding similarity (k-Means), with an optional explicit `k` and a silhouette-score-based automatic `k` when omitted — returning full cluster membership with a `central` flag marking the most representative entries, and leaving label generation to the calling LLM client.

**Architecture:** A new function in `store.py` reads every capture's `id`/`summary`/`embedding`, runs `sklearn.cluster.KMeans` (once if `k` is given, or once per candidate `k` during auto-selection via `silhouette_score`), and uses the fitted model's `.transform()` to rank each cluster's members by distance to their centroid. A thin `@mcp.tool()` wrapper in `server.py` exposes it, following the `server → store` pattern used by six of the seven existing tools (all except `compute_fingerprint`, which is a documented DB-free exception). No schema change.

**Tech Stack:** Python 3.11, `scikit-learn` (new dependency — `KMeans` + `silhouette_score`), `psycopg` 3 + `pgvector` (already in use), `pytest` against a real Postgres+pgvector instance (`DATABASE_URL`-gated, same as `test_store.py`'s existing suite).

**Reference:** Design spec at `docs/superpowers/specs/2026-07-21-openbrain-clustering-design.md`.

---

## File structure (files touched by this plan)

```
openbrain-mcp/
  pyproject.toml   # MODIFY: add scikit-learn dependency
  app/
    store.py       # MODIFY: add cluster_captures() + _auto_select_k() after find_near_duplicates()
    server.py      # MODIFY: add cluster_captures MCP tool after compute_fingerprint()
  tests/
    test_store.py  # MODIFY: add cluster-fixture helper + 4 tests for cluster_captures
README.md          # MODIFY: add cluster_captures row to the tool table
```

---

### Task 1: Store layer — `cluster_captures`

**Files:**
- Modify: `openbrain-mcp/pyproject.toml` (add `scikit-learn` to `dependencies`)
- Modify: `openbrain-mcp/app/store.py` (add imports; insert `cluster_captures`/`_auto_select_k` after `find_near_duplicates`, which currently ends at line 85, before `fetch_recent`)
- Modify: `openbrain-mcp/tests/test_store.py` (append after the existing tests, which end at line 129)

- [ ] **Step 1: Add the new dependency** **[repo]**

In `openbrain-mcp/pyproject.toml`, change:

```toml
dependencies = [
    "mcp>=1.2.0",
    "sentence-transformers>=3.0.0",
    "psycopg[binary]>=3.2.0",
    "pgvector>=0.3.0",
    "uvicorn>=0.30.0",
    "starlette>=0.37.0",
]
```

to:

```toml
dependencies = [
    "mcp>=1.2.0",
    "sentence-transformers>=3.0.0",
    "psycopg[binary]>=3.2.0",
    "pgvector>=0.3.0",
    "uvicorn>=0.30.0",
    "starlette>=0.37.0",
    "scikit-learn>=1.4.0",
]
```

Then install it into the environment: `cd openbrain-mcp && pip install -e .`

- [ ] **Step 2: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
_CLUSTER_TOPICS = {
    "career": [
        "Sarah is considering leaving her job to start a consulting business.",
        "Sarah is considering leaving her job to start a consulting company.",
        "Sarah is considering leaving her job to start a consulting firm.",
        "Sarah is considering leaving her job to start a consulting practice.",
        "Sarah is considering leaving her job to start a consulting agency.",
    ],
    "cooking": [
        "A recipe for sourdough bread using a rye starter.",
        "A recipe for sourdough bread using a wheat starter.",
        "A recipe for sourdough bread using a spelt starter.",
        "A recipe for sourdough bread using an einkorn starter.",
        "A recipe for sourdough bread using a kamut starter.",
    ],
    "meeting": [
        "Meeting notes: discussed the Q3 budget with the finance team.",
        "Meeting notes: discussed the Q3 forecast with the finance team.",
        "Meeting notes: discussed the Q3 roadmap with the finance team.",
        "Meeting notes: discussed the Q3 headcount with the finance team.",
        "Meeting notes: discussed the Q3 timeline with the finance team.",
    ],
}

def _save_topic_fixture() -> dict:
    """Saves 15 captures (3 topics x 5 near-duplicate-worded captures each,
    same 'vary one word' technique as the find_near_duplicates tests, to keep
    within-topic similarity high and between-topic similarity low).
    Returns {capture_id: topic_name}."""
    id_to_topic = {}
    with get_conn() as conn:
        for topic, summaries in _CLUSTER_TOPICS.items():
            for i, summary in enumerate(summaries):
                r = store.save_capture(
                    conn, raw_text=f"{topic}-{i}", summary=summary,
                    keywords=[topic], source="other",
                    source_url=f"https://example.com/{topic}-{i}",
                )
                id_to_topic[r["id"]] = topic
    return id_to_topic

def _assert_clusters_are_topic_pure(clusters, id_to_topic):
    for cluster in clusters:
        topics_in_cluster = {id_to_topic[m["id"]] for m in cluster["members"]}
        assert len(topics_in_cluster) == 1, f"cluster mixed topics: {topics_in_cluster}"

def test_cluster_captures_with_explicit_k_groups_by_theme():
    _clean()
    id_to_topic = _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn, k=3)
    assert result["k"] == 3
    assert len(result["clusters"]) == 3
    for cluster in result["clusters"]:
        assert cluster["size"] == 5
    _assert_clusters_are_topic_pure(result["clusters"], id_to_topic)

def test_cluster_captures_auto_k_picks_a_reasonable_cluster_count():
    _clean()
    id_to_topic = _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn)  # k=None -> auto via silhouette
    assert 2 <= result["k"] <= 14
    # Not asserting k == 3 exactly (would over-fit the test to this specific
    # embedding model's silhouette behavior) -- but however many clusters it
    # picked, each one must still be internally topic-pure. A sub-split of a
    # single topic into 2 clusters is fine; a cluster mixing topics is not.
    _assert_clusters_are_topic_pure(result["clusters"], id_to_topic)

def test_cluster_captures_marks_up_to_three_members_central_per_cluster():
    _clean()
    _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn, k=3)
    for cluster in result["clusters"]:
        central = [m for m in cluster["members"] if m["central"]]
        non_central = [m for m in cluster["members"] if not m["central"]]
        # size 5 per cluster here -> exactly 3 central, 2 not (regression
        # guard against "all members marked central" or "none marked" bugs;
        # exact nearest-neighbor correctness is sklearn's own tested
        # behavior via KMeans.transform(), not this project's to re-verify).
        assert len(central) == min(3, cluster["size"])
        assert len(non_central) == cluster["size"] - len(central)

def test_cluster_captures_reports_error_below_minimum():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="only one note",
                           keywords=["x"], source="other")
        store.save_capture(conn, raw_text="b", summary="only two notes",
                           keywords=["x"], source="other")
    with get_conn() as conn:
        result = store.cluster_captures(conn)
    assert result == {"error": "need at least 4 captures to cluster, have 2"}
```

- [ ] **Step 3: Run tests to verify they fail** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v -k cluster_captures`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'cluster_captures'` (or `pytest.skip`'d if `DATABASE_URL` is unset — export it first, pointing at a throwaway/dev Postgres with the schema from `migrations/001_init.sql` applied).

- [ ] **Step 4: Write the implementation** **[repo]**

At the top of `openbrain-mcp/app/store.py`, change:

```python
# app/store.py
import psycopg
from psycopg.types.json import Json
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
from app.keywords import normalize_keywords
from app.embeddings import embed_passage, embed_query
from app.fingerprint import content_fingerprint

_MIN_CAPTURES_TO_CLUSTER = 4
_MAX_AUTO_K = 10
```

Insert into `openbrain-mcp/app/store.py`, immediately after the `find_near_duplicates` function (ends at line 85) and before `fetch_recent`:

```python
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

- [ ] **Step 5: Run tests to verify they pass** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: PASS — all previous tests plus the 4 new ones (12 total in this file). This can take longer than the other test files (~10-30s) since `_auto_select_k` fits multiple `KMeans` models per call.

> If `test_cluster_captures_auto_k_picks_a_reasonable_cluster_count` or
> `test_cluster_captures_with_explicit_k_groups_by_theme` fails specifically
> on the topic-purity assertion (not a missing-function error), print
> `result["clusters"]` and check which topics got mixed: the 5 wording
> variants per topic in `_CLUSTER_TOPICS` may need to vary by fewer words
> (closer to the single-word "business"/"company" pattern) to increase
> within-topic similarity further. Don't loosen `_MIN_CAPTURES_TO_CLUSTER`
> or change the clustering algorithm to fix a flaky fixture — adjust the
> fixture's wording instead, the same resolution path documented in the
> design spec.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/pyproject.toml openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(store): add cluster_captures (embedding-based k-Means clustering)"
```

---

### Task 2: MCP tool — `cluster_captures`

**Files:**
- Modify: `openbrain-mcp/app/server.py` (insert after `compute_fingerprint`, which currently ends at line 81, before `class BearerAuthMiddleware`)

- [ ] **Step 1: Add the tool** **[repo]**

Insert into `openbrain-mcp/app/server.py`, immediately after the `compute_fingerprint` tool function (ends at line 81) and before `class BearerAuthMiddleware`:

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

This follows the `server → store` pattern of six of the seven existing tools (`save`, `search`,
`list_recent`, `stats`, `delete`, `update`, `find_near_duplicates`) — `compute_fingerprint` is the
one documented exception that bypasses `store.py`, since it needs no DB access; this tool does need
DB access (it reads every capture's embedding), so it follows the normal pattern. No dedicated
server-level test is added, consistent with `find_near_duplicates` and `compute_fingerprint`:
`tests/test_server.py` only covers auth/health/host-header behavior of the Starlette app, not
per-tool logic (that's `test_store.py`'s job).

- [ ] **Step 2: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — all tests, including `tests/test_server.py`'s auth/health tests (unaffected by this
change) and the 12 tests in `tests/test_store.py`.

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(mcp): expose cluster_captures as an MCP tool"
```

---

### Task 3: Document the new tool in `README.md`

**Files:**
- Modify: `README.md` (tool table, count references, repo-layout comments)

- [ ] **Step 1: Rename the tool-count heading and table** **[repo]**

Change the heading `## The eight MCP tools` to `## The nine MCP tools`, and add this row to the end
of the table (after the `compute_fingerprint` row):

```markdown
| `cluster_captures(k?)` | Read-only. Groups all captures into thematic clusters by embedding similarity (k-Means). If `k` is omitted, the cluster count is chosen automatically via silhouette score. Returns full cluster membership with a `central` flag marking each cluster's 3 most representative entries — cluster *labeling* is left to the calling client. |
```

- [ ] **Step 2: Update test-count references** **[repo]**

Change:
```markdown
(`openbrain-mcp/tests/`, 24 tests, mostly run against a real Postgres+pgvector instance;
`compute_fingerprint`'s tests are the exception and need no database).
```
to:
```markdown
(`openbrain-mcp/tests/`, 28 tests, mostly run against a real Postgres+pgvector instance;
`compute_fingerprint`'s tests are the exception and need no database).
```

Change:
```markdown
  tests/                     # 24 tests, pytest
```
to:
```markdown
  tests/                     # 28 tests, pytest
```

- [ ] **Step 3: Update the remaining "eight" references and repo-layout comments** **[repo]**

Change:
```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes eight tools over the
```
to:
```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes nine tools over the
```

Change:
```markdown
`http://localhost:8080/mcp` with that bearer token and call the eight tools directly — useful for
```
to:
```markdown
`http://localhost:8080/mcp` with that bearer token and call the nine tools directly — useful for
```

Change the repo-layout comment:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates -- the only file with SQL
```
to:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates/cluster_captures -- the only file with SQL
```

Change the repo-layout comment:
```
    server.py             # the 8 MCP tools + bearer auth + /health
```
to:
```
    server.py             # the 9 MCP tools + bearer auth + /health
```

**IMPORTANT — do NOT touch this line:** the "Status" table's early Phase-2 row says
"6 MCP tools, TDD, 18 tests" — this is an intentional historical point-in-time snapshot and must
stay unchanged (same rule as the previous two capabilities).

- [ ] **Step 4: Commit** **[repo]**

```bash
git add README.md
git commit -m "docs: document cluster_captures in the MCP tool table"
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

`scikit-learn` is a new dependency added in Task 1 — the `--build` flag ensures the image is
rebuilt with it installed rather than reusing a stale cached image.

- [ ] **Step 2: Seed enough captures to cluster, then call the tool from a connected MCP client** **[repo]**

From Claude Code or Claude Desktop (already configured per README Phase 6), with the local server's
bearer token:

```
"Save these 6 notes to openbrain, then call cluster_captures with k=2 and tell me what comes back:
1. Sarah is considering leaving her job to start a consulting business.
2. Sarah is considering leaving her job to start a consulting company.
3. Sarah is considering leaving her job to start a consulting firm.
4. A recipe for sourdough bread using a rye starter.
5. A recipe for sourdough bread using a wheat starter.
6. A recipe for sourdough bread using a spelt starter."
```

Expected: `cluster_captures` returns `{"k": 2, "clusters": [...]}` with two clusters, one containing
the 3 "Sarah" notes and the other containing the 3 "sourdough" notes, each member carrying a
`central` flag (all 3 should be `true` in each cluster here, since cluster size equals the central
cap of 3).

- [ ] **Step 3: Confirm auto-k and read-only behavior** **[repo]**

Run `stats`, then ask the client:

```
"Now call cluster_captures with no k argument (auto-detect) and tell me what k it picked."
```

Expected: a `k` between 2 and 5 (5 total captures minus 1), with the same topic-pure grouping as
Step 2 (possibly with the two topics still forming 2 clusters, or split further — either is fine, as
long as no cluster mixes both topics). Compare the `stats` result from just before this step to a
fresh `stats` call taken right after: the `total` count must be identical, confirming this
`cluster_captures` call itself did not mutate any data (it only reads).

- [ ] **Step 4: Tear down any throwaway local resources** **[repo]**

If the local compose stack was brought up only for this smoke test (not already running for other
reasons), bring it back down: `docker compose -f docker-compose.openbrain.yml down`.
