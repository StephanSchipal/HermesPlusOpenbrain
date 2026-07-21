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

def find_near_duplicates(conn: psycopg.Connection, *, threshold: float = 0.95,
                         limit: int = 50) -> list[dict]:
    """Pairs of captures whose summaries are near-duplicates by cosine similarity.
    Read-only -- caller decides what to do (e.g. the existing `delete` tool)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.summary, b.id, b.summary,
                   1 - (a.embedding <=> b.embedding) AS similarity
            FROM captures a
            JOIN captures b ON a.id < b.id
            WHERE 1 - (a.embedding <=> b.embedding) > %s
            ORDER BY similarity DESC
            LIMIT %s
            """,
            (threshold, limit),
        )
        rows = cur.fetchall()
    return [
        {"id_a": str(r[0]), "summary_a": r[1], "id_b": str(r[2]), "summary_b": r[3],
         "similarity": float(r[4])}
        for r in rows
    ]

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

    if k is not None and not (1 <= k <= len(rows)):
        return {"error": f"k must be between 1 and {len(rows)}, got {k}"}

    ids = [str(r[0]) for r in rows]
    summaries = [r[1] for r in rows]
    embeddings = [r[2].to_list() for r in rows]  # pgvector Vector -> plain list for sklearn

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

def _auto_select_k(embeddings: list[list[float]], max_k: int = _MAX_AUTO_K) -> int:
    """Try k = 2..min(max_k, n-1) and return the k with the best silhouette score."""
    upper = min(max_k, len(embeddings) - 1)
    best_k, best_score = 2, -1.0
    for candidate_k in range(2, upper + 1):
        labels = KMeans(n_clusters=candidate_k, random_state=0, n_init=10).fit_predict(embeddings)
        score = silhouette_score(embeddings, labels)
        if score > best_score:
            best_k, best_score = candidate_k, score
    return best_k

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
