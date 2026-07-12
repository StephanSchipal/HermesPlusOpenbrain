# tests/test_store.py
import os
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

def test_saving_same_url_twice_is_deduped():
    _clean()
    url = "https://youtube.com/watch?v=xyz"
    with get_conn() as conn:
        r1 = store.save_capture(conn, raw_text="t1", summary="a talk about memory systems",
                                keywords=["memory"], source="youtube", source_url=url)
        r2 = store.save_capture(conn, raw_text="t1 again", summary="same talk resent",
                                keywords=["memory"], source="youtube", source_url=url)
    assert r1["stored"] is True and r1["deduped"] is False
    assert r2["deduped"] is True and r2["id"] == r1["id"]
    with get_conn() as conn:
        assert store.compute_stats(conn)["total"] == 1  # only one row

def test_delete_removes_row():
    _clean()
    with get_conn() as conn:
        r = store.save_capture(conn, raw_text="z", summary="note to delete",
                               keywords=["tmp"], source="other")
        assert store.delete_capture(conn, capture_id=r["id"]) is True
        assert store.delete_capture(conn, capture_id=r["id"]) is False  # already gone
        assert store.compute_stats(conn)["total"] == 0

def test_update_changes_summary_and_reembeds():
    _clean()
    with get_conn() as conn:
        r = store.save_capture(conn, raw_text="w", summary="old summary about cooking",
                               keywords=["cooking"], source="other")
        ok = store.update_capture(conn, capture_id=r["id"],
                                  summary="new summary about astrophysics",
                                  keywords=["space", "physics"])
    assert ok is True
    with get_conn() as conn:
        hits = store.search_captures(conn, query="notes about the universe and stars", k=1)
    assert hits and hits[0]["id"] == r["id"]  # re-embedding took effect
