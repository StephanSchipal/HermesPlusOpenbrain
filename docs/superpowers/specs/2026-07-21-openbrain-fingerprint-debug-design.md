# OpenBrain — Explicit Fingerprint Introspection Tool — Design Spec

**Date:** 2026-07-21
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-21)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add a **read-only, DB-free** MCP tool that exposes the fingerprint mechanism
already used internally by `save` (exact URL/text dedup), so a caller can see
*which hash* a given `raw_text`/`source_url` produces and *what normalized
string* that hash was computed from — without writing anything and without
triggering a save.

This is capability 2 of 4 planned additions to `openbrain-mcp`, in this
order: (1) embedding-based duplicate detection — done, (2) this spec, (3)
clustering, (4) classification.

## 2. Context & constraints

- Existing system has seven MCP tools (`save`, `search`, `list_recent`,
  `stats`, `delete`, `update`, `find_near_duplicates`); `store.py` owns all
  SQL, `server.py` only wraps `store.py` calls as `@mcp.tool()`s (see
  [`2026-06-30-hermes-openbrain-memory-design.md`](2026-06-30-hermes-openbrain-memory-design.md)
  and
  [`2026-07-20-openbrain-duplicate-detection-design.md`](2026-07-20-openbrain-duplicate-detection-design.md)).
- `content_fingerprint()` in `app/fingerprint.py` already computes a stable
  SHA-256 dedup key: it prefers a normalized URL (strips scheme, `www.`,
  trailing slash, known tracking params) and falls back to normalized text
  (trim/lowercase/collapse whitespace) when no URL is given. `save` uses this
  internally and already reports `deduped: true/false` plus the existing id
  — that path is not being changed.
- This tool is **not** a duplicate-check against the database (that's what
  `save`'s dedup and `find_near_duplicates` already do). It answers a
  narrower question: "what hash/normalized string would this input produce,
  and why" — useful for debugging normalization behavior (e.g. confirming
  two URLs collide, or understanding why two didn't).

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Scope | **Pure computation, no DB check** | User explicitly chose this over a combined "compute + check existence" tool — `save` and `find_near_duplicates` already cover duplicate detection; this tool's job is introspection only. |
| Return shape | **Hash + normalized basis + which basis was used** | Chosen use case is debugging normalization, not just getting a hash to compare externally — so the response must show the intermediate string, not just the final digest. |
| Location | **`app/fingerprint.py`**, called **directly from `server.py`** (bypassing `store.py`) | No `conn`/DB access is needed, so routing through `store.py` (whose functions all take `conn` as first arg) would be an empty pass-through. Deliberately breaks the "server → store" pattern used by the other six tools, for a documented reason (YAGNI). |
| Existing `content_fingerprint()` | **Unchanged signature/behavior** | Refactored internally to share a new `_compute_basis()` helper with the new debug function, so there's one source of truth for the normalization logic — but callers of `content_fingerprint()` (i.e. `save_capture`) see no behavior change. |

## 4. Design

### 4.1 `app/fingerprint.py`

```python
def _compute_basis(*, source_url: str | None, raw_text: str) -> tuple[str, str]:
    """Returns (normalized_basis, basis_source) where basis_source is "url" or "text"."""
    if source_url:
        return _normalize_url(source_url), "url"
    return _normalize_text(raw_text), "text"

def content_fingerprint(*, source_url: str | None, raw_text: str) -> str:
    """Stable dedup key. Prefer the normalized URL; fall back to normalized text.
    ...(existing docstring unchanged)...
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

### 4.2 `app/server.py`

```python
from app.fingerprint import content_fingerprint_debug

@mcp.tool()
def compute_fingerprint(raw_text: str, source_url: str | None = None) -> dict:
    """Show the dedup fingerprint `save` would compute for this input, and the
    normalized string it's based on. Read-only, no DB access -- does not check
    whether this fingerprint already exists (use `save` or `find_near_duplicates`
    for that)."""
    return content_fingerprint_debug(source_url=source_url, raw_text=raw_text)
```

No schema change, no migration, no new `store.py` function.

### 4.3 Test plan (pure unit tests, no Postgres container needed)

Added to `tests/test_fingerprint.py`, alongside the existing
`content_fingerprint` tests:

1. `test_debug_matches_content_fingerprint` — for the same inputs,
   `content_fingerprint_debug()["fingerprint"] == content_fingerprint(...)`.
2. `test_debug_reports_url_basis_when_url_present` — `source_url` given →
   `basis_source == "url"` and `normalized_basis` equals the normalized URL.
3. `test_debug_reports_text_basis_when_no_url` — `source_url=None` →
   `basis_source == "text"` and `normalized_basis` equals the normalized
   text.

## 5. Non-goals (this iteration)

- No DB lookup / duplicate-existence check (covered by `save` and
  `find_near_duplicates`).
- No change to `save`'s behavior or to `content_fingerprint()`'s public
  signature.
- No new `store.py` function — this tool has no DB access.
- No manual Docker Compose smoke test required for DB behavior (nothing
  touches the DB); a lightweight MCP-protocol smoke test (tool callable,
  auth works, dict return deserializes) is still worth a quick manual check
  since it's cheap, but is not gated on a throwaway Postgres container the
  way capability 1's was.

## 6. Success criteria

- `compute_fingerprint(raw_text=..., source_url=...)` returns the same
  `fingerprint` value `save` would use for identical inputs.
- `basis_source` correctly reflects whether the URL or text path was taken.
- All three new tests pass alongside the existing 21 tests (no regressions).
- `content_fingerprint()`'s existing callers (`save_capture`) are unaffected
  — existing fingerprint/dedup tests in `test_fingerprint.py` and
  `test_store.py` still pass unchanged.

## 7. Implementation outline (to be expanded into a plan)

1. Refactor `app/fingerprint.py`: extract `_compute_basis()`, add
   `content_fingerprint_debug()`.
2. Add the three new tests to `tests/test_fingerprint.py`; run full test
   suite (no DB needed for these, but run the whole suite to confirm no
   regressions).
3. Add the `compute_fingerprint` MCP tool to `app/server.py`.
4. Update `README.md`'s tool table.
5. Light manual smoke test (MCP client call against local compose stack) to
   confirm the tool is reachable and its dict return deserializes correctly.
