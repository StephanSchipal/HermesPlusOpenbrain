# app/graph.py
"""Builds the /api/graph payload: combines list_recent()'s per-capture
keywords with cluster_captures()'s cluster membership into a
keyword-centric view -- one entry per keyword (its dominant cluster,
total count, and the captures that contain it) and one entry per
cluster (a deterministic label, size, and its captures)."""
from app.subject_line import make_subject_line

def build_keyword_graph(captures: list[dict], clusters: list[dict]) -> dict:
    """captures: list_recent()'s output ([{"id", "keywords", ...}, ...]).
    clusters: cluster_captures()'s "clusters" list
        ([{"cluster_id", "size", "members": [{"id", "summary", "central"}]}]).
    Captures present in one input but not the other (a race between the two
    separate MCP calls that produce these two inputs -- routes.py calls
    list_recent() and cluster_captures() one after another, not atomically)
    are skipped rather than raising."""
    member_info: dict[str, dict] = {}
    for cluster in clusters:
        for member in cluster["members"]:
            member_info[member["id"]] = {
                "cluster_id": cluster["cluster_id"],
                "central": member["central"],
                "subject_line": make_subject_line(member["summary"]),
            }

    capture_keywords = {c["id"]: c["keywords"] for c in captures}

    # keyword -> cluster_id -> occurrence count (dominant-cluster assignment)
    keyword_cluster_counts: dict[str, dict[int, int]] = {}
    # keyword -> captures containing it (not just captures in its cluster)
    keyword_captures: dict[str, list[dict]] = {}

    for capture_id, keywords in capture_keywords.items():
        info = member_info.get(capture_id)
        if info is None:
            continue
        for keyword in keywords:
            counts = keyword_cluster_counts.setdefault(keyword, {})
            counts[info["cluster_id"]] = counts.get(info["cluster_id"], 0) + 1
            keyword_captures.setdefault(keyword, []).append({
                "id": capture_id,
                "subject_line": info["subject_line"],
                "central": info["central"],
            })

    keywords_out = []
    for keyword, counts in keyword_cluster_counts.items():
        # Highest count wins; ties broken by the lowest cluster_id.
        dominant_cluster_id = max(counts, key=lambda cid: (counts[cid], -cid))
        keywords_out.append({
            "keyword": keyword,
            "count": sum(counts.values()),
            "cluster_id": dominant_cluster_id,
            "captures": keyword_captures[keyword],
        })

    clusters_out = []
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        label_counts: dict[str, int] = {}
        for member in cluster["members"]:
            for keyword in capture_keywords.get(member["id"], []):
                label_counts[keyword] = label_counts.get(keyword, 0) + 1
        if label_counts:
            # Highest count wins; ties broken alphabetically.
            label = min(label_counts, key=lambda kw: (-label_counts[kw], kw))
        else:
            label = f"cluster {cluster_id}"
        clusters_out.append({
            "cluster_id": cluster_id,
            "label": label,
            "size": cluster["size"],
            "captures": [
                {
                    "id": m["id"],
                    "subject_line": member_info[m["id"]]["subject_line"],
                    "central": m["central"],
                }
                for m in cluster["members"]
            ],
        })

    return {"clusters": clusters_out, "keywords": keywords_out}
