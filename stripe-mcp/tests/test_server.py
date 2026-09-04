# tests/test_server.py
import asyncio

import pytest
from starlette.testclient import TestClient

import app.config as config_module
import app.server as server_module


def _client(monkeypatch, token: str = "testtoken") -> TestClient:
    monkeypatch.setattr(config_module, "STRIPE_MCP_TOKEN", token)
    return TestClient(server_module.build_app())


def test_health_requires_no_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["read_only"] is True


def test_mcp_requires_auth(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/mcp").status_code == 401


def test_mcp_accepts_non_localhost_host_headers(monkeypatch):
    with _client(monkeypatch) as client:
        for host in ("stripe-mcp:8080", "srv1608402.hstgr.cloud"):
            resp = client.get("/mcp", headers={"Authorization": "Bearer testtoken", "Host": host})
            assert resp.status_code != 421, f"Host {host!r} rejected by DNS-rebinding check"


# --- the surface must stay read-only ------------------------------------
_WRITE_HINTS = ("create", "refund", "cancel", "delete", "update", "issue",
                "checkout", "invoice", "pause", "resume", "modify", "pay")


def test_registered_tools_are_read_only(monkeypatch):
    monkeypatch.setattr(config_module, "STRIPE_MCP_TOKEN", "x")
    tools = asyncio.run(server_module.mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names, "no tools registered"
    for n in names:
        assert not any(h in n.lower() for h in _WRITE_HINTS), f"write-looking tool exposed: {n}"
    # spot-check the expected read tools are present
    for expected in ("list_customers", "list_charges", "get_balance", "revenue_analytics"):
        assert expected in names


def test_mock_mode_tool_call_shapes():
    # is_mock_mode() true by default (no real key) -> sample data, no network.
    assert config_module.is_mock_mode() is True
    from app import stripe_read

    cust = stripe_read.list_customers()
    assert cust["success"] is True and cust["mode"] == "MOCK" and cust["count"] == 2

    vat = stripe_read.austrian_vat(100.0)
    assert vat["vat_amount"] == 20.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
