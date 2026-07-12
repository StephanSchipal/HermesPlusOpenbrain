# tests/test_server.py
from starlette.testclient import TestClient
import app.server as server_module

def _client(monkeypatch, token: str = "testtoken") -> TestClient:
    monkeypatch.setattr(server_module, "OPENBRAIN_TOKEN", token)
    return TestClient(server_module.build_app())

def test_health_requires_no_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

def test_mcp_requires_auth(monkeypatch):
    # No `with` here: the request is rejected by BearerAuthMiddleware before
    # it ever reaches the MCP route, so the ASGI lifespan need not run.
    client = _client(monkeypatch)
    resp = client.get("/mcp")
    assert resp.status_code == 401

def test_mcp_accepts_non_localhost_host_headers(monkeypatch):
    # Used as a context manager so the ASGI lifespan runs, initializing the
    # MCP session manager's task group -- required once a request has valid
    # auth and actually reaches the streamable-http route. The underlying
    # FastMCP session manager is a singleton (cached on the module-level
    # `mcp` object) whose run() can only be entered once per process, so
    # only this test -- the one that needs it -- uses `with`.
    with _client(monkeypatch) as client:
        for host in ("brain.srv1608402.hstgr.cloud", "openbrain-mcp:8080"):
            resp = client.get("/mcp", headers={
                "Authorization": "Bearer testtoken",
                "Host": host,
            })
            assert resp.status_code != 421, f"Host header {host!r} was rejected by DNS-rebinding check"
