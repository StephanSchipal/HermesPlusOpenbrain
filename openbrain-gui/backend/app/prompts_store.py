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
