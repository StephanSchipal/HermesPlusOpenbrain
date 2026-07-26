# OpenBrain GUI — Search Filters, Sorting & Find-Similar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `openbrain-gui` narrow search/browse results by source, date range, and keywords
(AND/OR), sort results by date, and jump from any result to "notes similar to this one" — all
without breaking any existing caller of `openbrain-mcp`'s `search`/`list_recent` tools.

**Architecture:** `openbrain-mcp/app/store.py`'s `search_captures()` and `fetch_recent()` grow
optional filter parameters (`source`, `date_from`, `date_to`, `keywords`, `keyword_mode`) applied as
a dynamic SQL `WHERE` clause, plus `search_captures()` grows a `capture_id` mode (mutually exclusive
with `query`) that reuses a capture's already-stored embedding. `openbrain-gui`'s backend
(`routes.py`) passes the new fields through and translates any `{"error": ...}` dict the tools now
return into an HTTP 400 (distinct from the existing 502 for "MCP unreachable"), plus a new
`GET /api/recent` route for filter-only browsing. The frontend gets one new component
(`FilterBar.jsx`), an extended `ResultGrid.jsx` (sort dropdown, hit count, "Find similar" button),
and `App.jsx` wiring — the existing keyword panel and keyword graph are untouched.

**Tech Stack:** Same as the rest of this project — `psycopg`/Postgres+pgvector and the `mcp` Python
SDK for `openbrain-mcp`; FastAPI + Pydantic for `openbrain-gui`'s backend; plain JS/JSX React (no
TypeScript, no test framework) for its frontend.

**Spec:** `docs/superpowers/specs/2026-07-26-openbrain-gui-search-filters-design.md`

---

### Task 1: `openbrain-mcp` — `search_captures()` gets source/date/keyword filters

**Files:**
- Modify: `openbrain-mcp/app/store.py:53-67` (`search_captures`)
- Test: `openbrain-mcp/tests/test_store.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
def test_search_captures_filters_by_source():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="a note about the weather today",
                           keywords=["weather"], source="whatsapp")
        store.save_capture(conn, raw_text="b", summary="a note about the weather today",
                           keywords=["weather"], source="web")
    with get_conn() as conn:
        results = store.search_captures(conn, query="weather", k=10, source="whatsapp")
    assert len(results) == 1
    assert results[0]["source"] == "whatsapp"

def test_search_captures_filters_by_date_range():
    _clean()
    with get_conn() as conn:
        r_old = store.save_capture(conn, raw_text="a", summary="an old note about hiking",
                                   keywords=["hiking"], source="other")
        r_new = store.save_capture(conn, raw_text="b", summary="a new note about hiking",
                                   keywords=["hiking"], source="other")
    with get_conn() as conn:  # backdate directly -- no API path changes created_at
        with conn.cursor() as cur:
            cur.execute("UPDATE captures SET created_at = '2020-01-01' WHERE id = %s", (r_old["id"],))
        conn.commit()
    with get_conn() as conn:
        results = store.search_captures(conn, query="hiking", k=10, date_from="2025-01-01")
    assert {r["id"] for r in results} == {r_new["id"]}

def test_search_captures_keyword_filter_or_mode():
    _clean()
    with get_conn() as conn:
        r_a = store.save_capture(conn, raw_text="a", summary="note one about topics",
                                 keywords=["sarah"], source="other")
        r_b = store.save_capture(conn, raw_text="b", summary="note two about topics",
                                 keywords=["job"], source="other")
        store.save_capture(conn, raw_text="c", summary="note three about topics",
                           keywords=["unrelated"], source="other")
    with get_conn() as conn:
        results = store.search_captures(conn, query="topics", k=10,
                                        keywords=["sarah", "job"], keyword_mode="or")
    assert {r["id"] for r in results} == {r_a["id"], r_b["id"]}

def test_search_captures_keyword_filter_and_mode():
    _clean()
    with get_conn() as conn:
        r_both = store.save_capture(conn, raw_text="a", summary="note one about topics",
                                    keywords=["sarah", "job"], source="other")
        store.save_capture(conn, raw_text="b", summary="note two about topics",
                           keywords=["sarah"], source="other")
    with get_conn() as conn:
        results = store.search_captures(conn, query="topics", k=10,
                                        keywords=["sarah", "job"], keyword_mode="and")
    assert {r["id"] for r in results} == {r_both["id"]}

def test_search_captures_keyword_filter_is_case_insensitive():
    _clean()
    with get_conn() as conn:
        r = store.save_capture(conn, raw_text="a", summary="a note about topics",
                               keywords=["Sarah"], source="other")
    with get_conn() as conn:
        results = store.search_captures(conn, query="topics", k=10, keywords=["sarah"])
    assert {r2["id"] for r2 in results} == {r["id"]}

def test_search_captures_combines_multiple_filters():
    _clean()
    with get_conn() as conn:
        r_match = store.save_capture(conn, raw_text="a", summary="a note about topics",
                                     keywords=["sarah"], source="whatsapp")
        store.save_capture(conn, raw_text="b", summary="a note about topics",
                           keywords=["sarah"], source="web")            # wrong source
        store.save_capture(conn, raw_text="c", summary="a note about topics",
                           keywords=["unrelated"], source="whatsapp")   # wrong keyword
    with get_conn() as conn:
        results = store.search_captures(conn, query="topics", k=10,
                                        source="whatsapp", keywords=["sarah"])
    assert {r["id"] for r in results} == {r_match["id"]}

def test_search_captures_rejects_invalid_keyword_mode():
    _clean()
    with get_conn() as conn:
        result = store.search_captures(conn, query="anything", keyword_mode="xor")
    assert result == {"error": "keyword_mode must be 'and' or 'or', got 'xor'"}
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run (reuse the `openbrain-test-db` container if it already exists — `docker start openbrain-test-db`,
port 55432, user `openbrain`, password `testpass123`; otherwise create one from
`migrations/001_init.sql`):

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "filters_by_source or filters_by_date_range or keyword_filter or combines_multiple or rejects_invalid_keyword_mode"
```

Expected: FAIL with `TypeError: search_captures() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Implement the filters** **[repo]**

Replace `search_captures` in `openbrain-mcp/app/store.py` (currently lines 53-67):

```python
def search_captures(conn: psycopg.Connection, *, query: str | None = None,
                     capture_id: str | None = None, k: int = 5,
                     source: str | None = None, date_from: str | None = None,
                     date_to: str | None = None, keywords: list[str] | None = None,
                     keyword_mode: str = "or") -> list[dict] | dict:
    """Semantic search by query text, or (capture_id mode, added Task 2) by an
    existing capture's already-stored embedding. Optional filters narrow the
    SQL WHERE clause itself, not a post-fetch filter in the caller -- so a
    narrow filter combined with a small k cannot silently under-return
    matches that exist further down the ranked list. keyword_mode must be
    "and" (every given keyword present) or "or" (any given keyword present);
    keyword matching is case-insensitive since `keywords` is stored
    case-preserving (see `normalize_keywords`)."""
    if keyword_mode not in ("and", "or"):
        return {"error": f"keyword_mode must be 'and' or 'or', got {keyword_mode!r}"}

    emb = embed_query(query)
    where_clauses, where_params = _build_filter_clause(
        source=source, date_from=date_from, date_to=date_to,
        keywords=keywords, keyword_mode=keyword_mode,
    )
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, summary, keywords, source, source_url, lang, created_at,
                   1 - (embedding <=> %s::vector) AS score
            FROM captures
            {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            [emb, *where_params, emb, k],
        )
        rows = cur.fetchall()
    return [_row_to_result(r) for r in rows]

def _build_filter_clause(*, source: str | None, date_from: str | None, date_to: str | None,
                         keywords: list[str] | None, keyword_mode: str) -> tuple[list[str], list]:
    """Shared by search_captures and fetch_recent: builds the WHERE-clause
    fragments and their parameters for the source/date/keyword filters. Does
    NOT validate keyword_mode -- callers already did that before this runs."""
    clauses: list[str] = []
    params: list = []
    if source is not None:
        clauses.append("source = %s")
        params.append(source)
    if date_from is not None:
        clauses.append("created_at::date >= %s::date")
        params.append(date_from)
    if date_to is not None:
        clauses.append("created_at::date <= %s::date")
        params.append(date_to)
    if keywords:
        lowered = [kw.lower() for kw in keywords]
        op = "@>" if keyword_mode == "and" else "&&"
        clauses.append(f"ARRAY(SELECT lower(kw) FROM unnest(keywords) AS kw) {op} %s::text[]")
        params.append(lowered)
    return clauses, params
```

Note the date filters compare on `created_at::date` (calendar day, ignoring time-of-day) rather than
a raw `timestamptz` bound — this matches a simple `<input type="date">` picker in the frontend,
where "Bis 2026-07-31" should include everything captured on July 31st, not just up to midnight.

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "filters_by_source or filters_by_date_range or keyword_filter or combines_multiple or rejects_invalid_keyword_mode"
```

Expected: PASS (7 tests).

- [ ] **Step 5: Run the full existing `test_store.py` suite to check for regressions** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v
```

Expected: PASS, all tests (existing `test_save_then_semantic_search_finds_by_meaning` etc. still pass
unfiltered, since every new parameter defaults to `None`/`"or"`).

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(openbrain-mcp): add source/date/keyword filters to search_captures"
```

---

### Task 2: `openbrain-mcp` — `search_captures()` gets a `capture_id` ("find similar") mode

**Files:**
- Modify: `openbrain-mcp/app/store.py` (`search_captures`, from Task 1)
- Test: `openbrain-mcp/tests/test_store.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py` (reuses the existing `_save_topic_fixture` helper
already defined above the clustering tests):

```python
def test_search_captures_by_capture_id_excludes_itself_and_finds_neighbors():
    _clean()
    id_to_topic = _save_topic_fixture()  # 15 captures, 3 topics x 5 near-duplicate-worded each
    career_ids = [cid for cid, topic in id_to_topic.items() if topic == "career"]
    with get_conn() as conn:
        results = store.search_captures(conn, capture_id=career_ids[0], k=4)
    result_ids = {r["id"] for r in results}
    assert career_ids[0] not in result_ids
    assert result_ids == set(career_ids[1:])  # its 4 nearest neighbors: the other career captures

def test_search_captures_capture_id_respects_filters():
    _clean()
    id_to_topic = _save_topic_fixture()
    career_ids = [cid for cid, topic in id_to_topic.items() if topic == "career"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE captures SET source = 'whatsapp' WHERE id = %s", (career_ids[1],))
        conn.commit()
    with get_conn() as conn:
        results = store.search_captures(conn, capture_id=career_ids[0], k=10, source="whatsapp")
    assert {r["id"] for r in results} == {career_ids[1]}

def test_search_captures_unknown_capture_id_returns_error():
    _clean()
    with get_conn() as conn:
        result = store.search_captures(conn, capture_id="00000000-0000-0000-0000-000000000000")
    assert result == {"error": "capture not found: 00000000-0000-0000-0000-000000000000"}

def test_search_captures_rejects_both_query_and_capture_id():
    _clean()
    with get_conn() as conn:
        r = store.save_capture(conn, raw_text="a", summary="a note", keywords=["x"], source="other")
    with get_conn() as conn:
        result = store.search_captures(conn, query="anything", capture_id=r["id"])
    assert result == {"error": "exactly one of query or capture_id must be given"}

def test_search_captures_rejects_neither_query_nor_capture_id():
    _clean()
    with get_conn() as conn:
        result = store.search_captures(conn)
    assert result == {"error": "exactly one of query or capture_id must be given"}
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "capture_id"
```

Expected: FAIL — `search_captures(conn)` with no `query` currently raises inside `embed_query(None)`
instead of returning the mutual-exclusivity error dict.

- [ ] **Step 3: Implement `capture_id` mode** **[repo]**

Replace the top of `search_captures` (the part before `where_clauses, where_params = ...`) with:

```python
def search_captures(conn: psycopg.Connection, *, query: str | None = None,
                     capture_id: str | None = None, k: int = 5,
                     source: str | None = None, date_from: str | None = None,
                     date_to: str | None = None, keywords: list[str] | None = None,
                     keyword_mode: str = "or") -> list[dict] | dict:
    """Semantic search by query text, or by an existing capture's
    already-stored embedding (capture_id) -- exactly one of the two must be
    given; the source capture is excluded from its own results. Optional
    filters narrow the SQL WHERE clause itself, not a post-fetch filter in
    the caller -- so a narrow filter combined with a small k cannot silently
    under-return matches that exist further down the ranked list.
    keyword_mode must be "and" (every given keyword present) or "or" (any
    given keyword present); keyword matching is case-insensitive since
    `keywords` is stored case-preserving (see `normalize_keywords`)."""
    if (query is None) == (capture_id is None):
        return {"error": "exactly one of query or capture_id must be given"}
    if keyword_mode not in ("and", "or"):
        return {"error": f"keyword_mode must be 'and' or 'or', got {keyword_mode!r}"}

    where_clauses, where_params = _build_filter_clause(
        source=source, date_from=date_from, date_to=date_to,
        keywords=keywords, keyword_mode=keyword_mode,
    )
    if capture_id is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT embedding FROM captures WHERE id = %s", (capture_id,))
            row = cur.fetchone()
        if row is None:
            return {"error": f"capture not found: {capture_id}"}
        emb = row[0]
        where_clauses.append("id != %s")
        where_params.append(capture_id)
    else:
        emb = embed_query(query)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, summary, keywords, source, source_url, lang, created_at,
                   1 - (embedding <=> %s::vector) AS score
            FROM captures
            {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            [emb, *where_params, emb, k],
        )
        rows = cur.fetchall()
    return [_row_to_result(r) for r in rows]
```

(`_build_filter_clause` and the trailing `[_row_to_result(r) for r in rows]` return from Task 1 are
unchanged — only the validation/embedding-source section above it changes.)

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "capture_id"
```

Expected: PASS (5 tests).

- [ ] **Step 5: Run the full `test_store.py` suite** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v
```

Expected: PASS, all tests.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(openbrain-mcp): add capture_id (find-similar) mode to search_captures"
```

---

### Task 3: `openbrain-mcp` — `fetch_recent()` gets the same filters

**Files:**
- Modify: `openbrain-mcp/app/store.py:201-213` (`fetch_recent`)
- Test: `openbrain-mcp/tests/test_store.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
def test_fetch_recent_filters_by_source():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="note a", keywords=["x"], source="whatsapp")
        store.save_capture(conn, raw_text="b", summary="note b", keywords=["x"], source="web")
    with get_conn() as conn:
        results = store.fetch_recent(conn, n=10, source="whatsapp")
    assert len(results) == 1
    assert results[0]["source"] == "whatsapp"

def test_fetch_recent_filters_by_date_range():
    _clean()
    with get_conn() as conn:
        r_old = store.save_capture(conn, raw_text="a", summary="old note", keywords=["x"], source="other")
        r_new = store.save_capture(conn, raw_text="b", summary="new note", keywords=["x"], source="other")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE captures SET created_at = '2020-01-01' WHERE id = %s", (r_old["id"],))
        conn.commit()
    with get_conn() as conn:
        results = store.fetch_recent(conn, n=10, date_from="2025-01-01")
    assert {r["id"] for r in results} == {r_new["id"]}

def test_fetch_recent_filters_by_keywords_and_mode():
    _clean()
    with get_conn() as conn:
        r_both = store.save_capture(conn, raw_text="a", summary="note",
                                    keywords=["sarah", "job"], source="other")
        store.save_capture(conn, raw_text="b", summary="note", keywords=["sarah"], source="other")
    with get_conn() as conn:
        results = store.fetch_recent(conn, n=10, keywords=["sarah", "job"], keyword_mode="and")
    assert {r["id"] for r in results} == {r_both["id"]}

def test_fetch_recent_rejects_invalid_keyword_mode():
    _clean()
    with get_conn() as conn:
        result = store.fetch_recent(conn, keyword_mode="xor")
    assert result == {"error": "keyword_mode must be 'and' or 'or', got 'xor'"}

def test_fetch_recent_still_orders_by_created_at_desc_with_filters():
    _clean()
    with get_conn() as conn:
        r1 = store.save_capture(conn, raw_text="a", summary="note one", keywords=["x"], source="other")
        r2 = store.save_capture(conn, raw_text="b", summary="note two", keywords=["x"], source="other")
    with get_conn() as conn:
        results = store.fetch_recent(conn, n=10, source="other")
    assert [r["id"] for r in results] == [r2["id"], r1["id"]]  # newest first
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "fetch_recent_filters or fetch_recent_rejects or fetch_recent_still"
```

Expected: FAIL with `TypeError: fetch_recent() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Implement the filters** **[repo]**

Replace `fetch_recent` in `openbrain-mcp/app/store.py` (currently lines 201-213):

```python
def fetch_recent(conn: psycopg.Connection, *, n: int = 10, source: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 keywords: list[str] | None = None, keyword_mode: str = "or"
                 ) -> list[dict] | dict:
    """Most recent captures, optionally narrowed by the same source/date/
    keyword filters as search_captures -- no query/ranking involved, stays
    ordered by created_at DESC. Used for filter-only browsing when there's
    no search text at all."""
    if keyword_mode not in ("and", "or"):
        return {"error": f"keyword_mode must be 'and' or 'or', got {keyword_mode!r}"}

    where_clauses, where_params = _build_filter_clause(
        source=source, date_from=date_from, date_to=date_to,
        keywords=keywords, keyword_mode=keyword_mode,
    )
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, summary, keywords, source, source_url, lang, created_at, NULL::float
            FROM captures
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [*where_params, n],
        )
        rows = cur.fetchall()
    return [_row_to_result(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v -k "fetch_recent"
```

Expected: PASS, all `fetch_recent` tests (old and new).

- [ ] **Step 5: Run the full `test_store.py` suite** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/test_store.py -v
```

Expected: PASS, all tests.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(openbrain-mcp): add source/date/keyword filters to fetch_recent"
```

---

### Task 4: `openbrain-mcp` — extend `search`/`list_recent` MCP tool signatures

**Files:**
- Modify: `openbrain-mcp/app/server.py:34-44` (`search`, `list_recent`)

No new automated tests for this task: `test_server.py` only covers the Starlette app's auth/health
layer (it doesn't invoke individual `@mcp.tool()` functions directly), and every other tool addition
in this project has relied on `test_store.py`'s real-DB tests for business-logic correctness — this
task is a thin, mechanical signature/docstring passthrough with no logic of its own to test.

- [ ] **Step 1: Update the tool signatures** **[repo]**

Replace the `search` and `list_recent` tools in `openbrain-mcp/app/server.py` (currently lines
34-44):

```python
@mcp.tool()
def search(query: str | None = None, capture_id: str | None = None, k: int = 5,
           source: str | None = None, date_from: str | None = None,
           date_to: str | None = None, keywords: list[str] | None = None,
           keyword_mode: str = "or") -> list[dict] | dict:
    """Semantic search over captured notes. Give exactly one of `query` (free
    text) or `capture_id` (find notes similar to an existing capture, using
    its already-stored embedding -- excludes that capture from its own
    results); giving both or neither returns {"error": ...}. Optional
    filters: `source` (exact match), `date_from`/`date_to` (ISO date
    strings, inclusive, by calendar day), `keywords` (case-insensitive;
    `keyword_mode` "and" requires all of them, "or" (default) requires any).
    An invalid `keyword_mode` also returns {"error": ...} instead of raising."""
    with get_conn() as conn:
        return store.search_captures(
            conn, query=query, capture_id=capture_id, k=k, source=source,
            date_from=date_from, date_to=date_to, keywords=keywords,
            keyword_mode=keyword_mode,
        )

@mcp.tool()
def list_recent(n: int = 10, source: str | None = None, date_from: str | None = None,
                date_to: str | None = None, keywords: list[str] | None = None,
                keyword_mode: str = "or") -> list[dict] | dict:
    """List the most recently captured notes, optionally narrowed by the same
    source/date/keyword filters as `search` (see its docstring) -- useful for
    browsing by filter alone, with no search text. An invalid `keyword_mode`
    returns {"error": ...} instead of raising."""
    with get_conn() as conn:
        return store.fetch_recent(
            conn, n=n, source=source, date_from=date_from, date_to=date_to,
            keywords=keywords, keyword_mode=keyword_mode,
        )
```

- [ ] **Step 2: Run the full existing test suite to confirm no regressions** **[repo]**

```bash
DATABASE_URL="postgresql://openbrain:testpass123@localhost:55432/openbrain" \
  python -m pytest openbrain-mcp/tests/ -v
```

Expected: PASS, all tests (including `test_server.py`'s auth/health tests, untouched by this change).

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(openbrain-mcp): expose search/list_recent filters and find-similar mode"
```

---

### Task 5: `openbrain-gui` backend — `POST /api/search` passes filters through

**Files:**
- Modify: `openbrain-gui/backend/app/mcp_client.py:69-78` (`parse_list_result` docstring/type hint)
- Modify: `openbrain-gui/backend/app/routes.py` (`SearchRequest`, `search`)
- Test: `openbrain-gui/backend/tests/test_routes.py`

**Important:** `search`'s new return annotation is `list[dict] | dict` (a `Union`). Per `mcp`'s own
`func_metadata` docs ("Generic types (list, dict, Union, etc.) - wrapped in a model with a 'result'
field"), a Union return type **still gets `structuredContent`**, exactly like a plain `list[dict]` —
only a **bare, unparameterized** `dict` annotation (like `stats`/`update`) skips `structuredContent`
entirely. So mocking `search` returning an error dict in tests must use the existing `_list_result`
helper (which sets `structuredContent`), **not** `_dict_result` (which doesn't) — even though the
payload itself is a dict, not a list.

- [ ] **Step 1: Update `mcp_client.py`'s docstring and type hint** **[repo]**

In `openbrain-gui/backend/app/mcp_client.py`, update the module docstring's tool list and
`parse_list_result`'s signature/docstring (currently lines 15-19 and 69-78):

```python
Concretely: `stats`, `delete`, `update`, and `cluster_captures` (bare `dict`)
must be parsed from the single unstructured text block (`parse_dict_result`);
`search`, `list_keywords`, and `list_recent` (`list[dict]`, or `list[dict] |
dict` for `search`/`list_recent`'s error-dict cases) get real
`structuredContent`, wrapped as `{"result": [...]}` (`parse_list_result`) --
Union return types are wrapped in `structuredContent` the same way a plain
`list[dict]` is. If a future `mcp` upgrade changes this behavior, re-check
that trace before collapsing the two helpers.
```

```python
def parse_list_result(result: types.CallToolResult) -> list[dict] | dict:
    """For tools with a `list[dict]` (or `list[dict] | dict`, e.g. `search`'s
    error-dict case) return annotation -- these DO get structuredContent,
    wrapped as {"result": ...}. Callers of a tool with a Union annotation
    must check `isinstance(result, dict)` themselves to detect the error
    case (see routes.py's `_rows_or_400`)."""
    if result.isError:
        raise OpenBrainMCPError(_error_message(result))
    assert result.structuredContent is not None, (
        "expected structuredContent for a list-returning tool"
    )
    return result.structuredContent["result"]
```

- [ ] **Step 2: Write the failing tests** **[repo]**

In `openbrain-gui/backend/tests/test_routes.py`, replace `test_search_adds_subject_line_per_row`
(currently lines 48-60) — the arguments dict now always includes every filter key — and append new
tests:

```python
def test_search_adds_subject_line_per_row(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "search"
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result(
            [{"id": "abc", "summary": "Sarah is considering a pivot", "keywords": ["career"]}]
        )
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"query": "career notes"})
    assert resp.status_code == 200
    assert resp.json() == [{
        "id": "abc", "summary": "Sarah is considering a pivot",
        "keywords": ["career"], "subject_line": "Sarah is considering a pivot",
    }]

def test_search_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": "whatsapp", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "keywords": ["sarah"], "keyword_mode": "and",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={
        "query": "career notes", "source": "whatsapp",
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "keywords": ["sarah"], "keyword_mode": "and",
    })
    assert resp.status_code == 200
    assert resp.json() == []

def test_search_by_capture_id(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments["capture_id"] == "abc" and arguments["query"] is None
        return _list_result([{"id": "def", "summary": "a neighbor", "keywords": []}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"capture_id": "abc"})
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "def"

def test_search_error_dict_from_mcp_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # search's return annotation is a Union -- its error dict still
        # arrives via structuredContent, so _list_result (not _dict_result)
        # is the correct mock here (see this task's header note).
        return _list_result({"error": "exactly one of query or capture_id must be given"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={})
    assert resp.status_code == 400
    assert "exactly one of" in resp.json()["detail"]

def test_search_rejects_invalid_keyword_mode(client):
    resp = client.post("/api/search", json={"query": "x", "keyword_mode": "xor"})
    assert resp.status_code == 422
```

- [ ] **Step 3: Run tests to verify they fail** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k search
```

Expected: FAIL — `test_search_adds_subject_line_per_row`/`test_search_passes_filters_to_mcp_tool`
fail on the arguments-dict assertion (extra keys not yet sent); `test_search_by_capture_id` fails
with a `pydantic.ValidationError` (no `capture_id` field yet); `test_search_error_dict_from_mcp_becomes_400`
fails because `rows` is currently iterated even when it's a dict; `test_search_rejects_invalid_keyword_mode`
fails because there's no `keyword_mode` field to validate (currently 200, ignores the extra key).

- [ ] **Step 4: Implement the request model and route** **[repo]**

In `openbrain-gui/backend/app/routes.py`, add the import and replace `SearchRequest` and the
`search` route (currently lines 1-22 for imports/models, and lines 59-68 for the route):

```python
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import mcp_client, prompts_store, delete_log_store, subject_line, graph
from app.mcp_client import OpenBrainMCPError
from app.config import DEFAULT_SEARCH_K, DEFAULT_DELETE_LOG_LIMIT, GRAPH_MAX_CAPTURES

router = APIRouter(prefix="/api")

class SearchRequest(BaseModel):
    query: str | None = None
    capture_id: str | None = None
    k: int = DEFAULT_SEARCH_K
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    keywords: list[str] | None = None
    keyword_mode: Literal["and", "or"] = "or"
```

```python
def _rows_or_400(parsed: list[dict] | dict) -> list[dict]:
    """search/list_recent can return an {"error": ...} dict (bad capture_id,
    invalid keyword_mode, both/neither of query+capture_id) instead of a row
    list -- surface that as a 400 (caller's problem), distinct from the 502
    reserved for openbrain-mcp being unreachable."""
    if isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=parsed.get("error", "invalid request"))
    return parsed

@router.post("/search")
async def search(body: SearchRequest):
    result = await _call("search", {
        "query": body.query, "capture_id": body.capture_id, "k": body.k,
        "source": body.source, "date_from": body.date_from, "date_to": body.date_to,
        "keywords": body.keywords, "keyword_mode": body.keyword_mode,
    })
    try:
        rows = _rows_or_400(mcp_client.parse_list_result(result))
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=f"openbrain-mcp unreachable: {exc}") from exc
    for row in rows:
        row["subject_line"] = subject_line.make_subject_line(row["summary"])
    return rows
```

- [ ] **Step 5: Run tests to verify they pass** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k search
```

Expected: PASS (5 tests).

- [ ] **Step 6: Run the full `test_routes.py` suite** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: PASS, all tests.

- [ ] **Step 7: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/mcp_client.py openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui-backend): pass search filters and capture_id through /api/search"
```

---

### Task 6: `openbrain-gui` backend — new `GET /api/recent` (filter-only browsing)

**Files:**
- Modify: `openbrain-gui/backend/app/routes.py`
- Test: `openbrain-gui/backend/tests/test_routes.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-gui/backend/tests/test_routes.py`:

```python
def test_get_recent_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_recent"
        assert arguments == {
            "n": 25, "source": "whatsapp", "date_from": "2026-01-01",
            "date_to": None, "keywords": ["sarah", "job"], "keyword_mode": "or",
        }
        return _list_result([{"id": "a", "summary": "note", "keywords": ["sarah"]}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent", params={
        "source": "whatsapp", "date_from": "2026-01-01",
        "keywords": ["sarah", "job"],
    })
    assert resp.status_code == 200
    assert resp.json()[0]["subject_line"] == "note"

def test_get_recent_defaults_to_no_filters(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "n": 25, "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 200
    assert resp.json() == []

def test_get_recent_error_dict_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # Exercises the _rows_or_400 branch for this route -- list_recent's
        # own keyword_mode check is stricter than nothing, even though the
        # GUI's Literal type already screens the common bad-value case.
        return _list_result({"error": "keyword_mode must be 'and' or 'or', got 'xor'"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 400

def test_get_recent_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k get_recent
```

Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Implement the route** **[repo]**

Add to `openbrain-gui/backend/app/routes.py`, after the `search` route:

```python
@router.get("/recent")
async def get_recent(
    n: int = DEFAULT_SEARCH_K,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    keywords: list[str] | None = Query(default=None),
    keyword_mode: Literal["and", "or"] = "or",
):
    result = await _call("list_recent", {
        "n": n, "source": source, "date_from": date_from, "date_to": date_to,
        "keywords": keywords, "keyword_mode": keyword_mode,
    })
    try:
        rows = _rows_or_400(mcp_client.parse_list_result(result))
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=f"openbrain-mcp unreachable: {exc}") from exc
    for row in rows:
        row["subject_line"] = subject_line.make_subject_line(row["summary"])
    return rows
```

Add `Query` to the `fastapi` import at the top of the file (needed for the repeatable `keywords`
query parameter):

```python
from fastapi import APIRouter, HTTPException, Query
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k get_recent
```

Expected: PASS (4 tests).

- [ ] **Step 5: Run the full `test_routes.py` suite** **[repo]**

```bash
cd openbrain-gui/backend && python -m pytest tests/ -v
```

Expected: PASS, all tests.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/routes.py openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui-backend): add GET /api/recent for filter-only browsing"
```

---

### Task 7: `openbrain-gui` frontend — `api.js`

**Files:**
- Modify: `openbrain-gui/frontend/src/api.js`

No automated frontend test framework exists in this project (established since Phase 1) —
verification for every frontend task in this plan happens manually in Task 12.

- [ ] **Step 1: Extend `search` and add `getRecent`** **[repo]**

Replace `openbrain-gui/frontend/src/api.js`'s `search` line (currently line 19) and add a new
function:

```js
  search: (payload) => request('/search', { method: 'POST', body: JSON.stringify(payload) }),
  getRecent: (params) => {
    const query = new URLSearchParams()
    if (params.n) query.set('n', params.n)
    if (params.source) query.set('source', params.source)
    if (params.date_from) query.set('date_from', params.date_from)
    if (params.date_to) query.set('date_to', params.date_to)
    for (const kw of params.keywords || []) query.append('keywords', kw)
    if (params.keyword_mode) query.set('keyword_mode', params.keyword_mode)
    return request(`/recent?${query.toString()}`)
  },
```

`search` changes from two positional args (`query, k`) to a single payload object, since it now
carries up to eight fields — every call site is updated in Task 10.

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/api.js
git commit -m "feat(gui-frontend): extend search() with filters, add getRecent()"
```

---

### Task 8: `openbrain-gui` frontend — `FilterBar.jsx`

**Files:**
- Create: `openbrain-gui/frontend/src/FilterBar.jsx`

- [ ] **Step 1: Create the file** **[repo]**

Create `openbrain-gui/frontend/src/FilterBar.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { api } from './api.js'

function isoDate(d) {
  return d.toISOString().slice(0, 10)
}

export default function FilterBar({ sources, filters, onFiltersChange }) {
  const [keywordInput, setKeywordInput] = useState('')
  const [suggestions, setSuggestions] = useState([])

  useEffect(() => {
    if (!keywordInput) { setSuggestions([]); return }
    const timer = setTimeout(() => {
      api.getKeywords(keywordInput)
        .then(setSuggestions)
        .catch(() => setSuggestions([]))
    }, 200)
    return () => clearTimeout(timer)
  }, [keywordInput])

  const update = (patch) => onFiltersChange({ ...filters, ...patch })

  const applyQuickRange = (days) => {
    const to = new Date()
    const from = days === null
      ? new Date(to.getFullYear(), 0, 1)
      : new Date(to.getTime() - days * 24 * 60 * 60 * 1000)
    update({ date_from: isoDate(from), date_to: isoDate(to) })
  }

  const addKeyword = (kw) => {
    if (!kw || filters.keywords.includes(kw)) return
    update({ keywords: [...filters.keywords, kw] })
    setKeywordInput('')
    setSuggestions([])
  }

  const removeKeyword = (kw) => update({ keywords: filters.keywords.filter((k) => k !== kw) })

  const reset = () =>
    onFiltersChange({ source: '', date_from: '', date_to: '', keywords: [], keyword_mode: 'or' })

  return (
    <div className="filter-bar">
      <select
        className="filter-source"
        value={filters.source}
        onChange={(e) => update({ source: e.target.value })}
      >
        <option value="">All sources</option>
        {sources.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>

      <div className="filter-dates">
        <input
          type="date"
          className="filter-date"
          value={filters.date_from}
          onChange={(e) => update({ date_from: e.target.value })}
        />
        <span>–</span>
        <input
          type="date"
          className="filter-date"
          value={filters.date_to}
          onChange={(e) => update({ date_to: e.target.value })}
        />
        <button type="button" onClick={() => applyQuickRange(7)}>7 days</button>
        <button type="button" onClick={() => applyQuickRange(30)}>1 month</button>
        <button type="button" onClick={() => applyQuickRange(null)}>This year</button>
      </div>

      <div className="filter-keywords">
        {filters.keywords.map((kw) => (
          <button key={kw} type="button" className="keyword-chip" onClick={() => removeKeyword(kw)}>
            {kw} ✕
          </button>
        ))}
        <input
          className="keyword-filter"
          placeholder="Add keyword filter…"
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); addKeyword(keywordInput) }
          }}
        />
        {suggestions.length > 0 && (
          <div className="keyword-suggestions">
            {suggestions.map((s) => (
              <button key={s.keyword} type="button" onClick={() => addKeyword(s.keyword)}>
                {s.keyword} ({s.count})
              </button>
            ))}
          </div>
        )}
        <select
          className="keyword-mode-toggle"
          value={filters.keyword_mode}
          onChange={(e) => update({ keyword_mode: e.target.value })}
        >
          <option value="or">OR</option>
          <option value="and">AND</option>
        </select>
      </div>

      <button type="button" onClick={reset}>Reset filters</button>
    </div>
  )
}
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/FilterBar.jsx
git commit -m "feat(gui-frontend): add FilterBar (source/date/keyword filters)"
```

---

### Task 9: `openbrain-gui` frontend — `ResultGrid.jsx` (sort, hit count, find-similar)

**Files:**
- Modify: `openbrain-gui/frontend/src/ResultGrid.jsx` (full rewrite — currently 36 lines)

- [ ] **Step 1: Replace the file** **[repo]**

Replace `openbrain-gui/frontend/src/ResultGrid.jsx` entirely:

```jsx
import { useEffect, useMemo, useState } from 'react'
import { formatDateTime } from './format.js'

function sortRows(rows, sortBy) {
  if (sortBy === 'date_desc') return [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at))
  if (sortBy === 'date_asc') return [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at))
  return rows // 'relevance' -- already ordered by the backend
}

export default function ResultGrid({ rows, selectedId, onSelect, onFindSimilar, hitCountLabel }) {
  const hasRelevance = rows.length > 0 && rows[0].score != null
  const [sortBy, setSortBy] = useState(hasRelevance ? 'relevance' : 'date_desc')

  useEffect(() => {
    setSortBy(rows.length > 0 && rows[0].score != null ? 'relevance' : 'date_desc')
  }, [rows])

  const sortedRows = useMemo(() => sortRows(rows, sortBy), [rows, sortBy])

  if (rows.length === 0) {
    return <p className="grid-empty">No results yet — run a search.</p>
  }

  return (
    <div className="result-grid-wrapper">
      <div className="result-grid-header">
        {hitCountLabel && <span className="label">{hitCountLabel}</span>}
        <select
          className="sort-dropdown"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
        >
          {hasRelevance && <option value="relevance">Relevance</option>}
          <option value="date_desc">Date, newest first</option>
          <option value="date_asc">Date, oldest first</option>
        </select>
      </div>
      <div className="result-grid">
        {sortedRows.map((row) => (
          <label key={row.id} className="result-row">
            <input
              type="radio"
              name="result-row"
              checked={selectedId === row.id}
              onChange={() => onSelect(row.id)}
            />
            <span className="result-id">{row.id.slice(0, 8)}</span>
            <div className="result-body">
              <div className="result-subject">{row.subject_line}</div>
              <div className="result-meta">
                {row.source_url && (
                  <a href={row.source_url} target="_blank" rel="noopener noreferrer">
                    {row.source_url}
                  </a>
                )}
              </div>
              <div className="result-meta">
                {formatDateTime(row.created_at)} · keywords: {row.keywords.join(', ')}
                {row.score != null && ` · relevance: ${Math.round(row.score * 100)}%`}
              </div>
            </div>
            <button
              type="button"
              className="find-similar-button"
              onClick={(e) => { e.stopPropagation(); onFindSimilar(row) }}
            >
              Find similar
            </button>
          </label>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/ResultGrid.jsx
git commit -m "feat(gui-frontend): add sort dropdown, hit count, and Find Similar to ResultGrid"
```

---

### Task 10: `openbrain-gui` frontend — wire it all up in `App.jsx`

**Files:**
- Modify: `openbrain-gui/frontend/src/App.jsx`

- [ ] **Step 1: Add the `FilterBar` import** **[repo]**

Add below the existing `KeywordGraph` import (currently line 11):

```js
import KeywordGraph from './KeywordGraph.jsx'
import FilterBar from './FilterBar.jsx'
```

- [ ] **Step 2: Add `filters` state** **[repo]**

Add after the existing `searchK` state declaration (currently line 21, `const [searchK, ...]`):

```js
  const [searchK, setSearchK] = useState(SEARCH_PAGE_SIZE)
  const [filters, setFilters] = useState({
    source: '', date_from: '', date_to: '', keywords: [], keyword_mode: 'or',
  })
```

- [ ] **Step 3: Replace `runSearch`/`handleSearch`/`handleLoadMore`** **[repo]**

Replace the block from `const runSearch = async (k) => {` through `const handleLoadMore = () => runSearch(searchK + SEARCH_PAGE_SIZE)` (currently lines 56-77):

```js
  const activeFilterPayload = () => ({
    source: filters.source || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
    keywords: filters.keywords.length ? filters.keywords : undefined,
    keyword_mode: filters.keyword_mode,
  })

  const hasActiveFilters = Boolean(
    filters.source || filters.date_from || filters.date_to || filters.keywords.length
  )

  const runSearch = async (k) => {
    setSearching(true)
    try {
      const results = await api.search({ query: prompt, k, ...activeFilterPayload() })
      setRows(results)
      setSearchK(k)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const runBrowse = async (n) => {
    setSearching(true)
    try {
      const results = await api.getRecent({ n, ...activeFilterPayload() })
      setRows(results)
      setSearchK(n)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const handleSearch = async () => {
    setSelectedId(null)
    setView('results')
    if (prompt.trim()) {
      await runSearch(SEARCH_PAGE_SIZE)
    } else {
      await runBrowse(SEARCH_PAGE_SIZE)
    }
  }

  const handleLoadMore = () =>
    prompt.trim() ? runSearch(searchK + SEARCH_PAGE_SIZE) : runBrowse(searchK + SEARCH_PAGE_SIZE)

  const handleFindSimilar = async (row) => {
    setSelectedId(null)
    setView('results')
    setSearching(true)
    try {
      const results = await api.search({
        capture_id: row.id, k: SEARCH_PAGE_SIZE, ...activeFilterPayload(),
      })
      setRows(results)
      setSearchK(SEARCH_PAGE_SIZE)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }
```

Note: `handleSearch` no longer early-returns on an empty prompt — an empty prompt is now the
"browse by filter" path, which is valid even with zero filters set (equivalent to plain
`list_recent`).

- [ ] **Step 4: Compute the hit-count label** **[repo]**

Add near the existing `selectedRow`/`canActOnSelection`/`canLoadMore` computations (currently lines
145-147):

```js
  const selectedRow = rows.find((r) => r.id === selectedId)
  const canActOnSelection = view === 'results' && !!selectedRow
  const canLoadMore = view === 'results' && rows.length > 0 && rows.length >= searchK
  const hitCountLabel = view === 'results' && rows.length > 0
    ? `${rows.length} result${rows.length === 1 ? '' : 's'}`
      + (hasActiveFilters && stats ? ` (filtered from ${stats.total})` : '')
    : null
```

- [ ] **Step 5: Mount `FilterBar` and wire `ResultGrid`'s new props** **[repo]**

Add `<FilterBar>` right after the existing `.top-row` div (currently closing at line 182, right
before `<div className="grid-actions">`):

```jsx
      <div className="top-row">
        <PromptBar
          prompt={prompt}
          onPromptChange={setPrompt}
          promptTextareaRef={promptTextareaRef}
          savedPrompts={savedPrompts}
          selectedPromptId={selectedPromptId}
          onSelectSavedPrompt={handleSelectSavedPrompt}
          onSearch={handleSearch}
          onSavePrompt={handleSavePrompt}
          onDeleteSavedPrompt={handleDeleteSavedPrompt}
          searching={searching}
        />
        <KeywordPanel onKeywordClick={handleKeywordClick} />
      </div>

      <FilterBar
        sources={stats ? Object.keys(stats.by_source) : []}
        filters={filters}
        onFiltersChange={setFilters}
      />

      <div className="grid-actions">
```

Then update the `ResultGrid` render call (currently line 209, inside the `view === 'results'`
branch) to pass the two new props:

```jsx
        <ResultGrid
          rows={rows}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onFindSimilar={handleFindSimilar}
          hitCountLabel={hitCountLabel}
        />
```

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/App.jsx
git commit -m "feat(gui-frontend): wire FilterBar, browse mode, and find-similar into App"
```

---

### Task 11: `openbrain-gui` frontend — CSS for the new UI

**Files:**
- Modify: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Add the new styles** **[repo]**

Append to `openbrain-gui/frontend/src/index.css`:

```css
.filter-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 12px;
  border: 1px solid var(--border); border-radius: 4px; padding: 8px 12px; margin-bottom: 8px;
}
.filter-source {
  background: var(--bg); color: var(--fg); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 6px;
}
.filter-dates { display: flex; align-items: center; gap: 6px; }
.filter-date {
  background: var(--bg); color: var(--fg); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 6px;
}
.filter-keywords { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; position: relative; }
.filter-keywords .keyword-filter { width: 160px; }
.keyword-mode-toggle {
  background: var(--bg); color: var(--fg); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 6px;
}
.keyword-suggestions {
  position: absolute; top: 100%; left: 0; z-index: 4; display: flex; flex-wrap: wrap;
  gap: 4px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
  padding: 6px; max-width: 320px;
}
.result-grid-wrapper { display: flex; flex-direction: column; gap: 6px; }
.result-grid-header { display: flex; justify-content: space-between; align-items: center; }
.sort-dropdown {
  background: var(--bg); color: var(--fg); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 6px;
}
.find-similar-button {
  align-self: center; background: none; color: var(--fg); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 8px; font-size: 12px;
}
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/index.css
git commit -m "style(gui-frontend): add styles for FilterBar and extended ResultGrid"
```

---

### Task 12: Manual end-to-end verification

**Files:** none (verification only)

No automated frontend test suite exists — this task is this feature's equivalent of the "manual
exercise in a real browser against the live corpus" step used for Phase 1 and Phase 3.

- [ ] **Step 1: Rebuild and start the local stack** **[repo]**

```bash
cd deploy && docker compose -f docker-compose.openbrain.yml up -d --build openbrain-mcp openbrain-gui
```

- [ ] **Step 2: Verify each success criterion from the spec** **[repo]**

Against the running local stack (a real browser at the GUI's local URL):

1. Search with a prompt + source filter → only that source appears in results.
2. Search with a prompt + date range → only captures in that range appear; pick a range you know
   excludes some existing captures, confirm they're absent.
3. Add two keyword filter chips, toggle OR → union of matches; toggle AND → only captures with both.
4. Clear the prompt, set only filters, click Search → `GET /api/recent` is used (check the Network
   tab), results have no relevance percentage shown.
5. Change the sort dropdown between Relevance/Date-newest/Date-oldest → grid re-orders instantly, no
   network request fires (check the Network tab).
6. Click "Find similar" on a result row → a new, different result set appears, the clicked row's own
   id is not present, and the sort dropdown shows "Relevance" again.
7. Set a filter, then click "Find similar" on a row → confirm the filter is still applied (e.g. the
   neighbor set only contains the filtered source).
8. Click "Reset filters" → filter bar clears; previous results stay on screen until the next search.
9. Hit-count line reads "N results" normally, and "N results (filtered from `stats.total`)" when any
   filter is active.
10. Regression check: existing keyword panel chip click still inserts text into the prompt textarea
    (unchanged); open the keyword graph view and confirm clicking a bubble still does the same.
11. Regression check: Summary/Change/Delete buttons, delete log, and saved prompts all still work as
    before.

- [ ] **Step 3: Tear down** **[repo]**

```bash
cd deploy && docker compose -f docker-compose.openbrain.yml down
```

(No commit for this task — verification only, no file changes.)

---

### Task 13: Update project docs

**Files:**
- Modify: `README.md`
- Modify: `OpenbrainAddition.md`

- [ ] **Step 1: Update the MCP tools table in `README.md`** **[repo]**

Modify `README.md` (currently lines 123-124) — replace:

```markdown
| `search(query, k=5)` | Semantic search. Returns the top-k matches by meaning, each with a cosine-similarity `score`. |
| `list_recent(n=10)` | Most recently captured notes, newest first. |
```

with:

```markdown
| `search(query?, capture_id?, k=5, source?, date_from?, date_to?, keywords?, keyword_mode="or")` | Semantic search — by free text (`query`) or by an existing capture's own embedding (`capture_id`, "find similar," excludes itself from its own results); exactly one of the two is required. Optional filters: exact `source`, `date_from`/`date_to` (inclusive, by calendar day), and `keywords` (`keyword_mode` "and"/"or"). Each result includes its cosine-similarity `score`. |
| `list_recent(n=10, source?, date_from?, date_to?, keywords?, keyword_mode="or")` | Most recently captured notes, newest first — optionally narrowed by the same source/date/keyword filters as `search`, with no query text at all. |
```

- [ ] **Step 2: Update the OpenBrain GUI section in `README.md`** **[repo]**

Modify `README.md` — replace the "Full details" bullet list (currently lines 324-328):

```markdown
Full details:
- Phase 1 design spec — [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md)
- Phase 1 implementation plan — [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md)
- Phase 3 design spec — [`docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md`](docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md)
- Phase 3 implementation plan — [`docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md`](docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md)
```

with:

```markdown
Full details:
- Phase 1 design spec — [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md)
- Phase 1 implementation plan — [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md)
- Phase 3 design spec — [`docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md`](docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md)
- Phase 3 implementation plan — [`docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md`](docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md)
- Search filters design spec — [`docs/superpowers/specs/2026-07-26-openbrain-gui-search-filters-design.md`](docs/superpowers/specs/2026-07-26-openbrain-gui-search-filters-design.md)
- Search filters implementation plan — [`docs/superpowers/plans/2026-07-26-openbrain-gui-search-filters.md`](docs/superpowers/plans/2026-07-26-openbrain-gui-search-filters.md)
```

Then add a new paragraph right after the existing "Keyword graph (Phase 3)" paragraph (currently
lines 358-364, ending "...Pan/zoom via scroll, drag, or the on-screen +/− buttons.") and before the
`### Running it locally` heading:

```markdown
**Search filters, sorting & find-similar:** the search bar now sits above a filter bar (source
dropdown, from/to date range with quick-range buttons, and its own keyword chips with an AND/OR
toggle — separate from the existing keyword-panel/keyword-graph click-to-insert behavior, which is
unchanged). Filters apply whether or not a search prompt is given — an empty prompt with filters
set browses recent captures directly (`GET /api/recent`). Results can be sorted by relevance or by
date; a "Find similar" button on each result finds neighbors of that capture by its own stored
embedding (`capture_id` search mode), respecting whatever filters are currently active.
```

- [ ] **Step 3: Update `OpenbrainAddition.md`** **[repo]**

Modify `OpenbrainAddition.md`'s §8 — after item 6 (Phase 3 keyword graph) and before the closing
summary paragraph, insert a new item 7:

```markdown
7. ✅ **Web-GUI — Suchfilter, Sortierung & "Ähnliche finden"** (`openbrain-gui`) —
   `search`/`list_recent` in `openbrain-mcp` bekamen optionale Filter
   (Quelle, Zeitraum nach Kalendertag, Keywords mit UND/ODER, case-
   insensitiv) direkt in der SQL-`WHERE`-Klausel — keine nachträgliche
   client-seitige Filterung eines kleinen Top-k, die bei engen Filtern
   fälschlich leere Ergebnisse liefern könnte. `search` bekam zusätzlich
   einen `capture_id`-Modus ("Ähnliche finden"): nutzt das bereits
   gespeicherte Embedding einer bestehenden Notiz statt neu zu embedden,
   schließt die Quell-Notiz aus den Ergebnissen aus, respektiert aktive
   Filter. Neue GUI-eigene `FilterBar` (Quelle-Dropdown, Von/Bis-Datum +
   Schnellauswahl-Buttons, eigene Keyword-Filter-Chips mit UND/ODER-Toggle —
   bewusst getrennt vom bestehenden Keyword-Panel/-Graph, deren Klick-fügt-
   Text-ein-Verhalten unverändert bleibt), Sortier-Dropdown (Relevanz/Datum)
   im Ergebnis-Grid, Trefferzahl-Anzeige, und ein "Ähnliche finden"-Button
   pro Ergebnis-Zeile. Neuer `GET /api/recent`-Endpunkt für reines Filter-
   Browsen ohne Suchtext. Spec:
   [`docs/superpowers/specs/2026-07-26-openbrain-gui-search-filters-design.md`](docs/superpowers/specs/2026-07-26-openbrain-gui-search-filters-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-26-openbrain-gui-search-filters.md`](docs/superpowers/plans/2026-07-26-openbrain-gui-search-filters.md).
```

Then update the closing summary paragraph from:

```markdown
Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten sowie Phase 1 und
Phase 3 der Web-GUI umgesetzt und deployed (Phase 2 bewusst übersprungen).
Details zu jeder einzelnen siehe die jeweiligen Spec-/Plan-Dokumente unter
`docs/superpowers/`.
```

to:

```markdown
Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten, Phase 1 und Phase 3
der Web-GUI, sowie deren Suchfilter-Erweiterung umgesetzt und deployed
(Phase 2 bewusst übersprungen). Details zu jeder einzelnen siehe die
jeweiligen Spec-/Plan-Dokumente unter `docs/superpowers/`.
```

- [ ] **Step 4: Commit** **[repo]**

```bash
git add README.md OpenbrainAddition.md
git commit -m "docs: document search filters, sorting, and find-similar in openbrain-gui"
```

---

### Task 14: Deploy to production and verify live

**Files:** none (deployment only)

- [ ] **Step 1: Push to GitHub** **[repo]**

```bash
git push origin main
```

- [ ] **Step 2: Pull and rebuild both services on the VPS** **[repo]**

Both `openbrain-mcp` (tool signatures) and `openbrain-gui` (backend + frontend) changed, so both
need rebuilding — unlike Phase 3, which only touched `openbrain-gui`:

```bash
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain && git pull --ff-only origin main"
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.openbrain.yml up -d --build openbrain-mcp openbrain-gui"
```

- [ ] **Step 3: Verify container health** **[repo]**

```bash
ssh root@srv1608402.hstgr.cloud "docker compose -f /root/HermesPlusOpenbrain/deploy/docker-compose.openbrain.yml ps"
```

Expected: both `deploy-openbrain-mcp-1` and `deploy-openbrain-gui-1` show `healthy`.

- [ ] **Step 4: Manually verify against the real production corpus** **[repo]**

Open `https://gui.<vps-host>.hstgr.cloud`, log in with the Traefik basic-auth credentials, and
repeat the key checks from Task 12 Step 2 (filters, browse mode, sorting, find-similar, hit count,
keyword-panel/graph regression check) against the real corpus.
