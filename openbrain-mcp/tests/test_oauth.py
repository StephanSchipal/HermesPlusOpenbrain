# tests/test_oauth.py
import base64
import hashlib
import secrets
import time

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
        Route("/token", oauth_module.token, methods=["POST"]),
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


def test_register_rejects_redirect_uri_outside_allowlist(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={
        "redirect_uris": ["https://attacker.example/callback"],
    })
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


from urllib.parse import parse_qs, urlsplit


def _get_auth_code(client: TestClient, client_id: str, code_verifier_challenge: tuple[str, str]) -> str:
    _verifier, challenge = code_verifier_challenge
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    location = resp.headers["location"]
    query = parse_qs(urlsplit(location).query)
    return query["code"][0]


def test_full_authorize_token_flow_with_valid_pkce(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "the-real-secret-token"
    assert body["token_type"] == "Bearer"
    assert "expires_in" not in body
    assert "refresh_token" not in body


def test_token_rejects_expired_code(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))
    oauth_module._auth_codes[code]["expires_at"] = time.time() - 1  # force expiry

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_replayed_code(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))
    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }

    first = client.post("/token", data=token_request)
    second = client.post("/token", data=token_request)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_rejects_wrong_code_verifier(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": "wrong-verifier",
        "redirect_uri": REDIRECT_URI,
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_client_id_mismatch(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    other_client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": other_client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_malformed_multipart_body(monkeypatch):
    client = _client(monkeypatch)
    # Content-Type declares boundary "AAA" but the body actually uses "BBB" -
    # a mismatched/bad boundary that the multipart parser cannot parse.
    body = (
        b"--BBB\r\n"
        b'Content-Disposition: form-data; name="code"\r\n\r\n'
        b"abc123\r\n"
        b"--BBB--\r\n"
    )
    resp = client.post(
        "/token",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=AAA"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_code_verifier_as_file_without_consuming_code(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    # code_verifier submitted as a file upload, not a plain text field -
    # form.get("code_verifier") would return an UploadFile, not a string.
    malformed = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
        },
        files={"code_verifier": ("f.txt", b"some-bytes")},
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"] == "invalid_grant"

    # The malformed request must NOT have consumed the code: a second,
    # well-formed request reusing the same code must still succeed.
    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "the-real-secret-token"
    assert body["token_type"] == "Bearer"


def test_token_rejects_missing_or_wrong_grant_type(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()

    code_missing = _get_auth_code(client, client_id, (verifier, challenge))
    resp_missing = client.post("/token", data={
        "code": code_missing,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp_missing.status_code == 400
    assert resp_missing.json()["error"] == "invalid_grant"

    code_wrong = _get_auth_code(client, client_id, (verifier, challenge))
    resp_wrong = client.post("/token", data={
        "grant_type": "client_credentials",
        "code": code_wrong,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp_wrong.status_code == 400
    assert resp_wrong.json()["error"] == "invalid_grant"

    # The wrong-grant_type attempt above must not have consumed the code --
    # a follow-up well-formed request with the same code should still work.
    resp_retry = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code_wrong,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp_retry.status_code == 200


def test_token_rejects_mismatched_redirect_uri(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback_wrong",
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"
