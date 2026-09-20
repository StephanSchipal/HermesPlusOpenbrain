# tests/test_oauth.py
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import app.oauth as oauth_module


@pytest.fixture(autouse=True)
def _clear_oauth_state():
    oauth_module._clients.clear()
    yield
    oauth_module._clients.clear()


def _oauth_test_app() -> Starlette:
    """A standalone app exposing only the oauth.py routes, for testing the
    module in isolation from the full openbrain-mcp app (that integration is
    covered separately in test_server.py)."""
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
        Route("/register", oauth_module.register, methods=["POST"]),
    ])


def _client(monkeypatch, host: str = "brain.test.example") -> TestClient:
    monkeypatch.setattr(oauth_module, "OPENBRAIN_HOST", host)
    return TestClient(_oauth_test_app())


def test_well_known_authorization_server_metadata_shape(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "https://brain.test.example"
    assert body["authorization_endpoint"] == "https://brain.test.example/authorize"
    assert body["token_endpoint"] == "https://brain.test.example/token"
    assert body["registration_endpoint"] == "https://brain.test.example/register"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


def test_well_known_protected_resource_metadata_shape(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == "https://brain.test.example/mcp"
    assert body["authorization_servers"] == ["https://brain.test.example"]


def test_register_returns_client_id_for_valid_metadata(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "client_id" in body and body["client_id"]
    assert body["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert body["token_endpoint_auth_method"] == "none"
    # the registered client_id must actually be usable later
    assert body["client_id"] in oauth_module._clients


def test_register_rejects_missing_redirect_uris(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"
