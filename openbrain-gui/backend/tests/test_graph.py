# tests/test_graph.py
from app.graph import build_keyword_graph
from app.subject_line import make_subject_line

def test_build_keyword_graph_basic_two_clusters():
    captures = [
        {"id": "a", "keywords": ["claude", "ai"]},
        {"id": "b", "keywords": ["claude"]},
        {"id": "c", "keywords": ["wall street"]},
    ]
    clusters = [
        {"cluster_id": 0, "size": 2, "members": [
            {"id": "a", "summary": "Claude does things", "central": True},
            {"id": "b", "summary": "Claude again", "central": False},
        ]},
        {"cluster_id": 1, "size": 1, "members": [
            {"id": "c", "summary": "Banks and markets", "central": True},
        ]},
    ]
    result = build_keyword_graph(captures, clusters)

    keywords_by_name = {k["keyword"]: k for k in result["keywords"]}
    assert keywords_by_name["claude"]["count"] == 2
    assert keywords_by_name["claude"]["cluster_id"] == 0
    assert {c["id"] for c in keywords_by_name["claude"]["captures"]} == {"a", "b"}
    assert keywords_by_name["ai"]["count"] == 1
    assert keywords_by_name["ai"]["cluster_id"] == 0
    assert keywords_by_name["wall street"]["count"] == 1
    assert keywords_by_name["wall street"]["cluster_id"] == 1

    clusters_by_id = {c["cluster_id"]: c for c in result["clusters"]}
    assert clusters_by_id[0]["label"] == "claude"
    assert clusters_by_id[0]["size"] == 2
    assert clusters_by_id[1]["label"] == "wall street"

def test_build_keyword_graph_tie_broken_by_lowest_cluster_id():
    # "shared" occurs once in cluster 1 and once in cluster 0 -- tied count,
    # lowest cluster_id must win the keyword's dominant-cluster assignment.
    captures = [
        {"id": "a", "keywords": ["shared"]},
        {"id": "b", "keywords": ["shared"]},
    ]
    clusters = [
        {"cluster_id": 1, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
        {"cluster_id": 0, "size": 1, "members": [{"id": "b", "summary": "y", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    shared = next(k for k in result["keywords"] if k["keyword"] == "shared")
    assert shared["cluster_id"] == 0

def test_build_keyword_graph_label_tie_broken_alphabetically():
    captures = [{"id": "a", "keywords": ["zebra", "apple"]}]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["label"] == "apple"

def test_build_keyword_graph_skips_capture_missing_from_either_input():
    # "b" is in captures but absent from every cluster's members -- simulates
    # the two-MCP-call race documented in graph.py; must be skipped, not raise.
    captures = [
        {"id": "a", "keywords": ["claude"]},
        {"id": "b", "keywords": ["ghost"]},
    ]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert [k["keyword"] for k in result["keywords"]] == ["claude"]

def test_build_keyword_graph_central_flag_preserved_on_both_sides():
    captures = [{"id": "a", "keywords": ["claude"]}]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["keywords"][0]["captures"][0]["central"] is True
    assert result["clusters"][0]["captures"][0]["central"] is True

def test_build_keyword_graph_uses_subject_line_not_raw_summary():
    long_summary = " ".join(f"word{i}" for i in range(20))
    captures = [{"id": "a", "keywords": ["claude"]}]
    clusters = [
        {"cluster_id": 0, "size": 1,
         "members": [{"id": "a", "summary": long_summary, "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["captures"][0]["subject_line"] == make_subject_line(long_summary)

def test_build_keyword_graph_label_falls_back_when_cluster_has_no_keywords():
    captures = [{"id": "a", "keywords": []}]
    clusters = [
        {"cluster_id": 3, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["label"] == "cluster 3"
