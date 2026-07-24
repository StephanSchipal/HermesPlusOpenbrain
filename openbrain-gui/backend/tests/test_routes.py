# tests/test_routes.py
"""Route-level tests: FastAPI TestClient with openbrain-mcp and the
subject-line generator mocked at the module boundary (app.mcp_client /
app.subject_line) -- no real network calls. Real SQLite (a tmp_path file)
is used for prompts/delete-log, since that IS this backend's own data."""
import json
import pytest
from fastapi.testclient import TestClient
from mcp import types

import app.db as db_module
import app.mcp_client as mcp_client_module
import app.subject_line as subject_line_module
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
    async def fake_generate_subject_line(summary):
        assert summary == "Sarah is considering a pivot"
        return "Sarah's career pivot"
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    monkeypatch.setattr(subject_line_module, "generate_subject_line", fake_generate_subject_line)
    resp = client.post("/api/search", json={"query": "career notes"})
    assert resp.status_code == 200
    assert resp.json() == [{
        "id": "abc", "summary": "Sarah is considering a pivot",
        "keywords": ["career"], "subject_line": "Sarah's career pivot",
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
    async def fake_generate_subject_line(summary):
        assert summary == "new summary"
        return "New subject line"
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    monkeypatch.setattr(subject_line_module, "generate_subject_line", fake_generate_subject_line)
    resp = client.patch("/api/captures/abc", json={"summary": "new summary", "keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True, "subject_line": "New subject line"}

def test_update_capture_keywords_only_skips_subject_line(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {"id": "abc", "summary": None, "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    calls = []
    async def fake_generate_subject_line(summary):
        calls.append(summary)
        return "should not be called"
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    monkeypatch.setattr(subject_line_module, "generate_subject_line", fake_generate_subject_line)
    resp = client.patch("/api/captures/abc", json={"keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}
    assert "subject_line" not in resp.json()
    assert calls == []

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
