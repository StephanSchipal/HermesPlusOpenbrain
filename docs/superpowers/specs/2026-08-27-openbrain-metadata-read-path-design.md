# OpenBrain: metadata read path + merge-on-update — Design

**Date:** 2026-08-27
**Status:** Approved

## Problem

`captures.metadata` (jsonb) is written by the `save` and `update` MCP tools but
never returned by any read path:

- `search_captures` / `fetch_recent` SELECT lists omit it (`app/store.py`).
- `_row_to_result` omits it.
- `cluster_captures` / `classify_captures` select only `id, summary, embedding`.
- There is no get-by-id tool (`search(capture_id=...)` deliberately *excludes*
  the target row).

So metadata is write-only across the whole tool surface. Worse, `update_capture`
does a **full replace** of the metadata column, so the documented
"classify → `update(id, metadata={category})`" workflow silently clobbers any
existing keys — and with no read path, the loss is invisible.

External context: a reader of the public repo flagged the write-only field.
Adding an MIT `LICENSE` (separate, trivial change) is tracked alongside this but
is not part of this design.

## Goals

1. Every `search` / `list_recent` result row carries its `metadata` when it is
   non-empty.
2. A get-by-id path returns metadata (reuse `list_recent(ids=[...])`, no new tool).
3. `update(id, metadata=...)` merges instead of replacing, so partial metadata
   writes stop destroying sibling keys.

## Non-goals

- Deleting individual metadata keys via `update` (no current need).
- Deep/recursive merge — Postgres `||` shallow merge is sufficient.
- A dedicated `get` tool — `list_recent(ids=[...])` already resolves exact ids.
- Filtering or searching *by* metadata contents.
- Any schema migration (the `metadata jsonb NOT NULL DEFAULT '{}'` column
  already exists).
- Changes to the GUI backend/frontend.

## Design

All changes in `openbrain-mcp/`.

### 1. Read path (`app/store.py`)

- Add `metadata` to the SELECT column list in `search_captures` (after the
  `score` expression) and in `fetch_recent` (after the `NULL::float` placeholder),
  keeping both row shapes aligned so `_row_to_result` can stay shared.
- `_row_to_result`: read the new trailing column. Include `"metadata"` in the
  returned dict **only when the value is a non-empty dict**. Empty `{}` → key
  absent, so the common no-metadata capture adds zero payload (matches the
  project's token-cost sensitivity).

### 2. Merge-on-update (`app/store.py`)

- In `update_capture`, change the metadata assignment from
  `metadata = %s` to `metadata = metadata || %s::jsonb` (Postgres jsonb
  shallow merge). Parameter stays `Json(metadata)`.
- Behaviour:
  - existing `{"a":1}` + `update(metadata={"b":2})` → `{"a":1,"b":2}`
  - existing `{"a":1}` + `update(metadata={"a":9})` → `{"a":9}` (same-key
    shallow overwrite)
  - `update(metadata={})` → no content change (still appends `updated_at = now()`
    and returns `True` if the row exists, unchanged from today)
- Update the `update_capture` docstring to state merge semantics and the
  "cannot delete keys" limitation.

### 3. Docstrings (`app/server.py`)

- `update` tool: replace any "full replace / overwrites existing metadata"
  wording with the merge semantics.
- `classify_captures` tool: its docstring currently warns that
  `update(id, metadata=...)` "is a full replace, not a merge, so this will
  overwrite any existing metadata" — correct it to describe the merge.
- `list_recent` tool: add one line noting that `ids=[...]` is the way to fetch
  full records (including metadata) by id.

## Data flow

`save`/`update` → `captures.metadata jsonb` → `search_captures` /
`fetch_recent` SELECT → `_row_to_result` → MCP tool JSON response → Hermes /
GUI. Only the two SELECTs and `_row_to_result` change; callers upstream and
downstream are unaffected (additive key).

## Error handling

No new failure modes. `metadata || %s::jsonb` on a `NOT NULL DEFAULT '{}'`
column with a `Json(dict)` param cannot produce NULL. Non-dict metadata is
already prevented by the `dict | None` type hint on the tool signatures and is
not revalidated here.

## Testing (TDD, `tests/test_store.py`)

Real Postgres+pgvector, same `_clean()` / `get_conn()` pattern as the existing
suite. New tests, each written failing-first:

1. `test_save_persists_metadata_and_search_returns_it` — `save(metadata={...})`,
   then `search_captures(query=...)` result row equals the stored dict.
2. `test_metadata_absent_from_result_when_empty` — `save` with no metadata →
   result row has no `"metadata"` key.
3. `test_fetch_recent_by_ids_returns_metadata` — get-by-id path carries metadata.
4. `test_update_merges_metadata_instead_of_replacing` — save `{"a":1}`,
   `update(metadata={"b":2})`, read back `{"a":1,"b":2}`.
5. `test_update_metadata_overwrites_same_key_shallowly` — save `{"a":1,"b":2}`,
   `update(metadata={"a":9})`, read back `{"a":9,"b":2}`.

No `server.py`-layer tests — consistent with the existing suite, which exercises
tool logic at the `store` layer.

## Rollout

Pure code change to `openbrain-mcp`. Ships with the container's normal
rebuild/redeploy. No DB migration, no config, no coordinated client change
(the new key is additive and Hermes/GUI ignore unknown keys today).
