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

def list_deletions(*, path: str | None = None, limit: int | None = None) -> list[dict]:
    query = """
        SELECT id, capture_id, subject_line, keywords, source_url, captured_at, deleted_at
        FROM delete_log ORDER BY deleted_at DESC, id DESC
    """
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    with get_conn(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {**dict(row), "keywords": json.loads(row["keywords"] or "[]")}
        for row in rows
    ]
