# app/store.py
import psycopg
from psycopg.types.json import Json
from app.keywords import normalize_keywords
from app.embeddings import embed_passage, embed_query
from app.fingerprint import content_fingerprint

def save_capture(conn: psycopg.Connection, *, raw_text: str, summary: str,
                 keywords: list[str], source: str | None = None,
                 source_url: str | None = None, lang: str | None = None,
                 metadata: dict | None = None) -> dict:
    """Insert a capture, or return the existing one if the fingerprint matches (dedup).

    Returns {"id", "stored": bool, "deduped": bool}. When deduped, no embedding is computed.
    """
    fp = content_fingerprint(source_url=source_url, raw_text=raw_text)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM captures WHERE fingerprint = %s", (fp,))
        existing = cur.fetchone()
    if existing:
        return {"id": str(existing[0]), "stored": False, "deduped": True}

    kws = normalize_keywords(keywords)
    emb = embed_passage(summary)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO captures
                (raw_text, summary, keywords, source, source_url, lang, metadata,
                 fingerprint, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fingerprint) DO NOTHING
            RETURNING id
            """,
            (raw_text, summary, kws, source, source_url, lang, Json(metadata or {}), fp, emb),
        )
        row = cur.fetchone()
        if row is None:  # lost an insert race on the same fingerprint
            cur.execute("SELECT id FROM captures WHERE fingerprint = %s", (fp,))
            row = cur.fetchone()
            conn.commit()
            return {"id": str(row[0]), "stored": False, "deduped": True}
        new_id = row[0]
    conn.commit()
    return {"id": str(new_id), "stored": True, "deduped": False}

def search_captures(conn: psycopg.Connection, *, query: str, k: int = 5) -> list[dict]:
    emb = embed_query(query)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, summary, keywords, source, source_url, lang, created_at,
                   1 - (embedding <=> %s::vector) AS score
            FROM captures
            ORDER BY embedding <=> %s::vector
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

def delete_capture(conn: psycopg.Connection, *, capture_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM captures WHERE id = %s", (capture_id,))
        deleted = cur.rowcount > 0
    conn.commit()
    return deleted

def update_capture(conn: psycopg.Connection, *, capture_id: str,
                   summary: str | None = None, keywords: list[str] | None = None,
                   metadata: dict | None = None) -> bool:
    """Update given fields; re-embed when summary changes; bump updated_at."""
    sets: list[str] = []
    params: list = []
    if summary is not None:
        sets += ["summary = %s", "embedding = %s"]
        params += [summary, embed_passage(summary)]
    if keywords is not None:
        sets.append("keywords = %s")
        params.append(normalize_keywords(keywords))
    if metadata is not None:
        sets.append("metadata = %s")
        params.append(Json(metadata))
    if not sets:
        return False
    sets.append("updated_at = now()")
    params.append(capture_id)
    # Column fragments are static literals; values are parameterized -> injection-safe.
    with conn.cursor() as cur:
        cur.execute(f"UPDATE captures SET {', '.join(sets)} WHERE id = %s", params)
        updated = cur.rowcount > 0
    conn.commit()
    return updated

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
