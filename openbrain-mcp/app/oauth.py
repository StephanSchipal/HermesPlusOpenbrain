# app/oauth.py
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.config import OPENBRAIN_HOST, OPENBRAIN_TOKEN

_clients: dict[str, dict] = {}

CODE_TTL_SECONDS = 60
_auth_codes: dict[str, dict] = {}

# The only legitimate caller of this OAuth shim is claude.ai; per Anthropic's
# connector docs this callback URL is the same across all hosted Claude
# surfaces (web, Desktop, mobile, Cowork). Pinning /register to it prevents
# an unauthenticated caller from registering their own redirect_uri and
# later phishing a code (and thus OPENBRAIN_TOKEN) via /authorize.
ALLOWED_REDIRECT_URIS = {"https://claude.ai/api/mcp/auth_callback"}


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)


async def well_known_auth_server(_request: Request) -> JSONResponse:
    return JSONResponse({
        "issuer": f"https://{OPENBRAIN_HOST}",
        "authorization_endpoint": f"https://{OPENBRAIN_HOST}/authorize",
        "token_endpoint": f"https://{OPENBRAIN_HOST}/token",
        "registration_endpoint": f"https://{OPENBRAIN_HOST}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


async def well_known_protected_resource(_request: Request) -> JSONResponse:
    return JSONResponse({
        "resource": f"https://{OPENBRAIN_HOST}/mcp",
        "authorization_servers": [f"https://{OPENBRAIN_HOST}"],
    })


async def register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    if not isinstance(redirect_uris, list) or not all(
        isinstance(uri, str) and uri for uri in redirect_uris
    ):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    if not set(redirect_uris) <= ALLOWED_REDIRECT_URIS:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    client_id = secrets.token_urlsafe(24)
    _clients[client_id] = {"redirect_uris": redirect_uris, "created_at": time.time()}
    return JSONResponse({
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    })


async def authorize(request: Request):
    q = request.query_params
    client = _clients.get(q.get("client_id", ""))
    redirect_uri = q.get("redirect_uri", "")
    if not client or redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE S256 required"},
            status_code=400,
        )
    code = secrets.token_urlsafe(24)
    _auth_codes[code] = {
        "client_id": q["client_id"],
        "code_challenge": q["code_challenge"],
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + CODE_TTL_SECONDS,
    }
    params = {"code": code}
    if q.get("state") is not None:
        params["state"] = q["state"]
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)


async def token(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    code = form.get("code")
    client_id = form.get("client_id")
    code_verifier = form.get("code_verifier")
    grant_type = form.get("grant_type")
    redirect_uri = form.get("redirect_uri")

    # Reject anything that isn't a plain string (e.g. an UploadFile from a
    # multipart file field) *before* touching _auth_codes, so a malformed
    # request can never pop and destroy a real, still-valid code.
    if not isinstance(code, str) or not isinstance(client_id, str) or not isinstance(code_verifier, str):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if grant_type != "authorization_code":
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    entry = _auth_codes.pop(code, None)   # pop, not get: makes the code single-use
    if not entry or entry["expires_at"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _verify_pkce(code_verifier, entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse({
        "access_token": OPENBRAIN_TOKEN,
        "token_type": "Bearer",
    })
