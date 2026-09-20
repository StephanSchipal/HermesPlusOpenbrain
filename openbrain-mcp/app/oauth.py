# app/oauth.py
import secrets
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import OPENBRAIN_HOST

_clients: dict[str, dict] = {}


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
    client_id = secrets.token_urlsafe(24)
    _clients[client_id] = {"redirect_uris": redirect_uris, "created_at": time.time()}
    return JSONResponse({
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    })
