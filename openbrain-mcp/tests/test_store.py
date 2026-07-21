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

def test_find_near_duplicates_detects_similar_but_not_identical_summaries():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a",
                           summary="Sarah is considering leaving her job to start a consulting business.",
                           keywords=["career"], source="other")
        store.save_capture(conn, raw_text="b",
                           summary="Sarah is considering leaving her job to start a consulting company.",
                           keywords=["career"], source="other")
        store.save_capture(conn, raw_text="c", summary="A recipe for sourdough bread.",
                           keywords=["cooking"], source="other")
    with get_conn() as conn:
        pairs = store.find_near_duplicates(conn, limit=10)  # default threshold: 0.95
    assert len(pairs) == 1, f"expected exactly the Sarah/Sarah pair, got {pairs}"
    top = pairs[0]
    assert "sourdough" not in top["summary_a"] and "sourdough" not in top["summary_b"]
    assert "Sarah" in top["summary_a"] and "Sarah" in top["summary_b"]
    assert 0.95 < top["similarity"] <= 1.0

def test_find_near_duplicates_respects_threshold():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="A talk about memory systems.",
                           keywords=["memory"], source="other")
        store.save_capture(conn, raw_text="b", summary="A recipe for sourdough bread.",
                           keywords=["cooking"], source="other")
    with get_conn() as conn:
        # cosine similarity is capped at 1.0 -- no pair can ever clear this bar
        pairs = store.find_near_duplicates(conn, threshold=1.01, limit=10)
    assert pairs == []

def test_find_near_duplicates_respects_limit():
    _clean()
    with get_conn() as conn:
        for i in range(4):
            store.save_capture(
                conn, raw_text=f"t{i}",
                summary="Meeting notes: discussed the Q3 budget with the finance team.",
                keywords=["meeting"], source="other",
                source_url=f"https://example.com/meeting-{i}",
            )
    with get_conn() as conn:
        # 4 identical-summary captures -> C(4,2) = 6 qualifying pairs at threshold 0.0
        pairs = store.find_near_duplicates(conn, threshold=0.0, limit=3)
    assert len(pairs) == 3

_CLUSTER_TOPICS = {
    "career": [
        "Sarah is considering leaving her job to start a consulting business.",
        "Sarah is considering leaving her job to start a consulting company.",
        "Sarah is considering leaving her job to start a consulting firm.",
        "Sarah is considering leaving her job to start a consulting practice.",
        "Sarah is considering leaving her job to start a consulting agency.",
    ],
    "cooking": [
        "A recipe for sourdough bread using a rye starter.",
        "A recipe for sourdough bread using a wheat starter.",
        "A recipe for sourdough bread using a spelt starter.",
        "A recipe for sourdough bread using an einkorn starter.",
        "A recipe for sourdough bread using a kamut starter.",
    ],
    "meeting": [
        "Meeting notes: discussed the Q3 budget with the finance team.",
        "Meeting notes: discussed the Q3 forecast with the finance team.",
        "Meeting notes: discussed the Q3 roadmap with the finance team.",
        "Meeting notes: discussed the Q3 headcount with the finance team.",
        "Meeting notes: discussed the Q3 timeline with the finance team.",
    ],
}

def _save_topic_fixture() -> dict:
    """Saves 15 captures (3 topics x 5 near-duplicate-worded captures each,
    same 'vary one word' technique as the find_near_duplicates tests, to keep
    within-topic similarity high and between-topic similarity low).
    Returns {capture_id: topic_name}."""
    id_to_topic = {}
    with get_conn() as conn:
        for topic, summaries in _CLUSTER_TOPICS.items():
            for i, summary in enumerate(summaries):
                r = store.save_capture(
                    conn, raw_text=f"{topic}-{i}", summary=summary,
                    keywords=[topic], source="other",
                    source_url=f"https://example.com/{topic}-{i}",
                )
                id_to_topic[r["id"]] = topic
    return id_to_topic

def _assert_clusters_are_topic_pure(clusters, id_to_topic):
    for cluster in clusters:
        topics_in_cluster = {id_to_topic[m["id"]] for m in cluster["members"]}
        assert len(topics_in_cluster) == 1, f"cluster mixed topics: {topics_in_cluster}"

def test_cluster_captures_with_explicit_k_groups_by_theme():
    _clean()
    id_to_topic = _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn, k=3)
    assert result["k"] == 3
    assert len(result["clusters"]) == 3
    for cluster in result["clusters"]:
        assert cluster["size"] == 5
    _assert_clusters_are_topic_pure(result["clusters"], id_to_topic)

def test_cluster_captures_auto_k_picks_a_reasonable_cluster_count():
    _clean()
    id_to_topic = _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn)  # k=None -> auto via silhouette
    assert 2 <= result["k"] <= 10  # _MAX_AUTO_K caps the search at 10
    # Not asserting k == 3 exactly (would over-fit the test to this specific
    # embedding model's silhouette behavior) -- but however many clusters it
    # picked, each one must still be internally topic-pure. A sub-split of a
    # single topic into 2 clusters is fine; a cluster mixing topics is not.
    _assert_clusters_are_topic_pure(result["clusters"], id_to_topic)

def test_cluster_captures_marks_up_to_three_members_central_per_cluster():
    _clean()
    _save_topic_fixture()
    with get_conn() as conn:
        result = store.cluster_captures(conn, k=3)
    for cluster in result["clusters"]:
        central = [m for m in cluster["members"] if m["central"]]
        non_central = [m for m in cluster["members"] if not m["central"]]
        # size 5 per cluster here -> exactly 3 central, 2 not (regression
        # guard against "all members marked central" or "none marked" bugs;
        # exact nearest-neighbor correctness is sklearn's own tested
        # behavior via KMeans.transform(), not this project's to re-verify).
        assert len(central) == min(3, cluster["size"])
        assert len(non_central) == cluster["size"] - len(central)

def test_cluster_captures_reports_error_below_minimum():
    _clean()
    with get_conn() as conn:
        store.save_capture(conn, raw_text="a", summary="only one note",
                           keywords=["x"], source="other")
        store.save_capture(conn, raw_text="b", summary="only two notes",
                           keywords=["x"], source="other")
    with get_conn() as conn:
        result = store.cluster_captures(conn)
    assert result == {"error": "need at least 4 captures to cluster, have 2"}

def test_cluster_captures_rejects_out_of_range_explicit_k():
    _clean()
    _save_topic_fixture()  # 15 captures
    with get_conn() as conn:
        too_high = store.cluster_captures(conn, k=100)
        zero = store.cluster_captures(conn, k=0)
        negative = store.cluster_captures(conn, k=-1)
    assert too_high == {"error": "k must be between 1 and 15, got 100"}
    assert zero == {"error": "k must be between 1 and 15, got 0"}
    assert negative == {"error": "k must be between 1 and 15, got -1"}
