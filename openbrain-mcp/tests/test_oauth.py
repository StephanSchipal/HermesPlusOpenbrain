# tests/test_oauth.py
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import app.oauth as oauth_module


def _oauth_test_app() -> Starlette:
    """A standalone app exposing only the oauth.py routes, for testing the
    module in isolation from the full openbrain-mcp app (that integration is
    covered separately in test_server.py)."""
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
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
