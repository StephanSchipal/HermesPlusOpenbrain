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
        assert name == "search" and arguments == {"query": "career notes", "k": 25}
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
        assert arguments == {"id": "abc", "summary": "new summary", "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"summary": "new summary", "keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True, "subject_line": "new summary"}

def test_update_capture_keywords_only_skips_subject_line(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {"id": "abc", "summary": None, "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"keywords": ["x"]})
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
