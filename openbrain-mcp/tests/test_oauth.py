# tests/test_oauth.py
import base64
import hashlib
import secrets

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import app.oauth as oauth_module


@pytest.fixture(autouse=True)
def _clear_oauth_state():
    oauth_module._clients.clear()
    oauth_module._auth_codes.clear()
    yield
    oauth_module._clients.clear()
    oauth_module._auth_codes.clear()


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
        Route("/authorize", oauth_module.authorize, methods=["GET"]),
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


def test_register_rejects_malformed_json_body(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/register",
        content="not-json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


def test_register_rejects_redirect_uris_as_bare_string(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={
        "redirect_uris": "https://claude.ai/api/mcp/auth_callback",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


@pytest.mark.parametrize("payload", [b"[]", b'"hello"', b"42", b"null"])
def test_register_rejects_non_object_json_body(monkeypatch, payload):
    client = _client(monkeypatch)
    resp = client.post("/register", content=payload, headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


def _pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for a valid S256 PKCE pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _register_client(client: TestClient, redirect_uri: str) -> str:
    resp = client.post("/register", json={"redirect_uris": [redirect_uri]})
    return resp.json()["client_id"]


REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def test_authorize_issues_code_and_redirects(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    _verifier, challenge = _pkce_pair()

    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    }, follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{REDIRECT_URI}?")
    assert "code=" in location
    assert "state=xyz" in location


def test_authorize_rejects_unknown_client_id(monkeypatch):
    client = _client(monkeypatch)
    _verifier, challenge = _pkce_pair()
    resp = client.get("/authorize", params={
        "client_id": "does-not-exist",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_authorize_rejects_unregistered_redirect_uri(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    _verifier, challenge = _pkce_pair()
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": "https://attacker.example/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_authorize_rejects_missing_pkce(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"
