# OpenBrain Fingerprint Introspection Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, DB-free `compute_fingerprint` MCP tool to `openbrain-mcp` that shows the SHA-256 dedup fingerprint `save` would compute for given input, plus the normalized string it's based on — for debugging/introspecting the existing fingerprint mechanism, not for checking duplicates against the database.

**Architecture:** `app/fingerprint.py`'s `content_fingerprint()` is refactored to share a new `_compute_basis()` helper with a new `content_fingerprint_debug()` function, which returns the hash plus the normalized basis and which input (`"url"` or `"text"`) it came from. A thin `@mcp.tool()` wrapper in `server.py` calls `content_fingerprint_debug()` **directly** — unlike the other seven tools, it does not go through `store.py`, because it needs no `conn`/DB access.

**Tech Stack:** Python 3.11, `pytest` (pure unit tests, no Postgres needed for this feature — unlike capability 1).

**Reference:** Design spec at `docs/superpowers/specs/2026-07-21-openbrain-fingerprint-debug-design.md`.

---

## File structure (files touched by this plan)

```
openbrain-mcp/
  app/
    fingerprint.py   # MODIFY: extract _compute_basis(), add content_fingerprint_debug()
    server.py        # MODIFY: add compute_fingerprint MCP tool after find_near_duplicates()
  tests/
    test_fingerprint.py  # MODIFY: add 3 tests for content_fingerprint_debug
README.md               # MODIFY: add compute_fingerprint row to the tool table, bump counts
```

---

### Task 1: `content_fingerprint_debug` in `app/fingerprint.py`

**Files:**
- Modify: `openbrain-mcp/app/fingerprint.py` (replace `content_fingerprint`, which currently spans lines 29-39, with a version that delegates to a new `_compute_basis` helper; append `content_fingerprint_debug` after it)
- Modify: `openbrain-mcp/tests/test_fingerprint.py` (append after the existing 7 tests, which end at line 37)

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_fingerprint.py`:

```python
def test_debug_matches_content_fingerprint():
    kwargs = dict(source_url="https://youtu.be/abc123?si=XYZ789", raw_text="x")
    assert content_fingerprint_debug(**kwargs)["fingerprint"] == content_fingerprint(**kwargs)

def test_debug_reports_url_basis_when_url_present():
    result = content_fingerprint_debug(source_url="https://WWW.Example.com/page/", raw_text="ignored")
    assert result["basis_source"] == "url"
    assert result["normalized_basis"] == "example.com/page"

def test_debug_reports_text_basis_when_no_url():
    result = content_fingerprint_debug(source_url=None, raw_text="  Hello World  ")
    assert result["basis_source"] == "text"
    assert result["normalized_basis"] == "hello world"
```

Update the import line at the top of the file from:

```python
from app.fingerprint import content_fingerprint
```

to:

```python
from app.fingerprint import content_fingerprint, content_fingerprint_debug
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-mcp && python -m pytest tests/test_fingerprint.py -v -k debug`
Expected: FAIL — `ImportError: cannot import name 'content_fingerprint_debug' from 'app.fingerprint'`

- [ ] **Step 3: Write the implementation** **[repo]**

Replace the existing `content_fingerprint` function (lines 29-39 of `openbrain-mcp/app/fingerprint.py`) with:

```python
def _compute_basis(*, source_url: str | None, raw_text: str) -> tuple[str, str]:
    """Returns (normalized_basis, basis_source) where basis_source is "url" or "text"."""
    if source_url:
        return _normalize_url(source_url), "url"
    return _normalize_text(raw_text), "text"

def content_fingerprint(*, source_url: str | None, raw_text: str) -> str:
    """Stable dedup key. Prefer the normalized URL; fall back to normalized text.

    Same link (or same text) -> same fingerprint -> deduped on save. URL
    normalization strips scheme, www., trailing slash, and known per-share
    tracking params (YouTube's `si`, UTM params, fbclid/gclid) so the same
    content forwarded twice on WhatsApp still dedupes even though each share
    link carries a different tracking token.
    """
    basis, _ = _compute_basis(source_url=source_url, raw_text=raw_text)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()

def content_fingerprint_debug(*, source_url: str | None, raw_text: str) -> dict:
    """Same computation as content_fingerprint, but also exposes the
    normalized string that was hashed and which input it came from --
    for introspection/debugging, not for dedup decisions."""
    basis, source = _compute_basis(source_url=source_url, raw_text=raw_text)
    return {
        "fingerprint": hashlib.sha256(basis.encode("utf-8")).hexdigest(),
        "normalized_basis": basis,
        "basis_source": source,
    }
```

No new imports needed — `hashlib` is already imported at the top of `fingerprint.py`.

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-mcp && python -m pytest tests/test_fingerprint.py -v`
Expected: PASS — all 7 previous tests plus the 3 new ones (10 total in this file). No `DATABASE_URL` needed — this file has no DB dependency.

- [ ] **Step 5: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL for the DB-backed suites]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — 24 tests total (21 existing + 3 new). If `DATABASE_URL` is unset, the DB-gated tests in `test_store.py`/`test_server.py` skip rather than fail; `test_fingerprint.py`'s 10 tests always run.

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-mcp/app/fingerprint.py openbrain-mcp/tests/test_fingerprint.py
git commit -m "feat(fingerprint): add content_fingerprint_debug for introspection"
```

---

### Task 2: MCP tool — `compute_fingerprint`

**Files:**
- Modify: `openbrain-mcp/app/server.py` (add import; insert new tool after `find_near_duplicates`, which currently ends at line 70, before `class BearerAuthMiddleware`)

- [ ] **Step 1: Add the import** **[repo]**

Change the import line near the top of `openbrain-mcp/app/server.py`:

```python
from app import store
```

to:

```python
from app import store
from app.fingerprint import content_fingerprint_debug
```

- [ ] **Step 2: Add the tool** **[repo]**

Insert into `openbrain-mcp/app/server.py`, immediately after the `find_near_duplicates` tool function (ends at line 70) and before `class BearerAuthMiddleware`:

```python
@mcp.tool()
def compute_fingerprint(raw_text: str, source_url: str | None = None) -> dict:
    """Show the dedup fingerprint `save` would compute for this input, and the
    normalized string it's based on. Read-only, no DB access -- does not check
    whether this fingerprint already exists (use `save` or `find_near_duplicates`
    for that)."""
    return content_fingerprint_debug(source_url=source_url, raw_text=raw_text)
```

Note this tool calls `content_fingerprint_debug` directly rather than going through `store.py` — it
needs no `conn`, unlike every other tool in this file.

- [ ] **Step 3: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL for the DB-backed suites]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — all 24 tests (no dedicated server-level test is added for this tool, consistent
with `find_near_duplicates` and the other tools: `tests/test_server.py` only covers auth/health/
host-header behavior of the Starlette app, not per-tool logic).

- [ ] **Step 4: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(mcp): expose compute_fingerprint as an MCP tool"
```

---

### Task 3: Document the new tool in `README.md`

**Files:**
- Modify: `README.md` (tool table, count references, repo-layout comments)

- [ ] **Step 1: Rename the tool-count heading and table** **[repo]**

Change the heading on line 115 from `## The seven MCP tools` to `## The eight MCP tools`, and add
this row to the end of the table (after the `find_near_duplicates` row, line 128):

```markdown
| `compute_fingerprint(raw_text, source_url?)` | Read-only, no DB access. Shows the SHA-256 dedup fingerprint `save` would compute for this input, plus the normalized string it's based on — for debugging the fingerprint mechanism, not for checking against existing captures. |
```

- [ ] **Step 2: Update test-count references** **[repo]**

Change line 131 from:

```markdown
(`openbrain-mcp/tests/`, 21 tests, run against a real Postgres+pgvector instance).
```

to:

```markdown
(`openbrain-mcp/tests/`, 24 tests; `compute_fingerprint`'s tests need no database).
```

Change line 146 from:

```markdown
  tests/                     # 21 tests, pytest
```

to:

```markdown
  tests/                     # 24 tests, pytest
```

- [ ] **Step 3: Update the remaining "seven" references and repo-layout comments** **[repo]**

Change line 82 from:

```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes seven tools over the
```

to:

```markdown
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes eight tools over the
```

Change line 191 from:

```markdown
`http://localhost:8080/mcp` with that bearer token and call the seven tools directly — useful for
```

to:

```markdown
`http://localhost:8080/mcp` with that bearer token and call the eight tools directly — useful for
```

Change the repo-layout comment on line 140 from:

```
    fingerprint.py   # content_fingerprint() -- SHA-256 dedup key
```

to:

```
    fingerprint.py   # content_fingerprint() / content_fingerprint_debug() -- SHA-256 dedup key
```

Change the repo-layout comment on line 144 from:

```
    server.py             # the 7 MCP tools + bearer auth + /health
```

to:

```
    server.py             # the 8 MCP tools + bearer auth + /health
```

Leave line 21 (the historical Phase-2 status row reading "6 MCP tools, TDD, 18 tests") untouched —
it's an intentional point-in-time snapshot of what Phase 2 originally shipped, per the reviewer
decision recorded when capability 1 was merged.

- [ ] **Step 4: Commit** **[repo]**

```bash
git add README.md
git commit -m "docs: document compute_fingerprint in the MCP tool table"
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

- [ ] **Step 2: Call the tool from a connected MCP client** **[repo]**

From Claude Code or Claude Desktop (already configured per README Phase 6), with the local
server's bearer token, ask:

```
"Use openbrain's compute_fingerprint tool on raw_text='hello world' with no source_url, and tell
me what it returns."
```

Expected: a dict with `fingerprint` (a 64-char hex string), `normalized_basis` equal to
`"hello world"`, and `basis_source` equal to `"text"`.

- [ ] **Step 3: Confirm URL-based input and read-only behavior** **[repo]**

Run `stats` before, then ask the client:

```
"Now call compute_fingerprint with source_url='https://www.Example.com/Page/' and any raw_text."
```

Expected: `normalized_basis` equal to `"example.com/page"`, `basis_source` equal to `"url"`. Run
`stats` again — the `total` count must be unchanged, confirming the tool never touches the
database (this is stronger than capability 1's read-only guarantee: this tool makes no DB calls
at all, not even a read).

- [ ] **Step 4: Tear down any throwaway local resources** **[repo]**

If the local compose stack was brought up only for this smoke test (not already running for other
reasons), bring it back down: `docker compose -f docker-compose.openbrain.yml down`.
