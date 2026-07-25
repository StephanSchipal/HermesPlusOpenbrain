# OpenBrain Web GUI — Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Amendment (2026-07-25):** Task 6's Claude Haiku subject-line generator (below) was replaced
> post-implementation with plain truncation of the `summary` field — no model call, no Anthropic
> API key. See the design spec's amendment note for why. This plan's Task 6 text is left as a
> historical record of what was originally built; it no longer matches the shipped code.

**Goal:** Build a single-user web GUI (React frontend, FastAPI backend) for browsing, searching,
editing, and deleting `openbrain-db` captures, plus saved search prompts and a delete audit log —
Phase 1 of the three-phase plan in `planGUI.md`.

**Architecture:** One new container (`openbrain-gui`) combining a Vite-built React SPA (served as
static files) with a FastAPI backend. The backend is the only thing holding secrets
(`OPENBRAIN_TOKEN`, the Anthropic key) and is the only thing that talks to `openbrain-mcp` (as an
MCP client, same bearer-token pattern Claude Desktop/Code already use) or to Claude Haiku (subject
lines). A small SQLite file (`gui.db`) owned directly by the backend holds saved prompts and the
delete log — entirely separate from `openbrain-db`. One new MCP tool, `list_keywords()`, is added
to `openbrain-mcp` first (Tasks 1-3) since everything else in this plan depends on it; it's kept in
this same plan/worktree rather than its own cycle because the GUI can't be meaningfully tested
without it.

**Tech Stack:** Python 3.11 (FastAPI, `mcp` SDK, `anthropic` SDK, stdlib `sqlite3`), React 18 +
Vite (plain JS, no TypeScript — YAGNI for a single-user, one-screen tool with no planned automated
frontend tests), `pytest` for all Python tests, Docker multi-stage build, Traefik basic-auth.

**Reference:** Design spec at `docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`.

---

## File structure (files touched by this plan)

```
openbrain-mcp/
  app/store.py         # MODIFY: add list_keywords()
  app/server.py         # MODIFY: add list_keywords MCP tool
  tests/test_store.py    # MODIFY: add list_keywords tests
README.md                 # MODIFY: tool table, counts (10 -> 11 tools)

openbrain-gui/
  Dockerfile               # CREATE: multi-stage (frontend build -> backend image)
  backend/
    pyproject.toml           # CREATE
    app/
      __init__.py              # CREATE
      config.py                 # CREATE: env vars
      db.py                      # CREATE: SQLite connection + schema
      mcp_client.py               # CREATE: openbrain-mcp client wrapper
      subject_line.py               # CREATE: Claude Haiku subject-line generator + fallback
      prompts_store.py                # CREATE: saved-prompts CRUD (SQLite)
      delete_log_store.py               # CREATE: delete-log insert/list (SQLite)
      routes.py                           # CREATE: all /api/* FastAPI routes
      main.py                               # CREATE: app factory, static file mount
    tests/
      test_db.py                            # CREATE
      test_mcp_client.py                      # CREATE
      test_subject_line.py                      # CREATE
      test_prompts_store.py                       # CREATE
      test_delete_log_store.py                      # CREATE
      test_routes.py                                  # CREATE
  frontend/
    package.json               # CREATE
    vite.config.js               # CREATE
    index.html                     # CREATE
    src/
      main.jsx                       # CREATE
      App.jsx                          # CREATE
      api.js                             # CREATE
      index.css                            # CREATE
      ThemeToggle.jsx                        # CREATE
      PromptBar.jsx                             # CREATE
      KeywordPanel.jsx                            # CREATE
      ResultGrid.jsx                                # CREATE
      DeleteLogView.jsx                               # CREATE
      ChangePopup.jsx                                   # CREATE

deploy/
  docker-compose.openbrain.yml   # MODIFY: add openbrain-gui service + volume
  .env.example                     # MODIFY: add new env vars

OpenbrainAddition.md                # MODIFY: status section
```

---

### Task 1: Store layer — `list_keywords`

**Files:**
- Modify: `openbrain-mcp/app/store.py` (insert after `classify_captures`, before `fetch_recent`)
- Modify: `openbrain-mcp/tests/test_store.py` (append at the end of the file)

- [ ] **Step 1: Write the failing tests** **[repo]**

Append to `openbrain-mcp/tests/test_store.py`:

```python
def test_list_keywords_returns_corpus_wide_counts_sorted_by_frequency():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="first note about AI agents",
                           keywords=["ai", "agents"], source="youtube")
        store.save_capture(conn, raw_text="b", summary="second note about AI safety",
                           keywords=["ai", "safety"], source="youtube")
        store.save_capture(conn, raw_text="c", summary="a note about gardening",
                           keywords=["garden"], source="other")
    with get_conn() as conn:
        result = store.list_keywords(conn)
    assert result[0] == {"keyword": "ai", "count": 2}
    assert {"keyword": "agents", "count": 1} in result
    assert {"keyword": "safety", "count": 1} in result
    assert {"keyword": "garden", "count": 1} in result
    assert len(result) == 4

def test_list_keywords_aggregates_case_insensitively():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="first note about AI agents",
                           keywords=["AI"], source="other")
        store.save_capture(conn, raw_text="b", summary="second note about ai safety",
                           keywords=["ai"], source="other")
    with get_conn() as conn:
        result = store.list_keywords(conn)
    # Two captures tagged "AI"/"ai" collapse into one lowercase entry --
    # normalize_keywords() only dedupes case-insensitively *within* a single
    # capture's own keyword list, so across captures "AI" and "ai" would
    # otherwise show up as two separate rows in a corpus-wide list, which
    # would look broken in the keyword panel.
    assert result == [{"keyword": "ai", "count": 2}]

def test_list_keywords_returns_empty_list_for_empty_corpus():
    _clean()
    with get_conn() as conn:
        result = store.list_keywords(conn)
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v -k list_keywords`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'list_keywords'`

- [ ] **Step 3: Write the implementation** **[repo]**

Insert into `openbrain-mcp/app/store.py`, immediately after the `classify_captures` function and
before `fetch_recent`:

```python
def list_keywords(conn: psycopg.Connection) -> list[dict]:
    """List every distinct keyword across all captures with its frequency,
    most-frequent first. Read-only. Aggregates case-insensitively (lowercased)
    since normalize_keywords() only dedupes within a single capture's own
    list, not across captures -- without this, "AI" (from one capture) and
    "ai" (from another) would show up as two separate rows here."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT lower(keyword) AS kw, count(*) AS n
            FROM captures, unnest(keywords) AS keyword
            GROUP BY kw
            ORDER BY n DESC, kw ASC
            """
        )
        rows = cur.fetchall()
    return [{"keyword": r[0], "count": r[1]} for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: PASS — all previous tests plus the 3 new ones.

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(store): add list_keywords (corpus-wide keyword frequency)"
```

---

### Task 2: MCP tool — `list_keywords`

**Files:**
- Modify: `openbrain-mcp/app/server.py` (insert after `classify_captures`, before
  `class BearerAuthMiddleware`)

- [ ] **Step 1: Add the tool** **[repo]**

Insert into `openbrain-mcp/app/server.py`, immediately after the `classify_captures` tool function
and before `class BearerAuthMiddleware`:

```python
@mcp.tool()
def list_keywords() -> list[dict]:
    """List every distinct keyword across all captures with its frequency,
    most-frequent first. Read-only."""
    with get_conn() as conn:
        return store.list_keywords(conn)
```

Follows the `server → store` pattern of nine of the ten existing tools (`compute_fingerprint` is
the one documented DB-free exception). No dedicated server-level test is added, consistent with
`find_near_duplicates`/`cluster_captures`/`classify_captures`: `tests/test_server.py` only covers
auth/health/host-header behavior, not per-tool logic.

- [ ] **Step 2: Run the full test suite to confirm no regressions** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: PASS — all tests, including the 3 new `list_keywords` tests (36 total in `openbrain-mcp`).

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(mcp): expose list_keywords as an MCP tool"
```

---

### Task 3: Document the new tool in `README.md`

**Files:**
- Modify: `README.md` (tool table, tool-count references)

- [ ] **Step 1: Rename the tool-count heading and table** **[repo]**

Change the heading `## The ten MCP tools` to `## The eleven MCP tools`, and add this row to the end
of the table (after the `classify_captures` row):

```markdown
| `list_keywords()` | Read-only. Lists every distinct keyword across all captures with its frequency, most-frequent first (aggregated case-insensitively). Powers the OpenBrain GUI's keyword panel. |
```

- [ ] **Step 2: Update test-count and tool-count references** **[repo]**

Change `(openbrain-mcp/tests/, 33 tests, ...)` to `(openbrain-mcp/tests/, 36 tests, ...)`, and
`tests/                     # 33 tests, pytest` to `tests/                     # 36 tests, pytest`.

Change `exposes ten tools over the` to `exposes eleven tools over the`, and `call the ten tools
directly` to `call the eleven tools directly`.

Update the repo-layout comment:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates/cluster_captures/classify_captures -- the only file with SQL
```
to:
```
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates/cluster_captures/classify_captures/list_keywords -- the only file with SQL
```
and:
```
    server.py             # the 10 MCP tools + bearer auth + /health
```
to:
```
    server.py             # the 11 MCP tools + bearer auth + /health
```

**IMPORTANT — do NOT touch** the "Status" table's historical Phase-2 row ("6 MCP tools, TDD, 18
tests") — intentional point-in-time snapshot, same rule as every previous capability.

- [ ] **Step 3: Commit** **[repo]**

```bash
git add README.md
git commit -m "docs: document list_keywords in the MCP tool table"
```

---

### Task 4: Backend scaffold — config, SQLite schema

**Files:**
- Create: `openbrain-gui/backend/pyproject.toml`
- Create: `openbrain-gui/backend/app/__init__.py`
- Create: `openbrain-gui/backend/app/config.py`
- Create: `openbrain-gui/backend/app/db.py`
- Create: `openbrain-gui/backend/tests/test_db.py`

- [ ] **Step 1: Create the backend project file** **[repo]**

Create `openbrain-gui/backend/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "openbrain-gui-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "mcp>=1.2.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]

[tool.setuptools]
packages = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `openbrain-gui/backend/app/__init__.py` (empty file).

Install it into the environment: `cd openbrain-gui/backend && pip install -e ".[dev]"`

- [ ] **Step 2: Create config.py** **[repo]**

Create `openbrain-gui/backend/app/config.py`:

```python
# app/config.py
import os

OPENBRAIN_MCP_URL = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8080/mcp")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUBJECT_LINE_MODEL = os.environ.get("SUBJECT_LINE_MODEL", "claude-haiku-4-5-20251001")
GUI_DB_PATH = os.environ.get("GUI_DB_PATH", "gui.db")
DEFAULT_SEARCH_K = 25
```

- [ ] **Step 3: Write the failing test for db.py** **[repo]**

Create `openbrain-gui/backend/tests/test_db.py`:

```python
# tests/test_db.py
from app.db import init_db, get_conn

def test_init_db_creates_prompts_and_delete_log_tables(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"prompts", "delete_log"} <= tables

def test_init_db_is_idempotent(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    init_db(db_path)  # must not raise on a second call (CREATE TABLE IF NOT EXISTS)
```

- [ ] **Step 4: Run test to verify it fails** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 5: Write db.py** **[repo]**

Create `openbrain-gui/backend/app/db.py`:

```python
# app/db.py
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from app.config import GUI_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delete_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL,
    subject_line TEXT,
    keywords TEXT,
    source_url TEXT,
    captured_at TEXT,
    deleted_at TEXT NOT NULL
);
"""

@contextmanager
def get_conn(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or GUI_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(path: str | None = None) -> None:
    with get_conn(path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
```

`path` is looked up as a plain function argument (falling back to the module-level `GUI_DB_PATH`
import at *call* time, not baked in as a default value) so tests can monkeypatch
`app.db.GUI_DB_PATH` and have it take effect.

- [ ] **Step 6: Run test to verify it passes** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 7: Commit** **[repo]**

```bash
git add openbrain-gui/backend/pyproject.toml openbrain-gui/backend/app/__init__.py \
        openbrain-gui/backend/app/config.py openbrain-gui/backend/app/db.py \
        openbrain-gui/backend/tests/test_db.py
git commit -m "feat(gui-backend): scaffold project, config, and SQLite schema"
```

---

### Task 5: MCP client wrapper

**Files:**
- Create: `openbrain-gui/backend/app/mcp_client.py`
- Create: `openbrain-gui/backend/tests/test_mcp_client.py`

> **Context for the implementer:** `openbrain-mcp`'s tools return a mix of bare `dict` and
> `list[dict]` return-type annotations. In the installed `mcp` SDK (verified against 1.27.0), a
> **bare, un-parameterized `dict` return type does not produce `structuredContent`** — only
> `list[...]`/`dict[str, X]`/`Union` do (see `mcp.server.fastmcp.utilities.func_metadata`'s
> `_try_create_model_and_schema`). Concretely: `stats`, `delete`, and `update` (bare `dict`) must be
> parsed from the single unstructured text block; `search` and `list_keywords` (`list[dict]`) get
> real `structuredContent`, wrapped as `{"result": [...]}`. This is *not* guesswork — it comes from
> reading the installed library's source directly. Two separate parsing helpers below encode this
> so each call site stays unambiguous about which shape it expects.

- [ ] **Step 1: Write the failing tests** **[repo]**

Create `openbrain-gui/backend/tests/test_mcp_client.py`:

```python
# tests/test_mcp_client.py
import pytest
from mcp import types
from app.mcp_client import parse_dict_result, parse_list_result, OpenBrainMCPError

def _text_result(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=is_error,
    )

def test_parse_dict_result_decodes_single_text_block():
    result = _text_result('{"total": 3, "by_source": {}}')
    assert parse_dict_result(result) == {"total": 3, "by_source": {}}

def test_parse_dict_result_raises_on_error():
    result = _text_result("boom", is_error=True)
    with pytest.raises(OpenBrainMCPError, match="boom"):
        parse_dict_result(result)

def test_parse_list_result_reads_structured_content():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="ignored for list results")],
        structuredContent={"result": [{"keyword": "ai", "count": 3}]},
    )
    assert parse_list_result(result) == [{"keyword": "ai", "count": 3}]

def test_parse_list_result_raises_on_error():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="nope")], isError=True,
    )
    with pytest.raises(OpenBrainMCPError, match="nope"):
        parse_list_result(result)
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mcp_client'`

- [ ] **Step 3: Write mcp_client.py** **[repo]**

Create `openbrain-gui/backend/app/mcp_client.py`:

```python
# app/mcp_client.py
"""Thin MCP client wrapper around openbrain-mcp's Streamable HTTP endpoint.

Opens a fresh connection + session per call rather than holding a
persistent session across requests -- simplest option at this project's
personal, low-concurrency scale, and avoids reconnection/session-expiry
logic entirely.
"""
import json
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from app.config import OPENBRAIN_MCP_URL, OPENBRAIN_TOKEN

class OpenBrainMCPError(RuntimeError):
    """Raised when openbrain-mcp returns an error result for a tool call."""

async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    # A plain httpx.AsyncClient (not mcp's own create_mcp_http_client helper,
    # which lives under a private `_httpx_utils` module) -- this is the
    # documented way to attach custom headers to streamable_http_client, per
    # its own docstring, without depending on an underscore-prefixed
    # (unstable) internal API.
    headers = {"Authorization": f"Bearer {OPENBRAIN_TOKEN}"}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0)) as http_client:
        async with streamable_http_client(OPENBRAIN_MCP_URL, http_client=http_client) as (
            read, write, _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

def _error_message(result: types.CallToolResult) -> str:
    return " ".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    ) or "unknown error"

def parse_dict_result(result: types.CallToolResult) -> dict:
    """For tools with a bare `dict` return annotation (`stats`, `delete`,
    `update`) -- no structuredContent in this mcp SDK version, so parse the
    single unstructured text block instead (see module docstring context
    in the implementation plan for why)."""
    if result.isError:
        raise OpenBrainMCPError(_error_message(result))
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)

def parse_list_result(result: types.CallToolResult) -> list[dict]:
    """For tools with a `list[dict]` return annotation (`search`,
    `list_keywords`) -- these DO get structuredContent, wrapped as
    {"result": [...]}."""
    if result.isError:
        raise OpenBrainMCPError(_error_message(result))
    assert result.structuredContent is not None, (
        "expected structuredContent for a list-returning tool"
    )
    return result.structuredContent["result"]
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_mcp_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/mcp_client.py openbrain-gui/backend/tests/test_mcp_client.py
git commit -m "feat(gui-backend): add openbrain-mcp client wrapper"
```

---

### Task 6: Subject-line generator

**Files:**
- Create: `openbrain-gui/backend/app/subject_line.py`
- Create: `openbrain-gui/backend/tests/test_subject_line.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Create `openbrain-gui/backend/tests/test_subject_line.py`:

```python
# tests/test_subject_line.py
import asyncio
import app.subject_line as subject_line

def test_truncate_fallback_leaves_short_summary_unchanged():
    assert subject_line.truncate_fallback("short summary here") == "short summary here"

def test_truncate_fallback_truncates_at_ten_words_by_default():
    summary = "one two three four five six seven eight nine ten eleven"
    assert subject_line.truncate_fallback(summary) == (
        "one two three four five six seven eight nine ten..."
    )

class _FakeTextBlock:
    def __init__(self, text):
        self.text = text

class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]

class _FakeMessages:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error

    async def create(self, **kwargs):
        if self._error:
            raise self._error
        return _FakeMessage(self._text)

class _FakeAnthropic:
    def __init__(self, text=None, error=None):
        self.messages = _FakeMessages(text=text, error=error)

def test_generate_subject_line_uses_model_output(monkeypatch):
    monkeypatch.setattr(
        subject_line.anthropic, "AsyncAnthropic",
        lambda **kwargs: _FakeAnthropic(text="Sarah's career pivot"),
    )
    result = asyncio.run(subject_line.generate_subject_line("Sarah is considering a pivot"))
    assert result == "Sarah's career pivot"

def test_generate_subject_line_falls_back_on_api_error(monkeypatch):
    monkeypatch.setattr(
        subject_line.anthropic, "AsyncAnthropic",
        lambda **kwargs: _FakeAnthropic(error=RuntimeError("rate limited")),
    )
    summary = "one two three four five six seven eight nine ten eleven"
    result = asyncio.run(subject_line.generate_subject_line(summary))
    assert result == subject_line.truncate_fallback(summary)
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_subject_line.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.subject_line'`

- [ ] **Step 3: Write subject_line.py** **[repo]**

Create `openbrain-gui/backend/app/subject_line.py`:

```python
# app/subject_line.py
"""Generates a short subject line from a capture's summary using Claude
Haiku, falling back to a truncation heuristic on any failure -- one
slow/failed row must not block the rest of a search's results (design
spec section 7, "Error handling")."""
import anthropic
from app.config import ANTHROPIC_API_KEY, SUBJECT_LINE_MODEL

_PROMPT = (
    "Write a short, plain subject line (under 8 words, no quotes, no "
    "trailing period) that captures the essence of this note:\n\n{summary}"
)

def truncate_fallback(summary: str, max_words: int = 10) -> str:
    words = summary.split()
    if len(words) <= max_words:
        return summary
    return " ".join(words[:max_words]) + "..."

async def generate_subject_line(summary: str) -> str:
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=SUBJECT_LINE_MODEL,
            max_tokens=30,
            messages=[{"role": "user", "content": _PROMPT.format(summary=summary)}],
        )
        text = response.content[0].text.strip()
        return text or truncate_fallback(summary)
    except Exception:
        return truncate_fallback(summary)
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_subject_line.py -v`
Expected: PASS

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/subject_line.py openbrain-gui/backend/tests/test_subject_line.py
git commit -m "feat(gui-backend): add Claude Haiku subject-line generator with fallback"
```

---

### Task 7: Saved-prompts store

**Files:**
- Create: `openbrain-gui/backend/app/prompts_store.py`
- Create: `openbrain-gui/backend/tests/test_prompts_store.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Create `openbrain-gui/backend/tests/test_prompts_store.py`:

```python
# tests/test_prompts_store.py
from app.db import init_db
from app import prompts_store

def test_create_list_delete_prompt_roundtrip(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    created = prompts_store.create_prompt("ai agents this week", path=db_path)
    assert created["text"] == "ai agents this week"
    listed = prompts_store.list_prompts(path=db_path)
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert prompts_store.delete_prompt(created["id"], path=db_path) is True
    assert prompts_store.list_prompts(path=db_path) == []

def test_delete_prompt_returns_false_when_missing(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    assert prompts_store.delete_prompt(999, path=db_path) is False

def test_list_prompts_newest_first(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    prompts_store.create_prompt("first", path=db_path)
    prompts_store.create_prompt("second", path=db_path)
    listed = prompts_store.list_prompts(path=db_path)
    assert [p["text"] for p in listed] == ["second", "first"]
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_prompts_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.prompts_store'`

- [ ] **Step 3: Write prompts_store.py** **[repo]**

Create `openbrain-gui/backend/app/prompts_store.py`:

```python
# app/prompts_store.py
"""CRUD for the `prompts` table (saved search prompts) -- no MCP call
involved, this is GUI-local bookkeeping, not capture data. The dropdown
label is just the prompt's own text, truncated client-side -- no separate
name field (design spec section 3, "Saved prompts")."""
from datetime import datetime, timezone
from app.db import get_conn

def create_prompt(text: str, *, path: str | None = None) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        cur = conn.execute(
            "INSERT INTO prompts (text, created_at) VALUES (?, ?)", (text, created_at)
        )
        conn.commit()
        return {"id": cur.lastrowid, "text": text, "created_at": created_at}

def list_prompts(*, path: str | None = None) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute(
            "SELECT id, text, created_at FROM prompts ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(row) for row in rows]

def delete_prompt(prompt_id: int, *, path: str | None = None) -> bool:
    with get_conn(path) as conn:
        cur = conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
        conn.commit()
        return cur.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_prompts_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/prompts_store.py openbrain-gui/backend/tests/test_prompts_store.py
git commit -m "feat(gui-backend): add saved-prompts SQLite store"
```

---

### Task 8: Delete-log store

**Files:**
- Create: `openbrain-gui/backend/app/delete_log_store.py`
- Create: `openbrain-gui/backend/tests/test_delete_log_store.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Create `openbrain-gui/backend/tests/test_delete_log_store.py`:

```python
# tests/test_delete_log_store.py
from app.db import init_db
from app import delete_log_store

def test_log_deletion_then_list_returns_snapshot(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    delete_log_store.log_deletion(
        capture_id="abc-123", subject_line="Sarah's career pivot",
        keywords=["career", "consulting"], source_url="https://example.com/post",
        captured_at="2026-07-20T14:32:00+00:00", path=db_path,
    )
    entries = delete_log_store.list_deletions(path=db_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["capture_id"] == "abc-123"
    assert entry["keywords"] == ["career", "consulting"]
    assert entry["subject_line"] == "Sarah's career pivot"

def test_list_deletions_newest_first(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    delete_log_store.log_deletion(
        capture_id="first", subject_line=None, keywords=[], source_url=None,
        captured_at=None, path=db_path,
    )
    delete_log_store.log_deletion(
        capture_id="second", subject_line=None, keywords=[], source_url=None,
        captured_at=None, path=db_path,
    )
    entries = delete_log_store.list_deletions(path=db_path)
    assert [e["capture_id"] for e in entries] == ["second", "first"]
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_delete_log_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.delete_log_store'`

- [ ] **Step 3: Write delete_log_store.py** **[repo]**

Create `openbrain-gui/backend/app/delete_log_store.py`:

```python
# app/delete_log_store.py
"""Insert/list for the `delete_log` table -- the GUI's audit trail for
captures deleted via the Delete button. Written *before* the corresponding
openbrain-mcp `delete()` call runs (design spec section 3, "Deletion audit
trail") so a silent deletion with zero log entry can't happen; worst case
on a subsequent MCP failure is an orphan log entry, never the reverse."""
import json
from datetime import datetime, timezone
from app.db import get_conn

def log_deletion(*, capture_id: str, subject_line: str | None, keywords: list[str],
                 source_url: str | None, captured_at: str | None,
                 path: str | None = None) -> dict:
    deleted_at = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO delete_log
                (capture_id, subject_line, keywords, source_url, captured_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (capture_id, subject_line, json.dumps(keywords), source_url, captured_at, deleted_at),
        )
        conn.commit()
        return {"id": cur.lastrowid, "capture_id": capture_id, "deleted_at": deleted_at}

def list_deletions(*, path: str | None = None) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute(
            """
            SELECT id, capture_id, subject_line, keywords, source_url, captured_at, deleted_at
            FROM delete_log ORDER BY deleted_at DESC, id DESC
            """
        ).fetchall()
    return [
        {**dict(row), "keywords": json.loads(row["keywords"] or "[]")}
        for row in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_delete_log_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/delete_log_store.py \
        openbrain-gui/backend/tests/test_delete_log_store.py
git commit -m "feat(gui-backend): add delete-log SQLite store"
```

---

### Task 9: FastAPI routes

**Files:**
- Create: `openbrain-gui/backend/app/routes.py`
- Create: `openbrain-gui/backend/app/main.py` (needed by the tests' `create_app` import)
- Create: `openbrain-gui/backend/tests/test_routes.py`

- [ ] **Step 1: Write main.py first (routes.py needs an app to mount into)** **[repo]**

Create `openbrain-gui/backend/app/main.py`:

```python
# app/main.py
"""FastAPI app entrypoint: mounts /api routes and serves the built React
frontend as static files (same-origin, single container -- design spec
section 4, "Architecture")."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes import router as api_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="openbrain-gui-backend")
    app.include_router(api_router)
    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
    return app

app = create_app()
```

`_STATIC_DIR` won't exist yet during backend-only development/tests (the frontend hasn't been
built) — the `is_dir()` guard means the mount is simply skipped, so `/api/*` routes still work.

- [ ] **Step 2: Write the failing route tests** **[repo]**

Create `openbrain-gui/backend/tests/test_routes.py`:

```python
# tests/test_routes.py
"""Route-level tests: FastAPI TestClient with openbrain-mcp and the
subject-line generator mocked at the module boundary (app.mcp_client /
app.subject_line) -- no real network calls. Real SQLite (a tmp_path file)
is used for prompts/delete-log, since that IS this backend's own data."""
import json
import pytest
from fastapi.testclient import TestClient
from mcp import types

import app.db as db_module
import app.mcp_client as mcp_client_module
import app.subject_line as subject_line_module
from app.main import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "GUI_DB_PATH", str(tmp_path / "gui.db"))
    return TestClient(create_app())

def _dict_result(payload: dict) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))])

def _list_result(payload: list) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="unstructured, ignored")],
        structuredContent={"result": payload},
    )

def test_get_stats_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "stats" and arguments == {}
        return _dict_result({"total": 3, "by_source": {"youtube": 3}})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 3, "by_source": {"youtube": 3}}

def test_get_keywords_filters_case_insensitively(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_keywords"
        return _list_result([{"keyword": "AI", "count": 5}, {"keyword": "cooking", "count": 2}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/keywords", params={"filter": "ai"})
    assert resp.status_code == 200
    assert resp.json() == [{"keyword": "AI", "count": 5}]

def test_search_adds_subject_line_per_row(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "search" and arguments == {"query": "career notes", "k": 25}
        return _list_result(
            [{"id": "abc", "summary": "Sarah is considering a pivot", "keywords": ["career"]}]
        )
    async def fake_generate_subject_line(summary):
        assert summary == "Sarah is considering a pivot"
        return "Sarah's career pivot"
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    monkeypatch.setattr(subject_line_module, "generate_subject_line", fake_generate_subject_line)
    resp = client.post("/api/search", json={"query": "career notes"})
    assert resp.status_code == 200
    assert resp.json() == [{
        "id": "abc", "summary": "Sarah is considering a pivot",
        "keywords": ["career"], "subject_line": "Sarah's career pivot",
    }]

def test_delete_capture_logs_before_calling_mcp(client, monkeypatch):
    calls = []
    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        return _dict_result({"id": "abc", "deleted": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/captures/abc/delete", json={
        "subject_line": "Sarah's career pivot", "keywords": ["career"],
        "source_url": "https://example.com/post", "created_at": "2026-07-20T14:32:00+00:00",
    })
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "deleted": True}
    assert calls == [("delete", {"id": "abc"})]
    log = client.get("/api/delete-log").json()
    assert len(log) == 1
    assert log[0]["capture_id"] == "abc"
    assert log[0]["keywords"] == ["career"]

def test_update_capture_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {"id": "abc", "summary": "new summary", "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"summary": "new summary", "keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}

def test_prompts_crud_roundtrip(client):
    created = client.post("/api/prompts", json={"text": "ai agents this week"}).json()
    assert created["text"] == "ai agents this week"
    listed = client.get("/api/prompts").json()
    assert len(listed) == 1
    resp = client.delete(f"/api/prompts/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/prompts").json() == []

def test_delete_missing_prompt_returns_404(client):
    resp = client.delete("/api/prompts/999")
    assert resp.status_code == 404

def test_mcp_connection_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 502
```

- [ ] **Step 3: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routes'`

- [ ] **Step 4: Write routes.py** **[repo]**

Create `openbrain-gui/backend/app/routes.py`:

```python
# app/routes.py
"""FastAPI routes for the OpenBrain GUI backend. Talks to openbrain-mcp
for capture data (search/stats/keywords/delete/update) and to gui.db
(SQLite, via prompts_store/delete_log_store) for GUI-local bookkeeping
(saved prompts, delete log) -- design spec section 4, "Architecture"."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import mcp_client, prompts_store, delete_log_store, subject_line
from app.mcp_client import OpenBrainMCPError
from app.config import DEFAULT_SEARCH_K

router = APIRouter(prefix="/api")

class SearchRequest(BaseModel):
    query: str
    k: int = DEFAULT_SEARCH_K

class UpdateRequest(BaseModel):
    summary: str | None = None
    keywords: list[str] | None = None

class PromptRequest(BaseModel):
    text: str

class CaptureSnapshot(BaseModel):
    subject_line: str | None = None
    keywords: list[str] = []
    source_url: str | None = None
    created_at: str | None = None

async def _call(name: str, arguments: dict):
    try:
        return await mcp_client.call_tool(name, arguments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"openbrain-mcp unreachable: {exc}") from exc

@router.get("/stats")
async def get_stats():
    result = await _call("stats", {})
    try:
        return mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.get("/keywords")
async def get_keywords(filter: str = ""):
    result = await _call("list_keywords", {})
    try:
        keywords = mcp_client.parse_list_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if filter:
        needle = filter.lower()
        keywords = [k for k in keywords if needle in k["keyword"].lower()]
    return keywords

@router.post("/search")
async def search(body: SearchRequest):
    result = await _call("search", {"query": body.query, "k": body.k})
    try:
        rows = mcp_client.parse_list_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Sequential, not parallel: per-row Claude calls are explicitly allowed
    # to be slow for Phase 1 (design spec section 3) -- keeping this simple
    # avoids concurrent-rate-limit surprises for a handful of rows at a time.
    for row in rows:
        row["subject_line"] = await subject_line.generate_subject_line(row["summary"])
    return rows

@router.post("/captures/{capture_id}/delete")
async def delete_capture(capture_id: str, snapshot: CaptureSnapshot):
    # Snapshot written BEFORE the MCP delete call -- see delete_log_store's
    # module docstring for why this ordering is load-bearing.
    delete_log_store.log_deletion(
        capture_id=capture_id,
        subject_line=snapshot.subject_line,
        keywords=snapshot.keywords,
        source_url=snapshot.source_url,
        captured_at=snapshot.created_at,
    )
    result = await _call("delete", {"id": capture_id})
    try:
        return mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.patch("/captures/{capture_id}")
async def update_capture(capture_id: str, body: UpdateRequest):
    result = await _call("update", {
        "id": capture_id, "summary": body.summary, "keywords": body.keywords,
    })
    try:
        return mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.get("/prompts")
def get_prompts():
    return prompts_store.list_prompts()

@router.post("/prompts")
def post_prompt(body: PromptRequest):
    return prompts_store.create_prompt(body.text)

@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int):
    deleted = prompts_store.delete_prompt(prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="prompt not found")
    return {"id": prompt_id, "deleted": True}

@router.get("/delete-log")
def get_delete_log():
    return delete_log_store.list_deletions()
```

Note: `mcp_client`, `subject_line`, `prompts_store`, and `delete_log_store` are imported as
*modules* (not `from ... import specific_function`) deliberately — the tests monkeypatch functions
like `mcp_client.call_tool` on the module object, which only takes effect at the call sites above
because they look the function up through the module each time, not through a locally-bound name.

Note on `delete_capture`: if `log_deletion` raises (e.g. `gui.db` unreachable), the exception
propagates as an uncaught 500 rather than being swallowed — this is intentional, not a missing
try/except. Given the snapshot-then-delete ordering is the whole point of Task 8, silently skipping
a failed log write and calling `delete()` anyway would defeat it; failing loudly here is correct
(see the corresponding note in the design spec's error-handling section).

- [ ] **Step 5: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/ -v`
Expected: PASS — 23 tests total across every file created in Tasks 4-9 (2 in `test_db.py`, 4 in
`test_mcp_client.py`, 4 in `test_subject_line.py`, 3 in `test_prompts_store.py`, 2 in
`test_delete_log_store.py`, 8 in `test_routes.py`).

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/main.py openbrain-gui/backend/app/routes.py \
        openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui-backend): add FastAPI routes and app entrypoint"
```

---

### Task 10: Local backend boot check

**Files:** none (verification only)

- [ ] **Step 1: Run the backend standalone (no real openbrain-mcp needed yet)** **[repo]**

```bash
cd openbrain-gui/backend
GUI_DB_PATH=/tmp/gui-dev.db OPENBRAIN_TOKEN=dev-placeholder \
  python -m uvicorn app.main:app --port 8000
```

Expected: starts without errors, logs `Uvicorn running on http://127.0.0.1:8000`.

- [ ] **Step 2: Confirm the API responds (a 502 here is correct, not a failure)** **[repo]**

In a second terminal: `curl http://localhost:8000/api/prompts`
Expected: `[]` (empty list — no prompts saved yet, real SQLite path, no MCP call involved).

`curl http://localhost:8000/api/stats`
Expected: a `502` with a JSON error body — there's no real `openbrain-mcp` at
`http://openbrain-mcp:8080/mcp` reachable from your laptop, so the connection fails and `_call`'s
except-clause converts it to a 502. This confirms the error-handling path from design spec section
7 works, not that anything is broken.

- [ ] **Step 3: Stop the server** **[repo]**

Ctrl+C in the terminal running uvicorn.

---

### Task 11: Frontend scaffold

**Files:**
- Create: `openbrain-gui/frontend/package.json`
- Create: `openbrain-gui/frontend/vite.config.js`
- Create: `openbrain-gui/frontend/index.html`
- Create: `openbrain-gui/frontend/src/main.jsx`
- Create: `openbrain-gui/frontend/src/api.js`
- Create: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Create package.json** **[repo]**

Create `openbrain-gui/frontend/package.json`:

```json
{
  "name": "openbrain-gui-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create vite.config.js** **[repo]**

Create `openbrain-gui/frontend/vite.config.js`:

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-server proxy so `fetch('/api/...')` works identically in dev (Vite
// on :5173, backend on :8000) and in production (same origin, one
// container) -- no environment-specific API base URL needed anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
```

- [ ] **Step 3: Create index.html** **[repo]**

Create `openbrain-gui/frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>OpenBrain</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Create src/main.jsx** **[repo]**

Create `openbrain-gui/frontend/src/main.jsx`:

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 5: Create src/api.js** **[repo]**

Create `openbrain-gui/frontend/src/api.js`:

```javascript
const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`)
  }
  if (resp.status === 204) return null
  return resp.json()
}

export const api = {
  getStats: () => request('/stats'),
  getKeywords: (filter) => request(`/keywords?filter=${encodeURIComponent(filter || '')}`),
  search: (query, k) => request('/search', { method: 'POST', body: JSON.stringify({ query, k }) }),
  deleteCapture: (id, snapshot) =>
    request(`/captures/${id}/delete`, { method: 'POST', body: JSON.stringify(snapshot) }),
  updateCapture: (id, changes) =>
    request(`/captures/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  getPrompts: () => request('/prompts'),
  savePrompt: (text) => request('/prompts', { method: 'POST', body: JSON.stringify({ text }) }),
  deletePrompt: (id) => request(`/prompts/${id}`, { method: 'DELETE' }),
  getDeleteLog: () => request('/delete-log'),
}
```

- [ ] **Step 6: Create src/index.css** **[repo]**

Create `openbrain-gui/frontend/src/index.css`:

```css
:root {
  --bg: #1e1e22;
  --fg: #f0f0f0;
  --border: #444;
  --accent: #6c8cff;
}
:root[data-theme="light"] {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --border: #ccc;
  --accent: #3a5ce8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, sans-serif;
}
.app { max-width: 1100px; margin: 0 auto; padding: 16px; }
.app-header { display: flex; justify-content: space-between; align-items: center; }
.theme-toggle {
  background: none; border: 1px solid var(--border); border-radius: 4px;
  padding: 4px 8px; cursor: pointer;
}
.stats-line { opacity: 0.8; font-size: 14px; }
.top-row { display: flex; gap: 16px; margin: 12px 0; }
.prompt-bar { flex: 2; display: flex; flex-direction: column; gap: 8px; }
.prompt-dropdown, .prompt-textarea, .keyword-filter {
  width: 100%; background: var(--bg); color: var(--fg);
  border: 1px solid var(--border); border-radius: 4px; padding: 6px;
}
.prompt-actions { display: flex; gap: 8px; }
.keyword-panel { flex: 1; display: flex; flex-direction: column; gap: 6px; }
.keyword-list {
  max-height: 140px; overflow: auto; display: flex; flex-wrap: wrap;
  gap: 4px; align-content: flex-start;
}
.keyword-chip {
  border: 1px solid var(--border); background: none; color: var(--fg);
  border-radius: 12px; padding: 2px 8px; cursor: pointer; font-size: 12px;
}
.grid-actions { display: flex; gap: 8px; margin-bottom: 8px; }
.result-grid { display: flex; flex-direction: column; gap: 6px; }
.result-row {
  display: flex; gap: 8px; align-items: flex-start; border: 1px solid var(--border);
  border-radius: 4px; padding: 8px; cursor: pointer;
}
.result-row--readonly { cursor: default; }
.result-id { opacity: 0.6; font-size: 12px; }
.result-meta { font-size: 12px; opacity: 0.8; }
.grid-empty { opacity: 0.6; }
.popup-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
}
.popup {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; width: 400px; display: flex; flex-direction: column; gap: 8px;
}
.popup textarea, .popup input {
  width: 100%; background: var(--bg); color: var(--fg);
  border: 1px solid var(--border); border-radius: 4px; padding: 6px;
}
.popup-actions { display: flex; justify-content: flex-end; gap: 8px; }
button { cursor: pointer; }
```

- [ ] **Step 7: Install dependencies** **[repo]**

```bash
cd openbrain-gui/frontend && npm install
```

- [ ] **Step 8: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/package.json openbrain-gui/frontend/package-lock.json \
        openbrain-gui/frontend/vite.config.js openbrain-gui/frontend/index.html \
        openbrain-gui/frontend/src/main.jsx openbrain-gui/frontend/src/api.js \
        openbrain-gui/frontend/src/index.css
git commit -m "feat(gui-frontend): scaffold Vite + React project"
```

---

### Task 12: Frontend components

**Files:**
- Create: `openbrain-gui/frontend/src/ThemeToggle.jsx`
- Create: `openbrain-gui/frontend/src/PromptBar.jsx`
- Create: `openbrain-gui/frontend/src/KeywordPanel.jsx`
- Create: `openbrain-gui/frontend/src/ResultGrid.jsx`
- Create: `openbrain-gui/frontend/src/DeleteLogView.jsx`
- Create: `openbrain-gui/frontend/src/ChangePopup.jsx`

No automated tests for the frontend in Phase 1 (design spec section 8) — verified manually in
Task 13.

- [ ] **Step 1: Create ThemeToggle.jsx** **[repo]**

Create `openbrain-gui/frontend/src/ThemeToggle.jsx`:

```jsx
import { useEffect, useState } from 'react'

export default function ThemeToggle() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('theme', theme)
  }, [theme])

  return (
    <button className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
      {theme === 'dark' ? '🌙' : '☀️'}
    </button>
  )
}
```

- [ ] **Step 2: Create PromptBar.jsx** **[repo]**

Create `openbrain-gui/frontend/src/PromptBar.jsx`:

```jsx
export default function PromptBar({
  prompt, onPromptChange, promptTextareaRef,
  savedPrompts, onSelectSavedPrompt,
  onSearch, onSavePrompt, onDeleteSavedPrompt, selectedPromptId,
}) {
  return (
    <div className="prompt-bar">
      <select
        className="prompt-dropdown"
        value={selectedPromptId || ''}
        onChange={(e) => onSelectSavedPrompt(e.target.value)}
      >
        <option value="">Saved prompts…</option>
        {savedPrompts.map((p) => (
          <option key={p.id} value={p.id}>
            {p.text.slice(0, 40)}
          </option>
        ))}
      </select>
      <textarea
        ref={promptTextareaRef}
        className="prompt-textarea"
        rows={3}
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="Search prompt…"
      />
      <div className="prompt-actions">
        <button onClick={onSearch}>Search</button>
        <button onClick={onSavePrompt}>Save prompt</button>
        <button onClick={onDeleteSavedPrompt} disabled={!selectedPromptId}>
          Delete prompt
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create KeywordPanel.jsx** **[repo]**

Create `openbrain-gui/frontend/src/KeywordPanel.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function KeywordPanel({ onKeywordClick }) {
  const [filter, setFilter] = useState('')
  const [keywords, setKeywords] = useState([])

  useEffect(() => {
    const timer = setTimeout(() => {
      api.getKeywords(filter).then(setKeywords).catch(() => setKeywords([]))
    }, 200)
    return () => clearTimeout(timer)
  }, [filter])

  return (
    <div className="keyword-panel">
      <input
        className="keyword-filter"
        placeholder="Filter keywords…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="keyword-list">
        {keywords.map((k) => (
          <button key={k.keyword} className="keyword-chip" onClick={() => onKeywordClick(k.keyword)}>
            {k.keyword} ({k.count})
          </button>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create ResultGrid.jsx** **[repo]**

Create `openbrain-gui/frontend/src/ResultGrid.jsx`:

```jsx
export default function ResultGrid({ rows, selectedId, onSelect }) {
  if (rows.length === 0) {
    return <p className="grid-empty">No results yet — run a search.</p>
  }
  return (
    <div className="result-grid">
      {rows.map((row) => (
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
            <div className="result-meta">{row.source_url}</div>
            <div className="result-meta">
              {row.created_at} · keywords: {row.keywords.join(', ')}
            </div>
          </div>
        </label>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Create DeleteLogView.jsx** **[repo]**

Create `openbrain-gui/frontend/src/DeleteLogView.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function DeleteLogView() {
  const [entries, setEntries] = useState([])

  useEffect(() => {
    api.getDeleteLog().then(setEntries).catch(() => setEntries([]))
  }, [])

  if (entries.length === 0) {
    return <p className="grid-empty">No deletions logged yet.</p>
  }
  return (
    <div className="result-grid">
      {entries.map((entry) => (
        <div key={entry.id} className="result-row result-row--readonly">
          <span className="result-id">{entry.capture_id.slice(0, 8)}</span>
          <div className="result-body">
            <div className="result-subject">{entry.subject_line}</div>
            <div className="result-meta">{entry.source_url}</div>
            <div className="result-meta">
              keywords: {entry.keywords.join(', ')} · deleted {entry.deleted_at}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 6: Create ChangePopup.jsx** **[repo]**

Create `openbrain-gui/frontend/src/ChangePopup.jsx`:

```jsx
import { useState } from 'react'

export default function ChangePopup({ row, onSave, onClose }) {
  const [summary, setSummary] = useState(row.summary)
  const [keywords, setKeywords] = useState(row.keywords.join(', '))

  const handleSave = () => {
    const trimmedSummary = summary.trim()
    const keywordList = keywords.split(',').map((k) => k.trim()).filter(Boolean)
    if (!trimmedSummary || keywordList.length === 0) return
    onSave({ summary: trimmedSummary, keywords: keywordList })
  }

  return (
    <div className="popup-overlay">
      <div className="popup">
        <h3>Change entry</h3>
        <label>
          Summary
          <textarea rows={4} value={summary} onChange={(e) => setSummary(e.target.value)} />
        </label>
        <label>
          Keywords (comma-separated)
          <input value={keywords} onChange={(e) => setKeywords(e.target.value)} />
        </label>
        <div className="popup-actions">
          <button onClick={handleSave}>Save</button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 7: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/ThemeToggle.jsx openbrain-gui/frontend/src/PromptBar.jsx \
        openbrain-gui/frontend/src/KeywordPanel.jsx openbrain-gui/frontend/src/ResultGrid.jsx \
        openbrain-gui/frontend/src/DeleteLogView.jsx openbrain-gui/frontend/src/ChangePopup.jsx
git commit -m "feat(gui-frontend): add UI components"
```

---

### Task 13: Wire App.jsx and manually verify in a browser

**Files:**
- Create: `openbrain-gui/frontend/src/App.jsx`

- [ ] **Step 1: Create App.jsx** **[repo]**

Create `openbrain-gui/frontend/src/App.jsx`:

```jsx
import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import ThemeToggle from './ThemeToggle.jsx'
import PromptBar from './PromptBar.jsx'
import KeywordPanel from './KeywordPanel.jsx'
import ResultGrid from './ResultGrid.jsx'
import DeleteLogView from './DeleteLogView.jsx'
import ChangePopup from './ChangePopup.jsx'

export default function App() {
  const [stats, setStats] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [savedPrompts, setSavedPrompts] = useState([])
  const [selectedPromptId, setSelectedPromptId] = useState('')
  const [rows, setRows] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [showDeleteLog, setShowDeleteLog] = useState(false)
  const [editingRow, setEditingRow] = useState(null)
  const promptTextareaRef = useRef(null)

  useEffect(() => {
    api.getStats().then(setStats).catch(() => setStats(null))
    api.getPrompts().then(setSavedPrompts).catch(() => setSavedPrompts([]))
  }, [])

  const handleKeywordClick = (keyword) => {
    const el = promptTextareaRef.current
    const start = el?.selectionStart ?? prompt.length
    const end = el?.selectionEnd ?? prompt.length
    setPrompt(prompt.slice(0, start) + keyword + prompt.slice(end))
  }

  const handleSearch = async () => {
    if (!prompt.trim()) return
    const results = await api.search(prompt)
    setRows(results)
    setSelectedId(null)
    setShowDeleteLog(false)
  }

  const handleSavePrompt = async () => {
    if (!prompt.trim()) return
    const created = await api.savePrompt(prompt)
    setSavedPrompts((prev) => [created, ...prev])
  }

  const handleSelectSavedPrompt = (id) => {
    setSelectedPromptId(id)
    const found = savedPrompts.find((p) => String(p.id) === String(id))
    if (found) setPrompt(found.text)
  }

  const handleDeleteSavedPrompt = async () => {
    if (!selectedPromptId) return
    await api.deletePrompt(selectedPromptId)
    setSavedPrompts((prev) => prev.filter((p) => String(p.id) !== String(selectedPromptId)))
    setSelectedPromptId('')
  }

  const handleDelete = async () => {
    const row = rows.find((r) => r.id === selectedId)
    if (!row || !window.confirm('Delete this capture?')) return
    await api.deleteCapture(row.id, {
      subject_line: row.subject_line,
      keywords: row.keywords,
      source_url: row.source_url,
      created_at: row.created_at,
    })
    setRows((prev) => prev.filter((r) => r.id !== row.id))
    setSelectedId(null)
  }

  const handleChangeSave = async (changes) => {
    await api.updateCapture(editingRow.id, changes)
    setRows((prev) => prev.map((r) => (r.id === editingRow.id ? { ...r, ...changes } : r)))
    setEditingRow(null)
  }

  const selectedRow = rows.find((r) => r.id === selectedId)

  return (
    <div className="app">
      <header className="app-header">
        <h1>OpenBrain</h1>
        <ThemeToggle />
      </header>

      {stats && (
        <p className="stats-line">
          {stats.total} captures ·{' '}
          {Object.entries(stats.by_source).map(([src, n]) => `${src}: ${n}`).join(', ')}
          {stats.first_capture && ` · first: ${stats.first_capture}`}
          {stats.last_capture && ` · last: ${stats.last_capture}`}
        </p>
      )}

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
        />
        <KeywordPanel onKeywordClick={handleKeywordClick} />
      </div>

      <div className="grid-actions">
        <button onClick={() => setShowDeleteLog((v) => !v)}>
          {showDeleteLog ? 'Back to results' : 'Show delete log'}
        </button>
        <button disabled={!selectedRow} onClick={() => setEditingRow(selectedRow)}>
          Change
        </button>
        <button disabled={!selectedRow} onClick={handleDelete}>
          Delete
        </button>
      </div>

      {showDeleteLog ? (
        <DeleteLogView />
      ) : (
        <ResultGrid rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
      )}

      {editingRow && (
        <ChangePopup row={editingRow} onSave={handleChangeSave} onClose={() => setEditingRow(null)} />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Run backend + frontend dev servers together** **[repo, needs a local openbrain-mcp]**

Terminal 1 — a real local `openbrain-mcp` (see `README.md`'s "Running it locally"), noting its
bearer token. The committed compose file doesn't publish `openbrain-mcp`'s port 8080 to the host
(same reason Postgres' port isn't published either, per the existing note under "Running it
locally") — add a local, gitignored override first:

```bash
cat > deploy/docker-compose.override.yml <<'EOF'
services:
  openbrain-mcp:
    ports:
      - "8080:8080"
EOF
cd deploy && docker compose -f docker-compose.openbrain.yml up -d --build
```

Terminal 2 — the GUI backend, pointed at it:

```bash
cd openbrain-gui/backend
OPENBRAIN_MCP_URL=http://localhost:8080/mcp OPENBRAIN_TOKEN=<your local token> \
  ANTHROPIC_API_KEY=<a real key, for subject lines> GUI_DB_PATH=/tmp/gui-dev.db \
  python -m uvicorn app.main:app --port 8000
```

Terminal 3 — the frontend dev server:

```bash
cd openbrain-gui/frontend && npm run dev
```

- [ ] **Step 3: Manually exercise every Phase 1 interaction in a browser** **[repo]**

Open the URL Vite prints (typically `http://localhost:5173`) and verify, using the local
`openbrain-mcp` (save a few test captures via Claude Desktop/Code first if the corpus is empty):

1. Stats line shows on load; Result grid is empty.
2. Toggling the theme button switches light/dark and survives a page refresh (`localStorage`).
3. Typing in the Prompt textarea and clicking **Search** populates the Result grid with subject
   lines.
4. Typing in the keyword filter box narrows the keyword list; clicking a keyword inserts it at the
   cursor position in the Prompt textarea.
5. Selecting a row's radio button enables **Change** and **Delete**.
6. **Change** opens a popup pre-filled with summary/keywords; saving updates the row in place.
7. **Delete** asks for confirmation, then removes the row from the grid.
8. **Save prompt** adds the current text to the dropdown; selecting it repopulates the textarea;
   **Delete prompt** removes it.
9. **Show delete log** swaps the grid for the delete log, showing the row deleted in step 7 with a
   deletion timestamp; clicking it again returns to the (now search-result) grid.

- [ ] **Step 4: Tear down** **[repo]**

Stop all three terminals (Ctrl+C). If Task 14 will follow immediately, leave the local
`openbrain-mcp` stack and `deploy/docker-compose.override.yml` in place (Task 14 reuses both);
otherwise `cd deploy && docker compose -f docker-compose.openbrain.yml down && rm
docker-compose.override.yml`.

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/App.jsx
git commit -m "feat(gui-frontend): wire components together in App"
```

---

### Task 14: Multi-stage Dockerfile and local Compose smoke test

**Files:**
- Create: `openbrain-gui/Dockerfile`
- Modify: `.gitignore` (ignore `openbrain-gui/backend/gui.db`, `node_modules/`, frontend `dist/`
  are already covered by existing patterns — verify, don't duplicate)

- [ ] **Step 1: Create the multi-stage Dockerfile** **[repo]**

Create `openbrain-gui/Dockerfile`:

```dockerfile
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/pyproject.toml ./
# Same stub-package trick as openbrain-mcp/Dockerfile: pyproject.toml
# declares packages = ["app"], so `pip install .` needs app/ to exist for
# the metadata step even though the real source is copied in afterward --
# this keeps the dependency layer cacheable independently of app changes.
RUN mkdir app && touch app/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf app

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify .gitignore already covers generated artifacts** **[repo]**

Check the root `.gitignore` (already has `node_modules/` and `.cache/`) — confirm it does NOT yet
ignore SQLite `.db` files or Vite's `dist/` output. Add these lines under the existing "Local data /
model caches" section:

```
dist/
*.db
```

- [ ] **Step 3: Build and run the image standalone** **[repo]**

`openbrain-gui` isn't added to `deploy/docker-compose.openbrain.yml` until Task 15, so this step
builds and runs the image directly with plain `docker build`/`docker run` rather than Compose:

```bash
cd openbrain-gui && docker build -t openbrain-gui:local .
docker run --rm -p 8000:8000 \
  -e OPENBRAIN_MCP_URL=http://host.docker.internal:8080/mcp \
  -e OPENBRAIN_TOKEN=<your local openbrain-mcp token> \
  -e ANTHROPIC_API_KEY=<a real key> \
  -e GUI_DB_PATH=/tmp/gui.db \
  openbrain-gui:local
```

(Requires the local `openbrain-mcp` from Task 13's Step 2 already running with its port published.)

- [ ] **Step 4: Verify the built image serves both frontend and API** **[repo]**

```bash
curl http://localhost:8000/          # HTML (the built React app's index.html)
curl http://localhost:8000/api/prompts   # [] (JSON, real SQLite inside the container)
```

Then open `http://localhost:8000` in a browser and repeat Task 13 Step 3's manual checklist against
this containerized build (confirms the production build behaves the same as the dev-server setup).

- [ ] **Step 5: Tear down** **[repo]**

```bash
docker stop <container-id-or-name-shown-when-it-was-run>
cd deploy && docker compose -f docker-compose.openbrain.yml down
rm docker-compose.override.yml
```

(The local `openbrain-mcp` stack and its port-mapping override, both from Task 13 Step 2, are no
longer needed — Task 15 deploys to the VPS, a separate environment.)

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-gui/Dockerfile .gitignore
git commit -m "feat(gui): add multi-stage Dockerfile for openbrain-gui"
```

---

### Task 15: Production deployment (Compose service, Traefik, VPS)

**Files:**
- Modify: `deploy/docker-compose.openbrain.yml` (add `openbrain-gui` service + volume)
- Modify: `deploy/.env.example` (add new required vars)

- [ ] **Step 1: Add the service to docker-compose.openbrain.yml** **[repo]**

In `deploy/docker-compose.openbrain.yml`, add after the `openbrain-mcp` service block (before the
closing `volumes:` section):

```yaml
  openbrain-gui:
    build: ../openbrain-gui
    restart: unless-stopped
    environment:
      OPENBRAIN_MCP_URL: http://openbrain-mcp:8080/mcp
      OPENBRAIN_TOKEN: ${OPENBRAIN_TOKEN}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      GUI_DB_PATH: /data/gui.db
    volumes:
      - openbrain_gui_data:/data
    depends_on:
      - openbrain-mcp
    networks:
      - hermes_net   # reaches openbrain-mcp:8080 by container name; NOT on openbrain_internal
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.openbrain-gui.rule=Host(`${OPENBRAIN_GUI_HOST}`)"
      - "traefik.http.routers.openbrain-gui.entrypoints=websecure"
      - "traefik.http.routers.openbrain-gui.tls.certresolver=letsencrypt"
      - "traefik.http.routers.openbrain-gui.middlewares=openbrain-gui-auth"
      - "traefik.http.middlewares.openbrain-gui-auth.basicauth.users=${GUI_BASIC_AUTH_USERS}"
      - "traefik.http.services.openbrain-gui.loadbalancer.server.port=8000"
```

Change the `volumes:` block at the bottom from:

```yaml
volumes:
  openbrain_pgdata:
```

to:

```yaml
volumes:
  openbrain_pgdata:
  openbrain_gui_data:
```

- [ ] **Step 2: Document the new env vars in .env.example** **[repo]**

Append to `deploy/.env.example`:

```
# openbrain-gui
OPENBRAIN_GUI_HOST=gui.yourhost.example.com
ANTHROPIC_API_KEY=sk-ant-...
# Generate with: htpasswd -nB <username>   (then escape every literal $ as $$
# for docker-compose's env-var interpolation, e.g. $$apr1$$... not $apr1$...)
GUI_BASIC_AUTH_USERS=someuser:$$apr1$$examplehash
```

- [ ] **Step 3: Commit** **[repo]**

```bash
git add deploy/docker-compose.openbrain.yml deploy/.env.example
git commit -m "feat(deploy): add openbrain-gui service with Traefik basic-auth"
```

- [ ] **Step 4: Deploy to the VPS** **[repo, needs SSH access to srv1608402.hstgr.cloud]**

```bash
ssh root@srv1608402.hstgr.cloud
cd /root/HermesPlusOpenbrain && git pull --ff-only origin main
```

Add the new variables to the real (not `.env.example`) `deploy/.env` on the VPS: `OPENBRAIN_GUI_HOST`
(a real subdomain, e.g. `gui.srv1608402.hstgr.cloud`), `ANTHROPIC_API_KEY` (the same key Hermes
already uses — check `~/.hermes/.env` on the VPS), and `GUI_BASIC_AUTH_USERS` (generate a real
htpasswd hash with `htpasswd -nB <username>`, escaping `$` as `$$`).

```bash
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.openbrain.yml up -d --build openbrain-gui
docker compose -f docker-compose.openbrain.yml ps   # openbrain-gui should show as running
```

- [ ] **Step 5: Verify the live deployment** **[repo]**

```bash
curl -u <username>:<password> https://<OPENBRAIN_GUI_HOST>/api/prompts
```

Expected: `[]` over real HTTPS with a valid cert. Without credentials (`curl
https://<OPENBRAIN_GUI_HOST>/api/prompts`), expected: `401` (Traefik's basic-auth middleware
rejecting the request before it reaches the container). Then open `https://<OPENBRAIN_GUI_HOST>` in
a browser, sign in with the basic-auth credentials, and repeat Task 13 Step 3's manual checklist
against the real production `openbrain-mcp`/`openbrain-db`.

---

### Task 16: Update project documentation

**Files:**
- Modify: `OpenbrainAddition.md` (§8 "Erweiterungen — Status", German — match the document's
  existing language)

- [ ] **Step 1: Add a Phase 1 GUI status entry** **[repo]**

`OpenbrainAddition.md`'s §8 currently ends (after item 4, `classify_captures`) with this paragraph:

```markdown
Damit sind alle 4 ursprünglich geplanten Fähigkeiten umgesetzt. Details zu
jeder einzelnen siehe die jeweiligen Spec-/Plan-Dokumente unter
`docs/superpowers/`.
```

Insert a new item 5 immediately before that paragraph:

```markdown
5. ✅ **Web GUI Phase 1** (`openbrain-gui`) — fertig, gemergt auf `main` am
   <YYYY-MM-DD, das tatsächliche Merge-Datum einsetzen>. Spec:
   [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md).
   23/23 neue Backend-Tests grün (`openbrain-gui/backend/tests/`), plus das
   neue `list_keywords`-Tool in `openbrain-mcp` (36/36 Tests dort grün
   insgesamt). Kein separates GUI-only-Repo: React-Frontend (Vite) +
   FastAPI-Backend in einem Container, per Multi-Stage-Dockerfile gebaut.
   Zugriff auf `openbrain-db` ausschließlich über `openbrain-mcp` (wie
   Claude Desktop/Code) — kein direkter Postgres-Zugriff. Eigene kleine
   SQLite-Datenbank (`gui.db`) für gespeicherte Prompts und das Lösch-Log,
   getrennt von `openbrain-db`. Subject-Lines pro Ergebniszeile werden live
   per Claude Haiku aus der Summary generiert (Fallback: Kürzung der ersten
   10 Wörter bei API-Fehlern). Einzelbenutzer-Zugriff über Traefik
   Basic-Auth, kein Login-Screen. Phase 2 (Wordcloud, AND/OR-Keyword-Suche)
   und Phase 3 (Clustering/Klassifikation in der GUI) sind bewusst nicht
   Teil dieser Phase — siehe `planGUI.md`.
```

Then update the paragraph right after it from "Damit sind alle 4 ursprünglich geplanten Fähigkeiten
umgesetzt." to "Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten sowie Phase 1 der Web-GUI
umgesetzt."

- [ ] **Step 2: Commit** **[repo]**

```bash
git add OpenbrainAddition.md
git commit -m "docs: document OpenBrain GUI Phase 1 in OpenbrainAddition.md"
```
