# app/oauth.py
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import OPENBRAIN_HOST


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
