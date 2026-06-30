# Hermes + OpenBrain Secondary Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a self-hosted, OpenBrain-style semantic memory (Postgres + pgvector + a small MCP server) on the existing Hostinger VPS, so content sent to Hermes-Agent via WhatsApp is summarized, keyworded, and stored — then searchable by meaning from WhatsApp and from the laptop (Claude Desktop / Claude Code).

**Architecture:** Two new Docker containers (`openbrain-db` = Postgres 16 + pgvector; `openbrain-mcp` = Python MCP service with in-process multilingual embeddings) join the existing `hermes-agent` + `traefik` stack. Hermes does the LLM summarization/keywording and calls the MCP `save` tool over the internal Docker network. Traefik exposes the MCP server over HTTPS (bearer-token auth) for laptop clients.

**Tech Stack:** Docker Compose, Postgres 16, `pgvector`, Python 3.11, official MCP SDK (`mcp.server.fastmcp.FastMCP`) over Streamable HTTP, Starlette/uvicorn, `sentence-transformers` (`intfloat/multilingual-e5-small`), `psycopg` 3 + `pgvector`, Traefik.

**Where things run:**
- The `openbrain-mcp` **code and tests** are developed in this repo (laptop, Claude Code) and pushed to GitHub.
- **Deployment** (Docker Compose, Traefik, Hermes config) happens **on the VPS** via the Hostinger terminal / SSH. Steps that must run on the VPS are marked **[VPS]**. Steps in the repo are marked **[repo]**.

**Reference:** Design spec at `docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md`.

---

## File structure (created by this plan)

```
openbrain-mcp/
  app/
    __init__.py
    config.py          # env-var loading (DATABASE_URL, OPENBRAIN_TOKEN, model name)
    embeddings.py      # load model once; embed_passage / embed_query (e5 prefixes)
    keywords.py        # normalize_keywords ("around 5", dedupe, trim)
    db.py              # get_conn() context manager + pgvector registration
    store.py           # save_capture / search_captures / fetch_recent / compute_stats
    server.py          # FastMCP tools + bearer-auth Starlette app + /health
  migrations/
    001_init.sql       # vector extension, captures table, indexes
  tests/
    test_keywords.py   # pure unit tests (no DB)
    test_store.py      # integration round-trip (needs a Postgres)
  Dockerfile
  pyproject.toml
  README.md
deploy/
  docker-compose.openbrain.yml   # openbrain-db + openbrain-mcp services + Traefik labels
  .env.example                   # documents required env vars (no secrets)
```

The existing `hermes-agent` and `traefik` projects are configured in place on the VPS (no files for them in this repo).

---

## Phase 0 — Verify prerequisites on the VPS (resolve spec §8 open questions)

These tasks gather facts that change later steps. **Do them first.** No code is written here; record answers in `deploy/.env.example` comments or a scratch note.

### Task 0.1: Confirm Hermes can fetch link content (spec §8.1)

**[VPS]** This decides whether capture is hands-off (send a link) or requires pasting text.

- [ ] **Step 1: List Hermes' enabled tools**

On the VPS terminal (or via the Hermes CLI inside its container):
```bash
docker exec -it $(docker ps --filter name=hermes --format '{{.Names}}' | head -1) hermes tools
```
Expected: a list of 40+ tools. Look for a web-fetch / URL-reader / browser / YouTube-transcript tool.

- [ ] **Step 2: Live test via WhatsApp**

Send Hermes a WhatsApp message: *"Fetch this and give me a 3-sentence summary: <a YouTube link> and <a Substack link>"*.
Expected: Hermes returns a real summary (proves it can retrieve the content).

- [ ] **Step 3: Record the outcome**

- If it can fetch → capture flow uses links directly (Task 5.2 instruction stays as written).
- If it **cannot** fetch a YouTube transcript → choose a fallback and note it: (a) enable/add a fetch or transcript MCP/tool in Hermes, or (b) the user pastes text into WhatsApp. Adjust Task 5.2 wording accordingly.

### Task 0.2: Confirm DNS / subdomain for Traefik TLS (spec §8.3)

**[VPS]** Traefik needs a hostname it can get a Let's Encrypt cert for.

- [ ] **Step 1: Determine an available hostname**

Check what hostname the existing Hermes service uses in Traefik (gives the working domain pattern):
```bash
docker inspect $(docker ps --filter name=traefik --format '{{.Names}}' | head -1) >/dev/null 2>&1 && \
docker ps --format '{{.Names}}' && \
grep -ri "Host(" /path/to/traefik/dynamic 2>/dev/null || echo "inspect compose labels of hermes instead"
```
- [ ] **Step 2: Pick `brain.<domain>`**

Decide the OpenBrain hostname (e.g. `brain.<your-domain>` or a subdomain of the working Hermes domain). Confirm a DNS A record points it at the VPS public IP (create one if needed in your DNS provider / Hostinger DNS). Record the chosen hostname as `OPENBRAIN_HOST` for Task 4.

### Task 0.3: Confirm how the existing stack is networked & managed

**[VPS]** Later tasks attach `openbrain-*` to the same network as Hermes and reuse Traefik.

- [ ] **Step 1: Find the Docker network and Traefik entrypoints**

```bash
docker network ls
docker inspect $(docker ps --filter name=traefik --format '{{.Names}}' | head -1) \
  --format '{{json .NetworkSettings.Networks}}'
docker inspect $(docker ps --filter name=hermes --format '{{.Names}}' | head -1) \
  --format '{{json .NetworkSettings.Networks}}'
```
Expected: identify the shared network name (e.g. `web` or `traefik`) and the Traefik HTTPS entrypoint name (commonly `websecure`) and certresolver name. Record these — they fill the compose labels in Task 4.

- [ ] **Step 2: Confirm compose workflow**

Note whether you deploy via the Hostinger Docker Manager "Compose" UI or `docker compose` on the terminal. Either works; Task 3/4 assume `docker compose -f deploy/docker-compose.openbrain.yml up -d`.

---

## Phase 1 — Database schema

### Task 1.1: Write the schema migration

**Files:**
- Create: `openbrain-mcp/migrations/001_init.sql`

- [ ] **Step 1: Write the migration** **[repo]**

```sql
-- 001_init.sql — OpenBrain captures schema
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS captures (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_text    text        NOT NULL,
    summary     text        NOT NULL,
    keywords    text[]      NOT NULL DEFAULT '{}',
    source      text,
    source_url  text,
    lang        text,
    metadata    jsonb       NOT NULL DEFAULT '{}',
    embedding   vector(384) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Cosine-distance ANN index for semantic search
CREATE INDEX IF NOT EXISTS captures_embedding_idx
    ON captures USING hnsw (embedding vector_cosine_ops);

-- Fast "recent" listing
CREATE INDEX IF NOT EXISTS captures_created_at_idx
    ON captures (created_at DESC);
```
Note: Postgres 16 provides `gen_random_uuid()` in core; the `vector` type comes from the `pgvector` image used in Task 3.

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-mcp/migrations/001_init.sql
git commit -m "feat(db): add captures schema with pgvector cosine index"
```

---

## Phase 2 — The `openbrain-mcp` service (TDD)

Develop in the repo on the laptop. If the laptop has no Python, run these on the VPS instead; the code is identical.

### Task 2.0: Project scaffolding

**Files:**
- Create: `openbrain-mcp/pyproject.toml`, `openbrain-mcp/app/__init__.py`, `openbrain-mcp/app/config.py`

- [ ] **Step 1: Write `pyproject.toml`** **[repo]**

```toml
[project]
name = "openbrain-mcp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.2.0",
    "sentence-transformers>=3.0.0",
    "psycopg[binary]>=3.2.0",
    "pgvector>=0.3.0",
    "uvicorn>=0.30.0",
    "starlette>=0.37.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `app/config.py`** **[repo]**

```python
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
MODEL_NAME = os.environ.get("OPENBRAIN_MODEL", "intfloat/multilingual-e5-small")
EMBED_DIM = 384
```

- [ ] **Step 3: Create empty `app/__init__.py`** **[repo]**

```python
```

- [ ] **Step 4: Install deps** **[repo]**

```bash
cd openbrain-mcp && python -m pip install -e ".[dev]"
```
Expected: installs without error (first run downloads torch; may take a few minutes).

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-mcp/pyproject.toml openbrain-mcp/app/__init__.py openbrain-mcp/app/config.py
git commit -m "chore: scaffold openbrain-mcp project"
```

### Task 2.1: Keyword normalization ("around 5")

**Files:**
- Create: `openbrain-mcp/app/keywords.py`
- Test: `openbrain-mcp/tests/test_keywords.py`

- [ ] **Step 1: Write the failing test** **[repo]**

```python
# tests/test_keywords.py
from app.keywords import normalize_keywords

def test_trims_and_drops_blanks():
    assert normalize_keywords(["  ai ", "", "  ", "memory"]) == ["ai", "memory"]

def test_dedupes_case_insensitively_preserving_first():
    assert normalize_keywords(["AI", "ai", "Memory"]) == ["AI", "Memory"]

def test_caps_at_max_but_does_not_force_exactly_five():
    out = normalize_keywords(["a", "b", "c"])  # fewer than 5 is fine ("around 5")
    assert out == ["a", "b", "c"]
    many = normalize_keywords([f"k{i}" for i in range(20)])
    assert len(many) == 8  # capped, not padded
```

- [ ] **Step 2: Run test to verify it fails** **[repo]**

Run: `cd openbrain-mcp && python -m pytest tests/test_keywords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.keywords'`.

- [ ] **Step 3: Write minimal implementation** **[repo]**

```python
# app/keywords.py
def normalize_keywords(keywords: list[str], max_len: int = 8) -> list[str]:
    """Trim, drop blanks, dedupe case-insensitively, cap at max_len.

    Intentionally does NOT pad to a fixed count — the spec wants "around 5".
    """
    seen: set[str] = set()
    out: list[str] = []
    for k in keywords or []:
        k = (k or "").strip()
        if not k:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(k)
    return out[:max_len]
```

- [ ] **Step 4: Run test to verify it passes** **[repo]**

Run: `cd openbrain-mcp && python -m pytest tests/test_keywords.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-mcp/app/keywords.py openbrain-mcp/tests/test_keywords.py
git commit -m "feat(keywords): normalize to ~5 deduped keywords"
```

### Task 2.2: Embeddings (e5 prefixes)

**Files:**
- Create: `openbrain-mcp/app/embeddings.py`

The model is heavy and network-dependent, so this task is verified by a smoke check rather than a committed unit test (we don't want the model download in every test run).

- [ ] **Step 1: Write the implementation** **[repo]**

```python
# app/embeddings.py
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from app.config import MODEL_NAME

@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)

def embed_passage(text: str) -> list[float]:
    # e5 family requires the "passage:" prefix for stored documents
    vec = get_model().encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()

def embed_query(text: str) -> list[float]:
    # ...and "query:" for search queries
    vec = get_model().encode(f"query: {text}", normalize_embeddings=True)
    return vec.tolist()
```

- [ ] **Step 2: Smoke-test the model + dimensions** **[repo]**

Run:
```bash
cd openbrain-mcp && python -c "from app.embeddings import embed_passage; v=embed_passage('Servus, das ist ein Test über KI-Memory'); print(len(v)); print(round(sum(x*x for x in v), 3))"
```
Expected: prints `384` and a value near `1.0` (normalized). First run downloads the model (~450 MB).

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/embeddings.py
git commit -m "feat(embeddings): multilingual-e5-small with passage/query prefixes"
```

### Task 2.3: Database connection helper

**Files:**
- Create: `openbrain-mcp/app/db.py`

- [ ] **Step 1: Write the implementation** **[repo]**

```python
# app/db.py
from contextlib import contextmanager
from collections.abc import Iterator
import psycopg
from pgvector.psycopg import register_vector
from app.config import DATABASE_URL

@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(DATABASE_URL)
    try:
        register_vector(conn)          # lets us pass/return Python lists as vectors
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-mcp/app/db.py
git commit -m "feat(db): connection helper with pgvector registration"
```

### Task 2.4: Store layer — save / search / recent / stats

**Files:**
- Create: `openbrain-mcp/app/store.py`
- Test: `openbrain-mcp/tests/test_store.py`

- [ ] **Step 1: Write the failing integration test** **[repo]**

This test needs a real Postgres+pgvector with the schema applied. It reads `DATABASE_URL` from the environment (point it at a throwaway DB — e.g. the `openbrain-db` container on a test database). It is skipped automatically when `DATABASE_URL` is unset.

```python
# tests/test_store.py
import os
import uuid
import pytest
from app.db import get_conn
from app import store

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"
)

def _clean():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM captures")
        conn.commit()

def test_save_then_semantic_search_finds_by_meaning():
    _clean()
    with get_conn() as conn:
        store.save_capture(
            conn,
            raw_text="Full transcript about switching careers into consulting.",
            summary="Sarah is considering leaving her job to start a consulting business.",
            keywords=["career", "consulting", "Sarah"],
            source="substack",
            source_url="https://example.com/post",
            lang="en",
        )
    # Query wording differs from the stored text -> semantic match required
    with get_conn() as conn:
        results = store.search_captures(conn, query="notes about people changing jobs", k=5)
    assert results, "expected at least one semantic match"
    assert "consulting" in results[0]["summary"].lower()
    assert 0.0 <= results[0]["score"] <= 1.0

def test_fetch_recent_and_stats():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="x", summary="first note about AI agents",
                           keywords=["ai"], source="youtube")
        store.save_capture(conn, raw_text="y", summary="second note about gardening",
                           keywords=["garden"], source="youtube")
    with get_conn() as conn:
        recent = store.fetch_recent(conn, n=10)
        s = store.compute_stats(conn)
    assert len(recent) == 2
    assert s["total"] == 2
    assert s["by_source"]["youtube"] == 2
```

- [ ] **Step 2: Run test to verify it fails** **[repo]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.store'` (or skipped if `DATABASE_URL` unset; set it before running — see Task 3.3).

- [ ] **Step 3: Write the implementation** **[repo]**

```python
# app/store.py
import psycopg
from psycopg.types.json import Json
from app.keywords import normalize_keywords
from app.embeddings import embed_passage, embed_query

def save_capture(conn: psycopg.Connection, *, raw_text: str, summary: str,
                 keywords: list[str], source: str | None = None,
                 source_url: str | None = None, lang: str | None = None,
                 metadata: dict | None = None) -> str:
    kws = normalize_keywords(keywords)
    emb = embed_passage(summary)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO captures
                (raw_text, summary, keywords, source, source_url, lang, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (raw_text, summary, kws, source, source_url, lang, Json(metadata or {}), emb),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return str(new_id)

def search_captures(conn: psycopg.Connection, *, query: str, k: int = 5) -> list[dict]:
    emb = embed_query(query)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, summary, keywords, source, source_url, lang, created_at,
                   1 - (embedding <=> %s) AS score
            FROM captures
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (emb, emb, k),
        )
        rows = cur.fetchall()
    return [_row_to_result(r) for r in rows]

def fetch_recent(conn: psycopg.Connection, *, n: int = 10) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, summary, keywords, source, source_url, lang, created_at, NULL::float
            FROM captures
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (n,),
        )
        rows = cur.fetchall()
    return [_row_to_result(r) for r in rows]

def compute_stats(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM captures")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT coalesce(source, 'unknown'), count(*) FROM captures GROUP BY 1"
        )
        by_source = {src: cnt for src, cnt in cur.fetchall()}
        cur.execute("SELECT min(created_at), max(created_at) FROM captures")
        first, last = cur.fetchone()
    return {
        "total": total,
        "by_source": by_source,
        "first_capture": first.isoformat() if first else None,
        "last_capture": last.isoformat() if last else None,
    }

def _row_to_result(r) -> dict:
    return {
        "id": str(r[0]),
        "summary": r[1],
        "keywords": list(r[2] or []),
        "source": r[3],
        "source_url": r[4],
        "lang": r[5],
        "created_at": r[6].isoformat() if r[6] else None,
        "score": float(r[7]) if r[7] is not None else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass** **[repo, needs DATABASE_URL]**

Run: `cd openbrain-mcp && python -m pytest tests/test_store.py -v`
Expected: PASS (2 tests). Requires the DB from Task 3.1–3.3 to be up and `DATABASE_URL` exported.

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-mcp/app/store.py openbrain-mcp/tests/test_store.py
git commit -m "feat(store): save/search/recent/stats over pgvector"
```

### Task 2.5: MCP server + bearer auth + health

**Files:**
- Create: `openbrain-mcp/app/server.py`

- [ ] **Step 1: Write the implementation** **[repo]**

```python
# app/server.py
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import OPENBRAIN_TOKEN
from app.db import get_conn
from app import store

mcp = FastMCP("openbrain")

@mcp.tool()
def save(raw_text: str, summary: str, keywords: list[str],
         source: str | None = None, source_url: str | None = None,
         lang: str | None = None) -> dict:
    """Store a captured note. The summary is embedded for semantic search.
    Pass the original text as raw_text, a concise summary, and ~5 keywords."""
    with get_conn() as conn:
        new_id = store.save_capture(
            conn, raw_text=raw_text, summary=summary, keywords=keywords,
            source=source, source_url=source_url, lang=lang,
        )
    return {"id": new_id, "stored": True}

@mcp.tool()
def search(query: str, k: int = 5) -> list[dict]:
    """Semantic search over captured notes. Returns the top-k matches by meaning."""
    with get_conn() as conn:
        return store.search_captures(conn, query=query, k=k)

@mcp.tool()
def list_recent(n: int = 10) -> list[dict]:
    """List the most recently captured notes."""
    with get_conn() as conn:
        return store.fetch_recent(conn, n=n)

@mcp.tool()
def stats() -> dict:
    """Summary statistics: total captures, counts by source, date range."""
    with get_conn() as conn:
        return store.compute_stats(conn)

class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        expected = f"Bearer {OPENBRAIN_TOKEN}"
        if not OPENBRAIN_TOKEN or request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})

def build_app() -> Starlette:
    app = mcp.streamable_http_app()           # Starlette app serving MCP at /mcp
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(BearerAuthMiddleware)
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=8080)
```

- [ ] **Step 2: Smoke-test locally (no DB needed for /health)** **[repo]**

Run, in two shells:
```bash
# shell 1
cd openbrain-mcp && OPENBRAIN_TOKEN=testtoken DATABASE_URL=postgresql://x python -m app.server
# shell 2
curl -s localhost:8080/health        # expect: {"ok":true}
curl -s -o /dev/null -w "%{http_code}\n" localhost:8080/mcp            # expect: 401 (no token)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer testtoken" localhost:8080/mcp   # expect: 200 or 406 (MCP handshake), NOT 401
```
Expected: `/health` returns ok; `/mcp` without token is 401; with token is not 401.

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/app/server.py
git commit -m "feat(server): MCP tools over streamable-http with bearer auth + health"
```

---

## Phase 3 — Containerize and run the database

### Task 3.1: Dockerfile for `openbrain-mcp`

**Files:**
- Create: `openbrain-mcp/Dockerfile`, `openbrain-mcp/README.md`

- [ ] **Step 1: Write the Dockerfile** **[repo]**

```dockerfile
FROM python:3.11-slim

# Build deps for psycopg/torch wheels are not needed (binary wheels), keep slim.
WORKDIR /app
COPY pyproject.toml ./
RUN python -m pip install --no-cache-dir .

# Pre-download the embedding model at build time so first request is fast
ENV OPENBRAIN_MODEL=intfloat/multilingual-e5-small
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY app ./app
COPY migrations ./migrations

EXPOSE 8080
CMD ["python", "-m", "app.server"]
```

- [ ] **Step 2: Write a short `openbrain-mcp/README.md`** **[repo]**

````markdown
# openbrain-mcp

Self-hosted MCP server for OpenBrain memory. Tools: `save`, `search`, `list_recent`, `stats`.
Embeds with `intfloat/multilingual-e5-small`; stores in Postgres + pgvector.

Env: `DATABASE_URL`, `OPENBRAIN_TOKEN`, optional `OPENBRAIN_MODEL`.
Run: `python -m app.server` (serves MCP at `/mcp`, health at `/health`, port 8080).
````

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-mcp/Dockerfile openbrain-mcp/README.md
git commit -m "build: Dockerfile for openbrain-mcp with prebaked model"
```

### Task 3.2: Compose file for the two services

**Files:**
- Create: `deploy/docker-compose.openbrain.yml`, `deploy/.env.example`

Use the shared network and Traefik settings discovered in Task 0.3. Replace `REPLACE_NETWORK`, `REPLACE_ENTRYPOINT`, `REPLACE_CERTRESOLVER`, and `OPENBRAIN_HOST` with the recorded values.

- [ ] **Step 1: Write `deploy/.env.example`** **[repo]**

```bash
# Copy to deploy/.env on the VPS and fill in. Do NOT commit the real .env.
POSTGRES_PASSWORD=change-me-long-random
OPENBRAIN_TOKEN=change-me-long-random
OPENBRAIN_HOST=brain.example.com
# DATABASE_URL is constructed inside compose from POSTGRES_PASSWORD; shown here for local test:
# DATABASE_URL=postgresql://openbrain:change-me-long-random@localhost:5432/openbrain
```

- [ ] **Step 2: Write the compose file** **[repo]**

```yaml
# deploy/docker-compose.openbrain.yml
services:
  openbrain-db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: openbrain
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: openbrain
    volumes:
      - openbrain_pgdata:/var/lib/postgresql/data
      - ../openbrain-mcp/migrations/001_init.sql:/docker-entrypoint-initdb.d/001_init.sql:ro
    networks: [REPLACE_NETWORK]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openbrain -d openbrain"]
      interval: 10s
      timeout: 5s
      retries: 5

  openbrain-mcp:
    build: ../openbrain-mcp
    environment:
      DATABASE_URL: postgresql://openbrain:${POSTGRES_PASSWORD}@openbrain-db:5432/openbrain
      OPENBRAIN_TOKEN: ${OPENBRAIN_TOKEN}
    depends_on:
      openbrain-db:
        condition: service_healthy
    networks: [REPLACE_NETWORK]
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.openbrain.rule=Host(`${OPENBRAIN_HOST}`)"
      - "traefik.http.routers.openbrain.entrypoints=REPLACE_ENTRYPOINT"
      - "traefik.http.routers.openbrain.tls.certresolver=REPLACE_CERTRESOLVER"
      - "traefik.http.services.openbrain.loadbalancer.server.port=8080"
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8080/health')\""]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  openbrain_pgdata:

networks:
  REPLACE_NETWORK:
    external: true
```

- [ ] **Step 3: Commit** **[repo]**

```bash
git add deploy/docker-compose.openbrain.yml deploy/.env.example
git commit -m "deploy: compose for openbrain-db + openbrain-mcp with Traefik labels"
```

### Task 3.3: Bring up the database and run the store tests against it

**[VPS]** (or laptop with Docker). Pull the repo onto the VPS first.

- [ ] **Step 1: Get the code onto the VPS**

```bash
git clone https://github.com/StephanSchipal/HermesPlusOpenbrain.git
cd HermesPlusOpenbrain/deploy
cp .env.example .env && nano .env   # set strong POSTGRES_PASSWORD, OPENBRAIN_TOKEN, OPENBRAIN_HOST
```

- [ ] **Step 2: Start only the database**

```bash
docker compose -f docker-compose.openbrain.yml up -d openbrain-db
docker compose -f docker-compose.openbrain.yml ps   # openbrain-db healthy
```
Expected: `openbrain-db` is `healthy`. The migration in `/docker-entrypoint-initdb.d` ran on first boot.

- [ ] **Step 3: Verify schema + extension**

```bash
docker exec -it $(docker ps --filter name=openbrain-db --format '{{.Names}}') \
  psql -U openbrain -d openbrain -c "\dx" -c "\d captures"
```
Expected: `vector` extension listed; `captures` table with an `embedding vector(384)` column and the two indexes.

- [ ] **Step 4: Run the store integration tests against it**

From the repo on the VPS (Python + deps installed via `pip install -e ".[dev]"` in `openbrain-mcp`):
```bash
cd ../openbrain-mcp
DATABASE_URL="postgresql://openbrain:$(grep POSTGRES_PASSWORD ../deploy/.env | cut -d= -f2)@localhost:5432/openbrain" \
  python -m pytest tests/ -v
```
Note: publish port 5432 temporarily for this test, or run pytest from a container on the same network. Expected: all tests PASS.

- [ ] **Step 5: Commit (if any fixes were needed)** **[repo]**

```bash
git commit -am "fix: adjustments from live DB integration run" || echo "no changes"
```

---

## Phase 4 — Deploy the MCP server behind Traefik

### Task 4.1: Build and start `openbrain-mcp`

**[VPS]**

- [ ] **Step 1: Build and start both services**

```bash
cd HermesPlusOpenbrain/deploy
docker compose -f docker-compose.openbrain.yml up -d --build
docker compose -f docker-compose.openbrain.yml ps
```
Expected: both `openbrain-db` and `openbrain-mcp` `healthy`. (First build downloads the model — minutes.)

- [ ] **Step 2: Verify internal health and auth**

```bash
NET=REPLACE_NETWORK
docker run --rm --network $NET curlimages/curl -s http://openbrain-mcp:8080/health   # {"ok":true}
docker run --rm --network $NET curlimages/curl -s -o /dev/null -w "%{http_code}\n" http://openbrain-mcp:8080/mcp   # 401
```
Expected: health ok; `/mcp` is 401 without a token.

### Task 4.2: Verify HTTPS + TLS from the public internet

**[VPS / laptop]**

- [ ] **Step 1: Confirm the cert and routing**

```bash
TOKEN=$(grep OPENBRAIN_TOKEN HermesPlusOpenbrain/deploy/.env | cut -d= -f2)
HOST=$(grep OPENBRAIN_HOST HermesPlusOpenbrain/deploy/.env | cut -d= -f2)
curl -s https://$HOST/health                                  # {"ok":true}
curl -s -o /dev/null -w "%{http_code}\n" https://$HOST/mcp    # 401 without token
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" https://$HOST/mcp   # not 401
```
Expected: valid Let's Encrypt cert (no TLS warning), `/health` ok, `/mcp` gated by token. If the cert is missing, recheck the DNS A record (Task 0.2) and Traefik entrypoint/certresolver names (Task 0.3).

---

## Phase 5 — Wire Hermes-Agent to OpenBrain

### Task 5.1: Register the MCP server in Hermes

**[VPS]** Exact mechanism per Hermes docs (https://hermes-agent.nousresearch.com/docs/) — Hermes supports "Connect any MCP server."

- [ ] **Step 1: Add the MCP server to Hermes config**

Point Hermes at the **internal** address (no TLS hop needed inside Docker). In Hermes' MCP configuration, add a streamable-http server:
- URL: `http://openbrain-mcp:8080/mcp`
- Header: `Authorization: Bearer <OPENBRAIN_TOKEN>`

Ensure the `hermes-agent` container shares `REPLACE_NETWORK` (from Task 0.3); if not, attach it:
```bash
docker network connect REPLACE_NETWORK $(docker ps --filter name=hermes --format '{{.Names}}' | head -1)
```

- [ ] **Step 2: Verify Hermes sees the tools**

Restart/reload Hermes, then:
```bash
docker exec -it $(docker ps --filter name=hermes --format '{{.Names}}' | head -1) hermes tools | grep -i openbrain
```
Expected: `save`, `search`, `list_recent`, `stats` appear (names may be prefixed, e.g. `openbrain.save`).

### Task 5.2: Add the capture instruction/skill to Hermes

**[VPS]** This tells Hermes what to do when you send content. Use the outcome of Task 0.1.

- [ ] **Step 1: Add a capture directive to Hermes' instructions/memory**

Add to Hermes' system instruction (or a Hermes skill) text equivalent to:

> When I send you a link or content from YouTube, Substack, or a similar source: (1) retrieve/read the content, (2) write a concise summary, (3) extract around 5 keywords, (4) call the `openbrain` `save` tool with `raw_text` = the source text (or my message if you cannot fetch it), `summary`, `keywords`, `source` (youtube/substack/other), and `source_url` if present. Then reply confirming the summary and keywords you stored. When I ask you to recall or find something I saved, use the `openbrain` `search` tool and answer from the results.

If Task 0.1 found Hermes cannot fetch links, change (1) to: "use the text I paste."

- [ ] **Step 2: End-to-end capture test via WhatsApp**

Send a YouTube or Substack link (or text) to Hermes on WhatsApp.
Expected: within seconds, a reply confirming a stored summary + ~5 keywords. Verify storage:
```bash
docker run --rm --network REPLACE_NETWORK -e TOKEN=$TOKEN curlimages/curl -s \
  -H "Authorization: Bearer $TOKEN" http://openbrain-mcp:8080/mcp >/dev/null
# Simpler check: query the DB count
docker exec -it $(docker ps --filter name=openbrain-db --format '{{.Names}}') \
  psql -U openbrain -d openbrain -c "SELECT count(*), max(created_at) FROM captures;"
```
Expected: row count incremented; latest `created_at` is just now.

- [ ] **Step 3: End-to-end recall test via WhatsApp**

Ask Hermes (differently worded than the stored text) to recall the item.
Expected: Hermes returns the captured summary via the `search` tool.

---

## Phase 6 — Connect laptop clients

### Task 6.1: Claude Desktop

**[laptop]**

- [ ] **Step 1: Add the remote MCP server**

In Claude Desktop's MCP/connectors settings, add a remote (HTTP) MCP server:
- URL: `https://<OPENBRAIN_HOST>/mcp`
- Header: `Authorization: Bearer <OPENBRAIN_TOKEN>`

(If your Claude Desktop version configures MCP via `claude_desktop_config.json` and needs a stdio bridge for remote HTTP, use `mcp-remote`:
```json
{
  "mcpServers": {
    "openbrain": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<OPENBRAIN_HOST>/mcp",
               "--header", "Authorization: Bearer <OPENBRAIN_TOKEN>"]
    }
  }
}
```
)

- [ ] **Step 2: Verify**

Restart Claude Desktop; confirm the `openbrain` tools appear. Ask: *"Search my brain for the thing I saved about <topic>."*
Expected: returns the capture from Phase 5.

### Task 6.2: Claude Code

**[laptop]**

- [ ] **Step 1: Add the server**

```bash
claude mcp add --transport http openbrain https://<OPENBRAIN_HOST>/mcp \
  --header "Authorization: Bearer <OPENBRAIN_TOKEN>"
```

- [ ] **Step 2: Verify**

```bash
claude mcp list
```
Expected: `openbrain` listed and reachable. In a session, the `save`/`search` tools are available.

---

## Phase 7 — End-to-end acceptance (spec §9)

**[mixed]** Confirm every success criterion from the spec.

- [ ] **Step 1: Capture latency + keywords**

Send a fresh YouTube link via WhatsApp; confirm a stored capture appears within a few seconds with a summary and ~5 keywords (`SELECT keywords, summary FROM captures ORDER BY created_at DESC LIMIT 1;`).

- [ ] **Step 2: Cross-tool semantic recall**

From **Claude Desktop** and **Claude Code**, run a semantically-worded query (different words than stored, and try one in German for an English note and vice-versa). Both return the right capture.

- [ ] **Step 3: WhatsApp recall parity**

Ask Hermes the same query; it returns the same capture.

- [ ] **Step 4: Persistence**

```bash
docker compose -f deploy/docker-compose.openbrain.yml restart openbrain-db
# wait for healthy, then:
docker exec -it $(docker ps --filter name=openbrain-db --format '{{.Names}}') \
  psql -U openbrain -d openbrain -c "SELECT count(*) FROM captures;"
```
Expected: count unchanged after restart (volume persisted).

- [ ] **Step 5: Cost/ownership check**

Confirm no external paid SaaS is in the path (embeddings local, DB local). Done.

- [ ] **Step 6: Final commit / tag**

```bash
git commit -am "docs: mark plan complete" || echo "nothing to commit"
git tag v0.1.0 && git push --tags
```

---

## Self-review notes (author)

- **Spec coverage:** Hosting/self-host (Phase 3–4), capture via WhatsApp+Hermes (Phase 5), summarize+~5 keywords by Hermes (Task 5.2), lean custom MCP server (Phase 2), local multilingual embeddings (Task 2.2), retrieval from WhatsApp + laptop (Phases 5–6), Traefik TLS + bearer token (Phase 4), data model (Task 1.1/2.4), all four MCP tools (Task 2.5), security model (Task 2.5 auth + Task 4), success criteria (Phase 7). Open questions §8 → Phase 0.
- **Type consistency:** tool `list_recent`/`stats` delegate to store `fetch_recent`/`compute_stats` (renamed to avoid shadowing); `save_capture`/`search_captures` signatures match their call sites; result dicts share `_row_to_result`. Vector param passed as Python list works because `register_vector` is called on every connection.
- **Known follow-ups (not blockers):** connection-per-request is fine for personal volume (add pooling later); HNSW index is created empty and fills as rows insert; consider a read-only token for laptop vs write token for Hermes (spec §6 optional hardening).
