# tests/test_routes.py
"""Route-level tests: FastAPI TestClient with openbrain-mcp mocked at the
module boundary (app.mcp_client) -- no real network calls. Subject-line
generation is pure/deterministic (app.subject_line), so it needs no
mocking. Real SQLite (a tmp_path file) is used for prompts/delete-log,
since that IS this backend's own data."""
import json
import pytest
from fastapi.testclient import TestClient
from mcp import types

import app.db as db_module
import app.mcp_client as mcp_client_module
from app.main import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "GUI_DB_PATH", str(tmp_path / "gui.db"))
    return TestClient(create_app())

def _dict_result(payload: dict) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))])

def _list_result(payload: list) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="unstructured, ignored")],
        structuredContent={"result": payload},
    )

def test_get_stats_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "stats" and arguments == {}
        return _dict_result({"total": 3, "by_source": {"youtube": 3}})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 3, "by_source": {"youtube": 3}}

def test_get_keywords_filters_case_insensitively(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_keywords"
        return _list_result([{"keyword": "AI", "count": 5}, {"keyword": "cooking", "count": 2}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/keywords", params={"filter": "ai"})
    assert resp.status_code == 200
    assert resp.json() == [{"keyword": "AI", "count": 5}]

def test_search_adds_subject_line_per_row(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "search"
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result(
            [{"id": "abc", "summary": "Sarah is considering a pivot", "keywords": ["career"]}]
        )
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"query": "career notes"})
    assert resp.status_code == 200
    assert resp.json() == [{
        "id": "abc", "summary": "Sarah is considering a pivot",
        "keywords": ["career"], "subject_line": "Sarah is considering a pivot",
    }]

def test_search_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": "whatsapp", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "keywords": ["sarah"], "keyword_mode": "and",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={
        "query": "career notes", "source": "whatsapp",
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "keywords": ["sarah"], "keyword_mode": "and",
    })
    assert resp.status_code == 200
    assert resp.json() == []

def test_search_by_capture_id(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments["capture_id"] == "abc" and arguments["query"] is None
        return _list_result([{"id": "def", "summary": "a neighbor", "keywords": []}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"capture_id": "abc"})
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "def"

def test_search_error_dict_from_mcp_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # search's return annotation is a Union -- its error dict still
        # arrives via structuredContent, so _list_result (not _dict_result)
        # is the correct mock here (see the note above about Union handling).
        return _list_result({"error": "exactly one of query or capture_id must be given"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={})
    assert resp.status_code == 400
    assert "exactly one of" in resp.json()["detail"]

def test_search_rejects_invalid_keyword_mode(client):
    resp = client.post("/api/search", json={"query": "x", "keyword_mode": "xor"})
    assert resp.status_code == 422

def test_delete_capture_logs_before_calling_mcp(client, monkeypatch):
    calls = []
    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        return _dict_result({"id": "abc", "deleted": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/captures/abc/delete", json={
        "subject_line": "Sarah's career pivot", "keywords": ["career"],
        "source_url": "https://example.com/post", "created_at": "2026-07-20T14:32:00+00:00",
    })
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "deleted": True}
    assert calls == [("delete", {"id": "abc"})]
    log = client.get("/api/delete-log").json()
    assert len(log) == 1
    assert log[0]["capture_id"] == "abc"
    assert log[0]["keywords"] == ["career"]

def test_update_capture_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {
            "id": "abc", "summary": "new summary", "raw_text": None, "keywords": ["x"],
        }
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"summary": "new summary", "keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True, "subject_line": "new summary"}

def test_update_capture_keywords_only_skips_subject_line(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {"id": "abc", "summary": None, "raw_text": None, "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}
    assert "subject_line" not in resp.json()

def test_update_capture_forwards_raw_text(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {
            "id": "abc", "summary": None, "raw_text": "corrected original text", "keywords": None,
        }
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"raw_text": "corrected original text"})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}
    assert "subject_line" not in resp.json()

def test_prompts_crud_roundtrip(client):
    created = client.post("/api/prompts", json={"text": "ai agents this week"}).json()
    assert created["text"] == "ai agents this week"
    listed = client.get("/api/prompts").json()
    assert len(listed) == 1
    resp = client.delete(f"/api/prompts/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/prompts").json() == []

def test_delete_missing_prompt_returns_404(client):
    resp = client.delete("/api/prompts/999")
    assert resp.status_code == 404

def test_mcp_connection_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 502

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

def test_delete_log_respects_limit(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        return _dict_result({"id": "abc", "deleted": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    for capture_id in ["a", "b", "c"]:
        client.post(f"/api/captures/{capture_id}/delete", json={"keywords": []})
    log = client.get("/api/delete-log", params={"limit": 2}).json()
    assert [entry["capture_id"] for entry in log] == ["c", "b"]

def test_get_graph_builds_keyword_and_cluster_data(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            assert arguments == {"n": 100_000}
            return _list_result([
                {"id": "a", "keywords": ["claude"]},
                {"id": "b", "keywords": ["claude", "ai"]},
            ])
        if name == "cluster_captures":
            assert arguments == {"k": None}
            return _dict_result({"k": 1, "clusters": [
                {"cluster_id": 0, "size": 2, "members": [
                    {"id": "a", "summary": "Claude does things", "central": True},
                    {"id": "b", "summary": "Claude and AI", "central": False},
                ]},
            ]})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["clusters"][0]["label"] == "claude"
    keywords_by_name = {k["keyword"]: k for k in body["keywords"]}
    assert keywords_by_name["claude"]["count"] == 2
    assert keywords_by_name["ai"]["count"] == 1

def test_get_graph_returns_not_enough_captures(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []}])
        if name == "cluster_captures":
            return _dict_result({"error": "need at least 4 captures to cluster, have 2"})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    assert resp.json() == {"error": "not_enough_captures", "count": 2}

def test_get_graph_forwards_explicit_k(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []},
                                  {"id": "c", "keywords": []}, {"id": "d", "keywords": []}])
        if name == "cluster_captures":
            assert arguments == {"k": 4}
            return _dict_result({"k": 4, "clusters": [
                {"cluster_id": i, "size": 1, "members": [{"id": cid, "summary": "s", "central": True}]}
                for i, cid in enumerate(["a", "b", "c", "d"])
            ]})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph", params={"k": 4})
    assert resp.status_code == 200
    assert len(resp.json()["clusters"]) == 4

def test_get_graph_rejects_k_above_capture_count(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_recent"
        return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph", params={"k": 5})
    assert resp.status_code == 400

def test_get_graph_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 502

def test_get_recent_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_recent"
        assert arguments == {
            "n": 25, "source": "whatsapp", "date_from": "2026-01-01",
            "date_to": None, "keywords": ["sarah", "job"], "keyword_mode": "or",
        }
        return _list_result([{"id": "a", "summary": "note", "keywords": ["sarah"]}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent", params={
        "source": "whatsapp", "date_from": "2026-01-01",
        "keywords": ["sarah", "job"],
    })
    assert resp.status_code == 200
    assert resp.json()[0]["subject_line"] == "note"

def test_get_recent_defaults_to_no_filters(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "n": 25, "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 200
    assert resp.json() == []

def test_get_recent_error_dict_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # Exercises the _rows_or_400 branch for this route -- list_recent's
        # own keyword_mode check is stricter than nothing, even though the
        # GUI's Literal type already screens the common bad-value case.
        return _list_result({"error": "keyword_mode must be 'and' or 'or', got 'xor'"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 400

def test_get_recent_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 502
