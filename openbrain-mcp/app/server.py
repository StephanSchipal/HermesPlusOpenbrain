# app/server.py
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import OPENBRAIN_TOKEN
from app.db import get_conn
from app import store

mcp = FastMCP("openbrain")

@mcp.tool()
def save(raw_text: str, summary: str, keywords: list[str],
         source: str | None = None, source_url: str | None = None,
         lang: str | None = None) -> dict:
    """Store a captured note. The summary is embedded for semantic search.
    Pass the original text as raw_text, a concise summary, and ~5 keywords.
    Idempotent: resending the same link/text returns the existing id (deduped)."""
    with get_conn() as conn:
        return store.save_capture(
            conn, raw_text=raw_text, summary=summary, keywords=keywords,
            source=source, source_url=source_url, lang=lang,
        )

@mcp.tool()
def search(query: str, k: int = 5) -> list[dict]:
    """Semantic search over captured notes. Returns the top-k matches by meaning."""
    with get_conn() as conn:
        return store.search_captures(conn, query=query, k=k)

@mcp.tool()
def list_recent(n: int = 10) -> list[dict]:
    """List the most recently captured notes."""
    with get_conn() as conn:
        return store.fetch_recent(conn, n=n)

@mcp.tool()
def stats() -> dict:
    """Summary statistics: total captures, counts by source, date range."""
    with get_conn() as conn:
        return store.compute_stats(conn)

@mcp.tool()
def delete(id: str) -> dict:
    """Delete a capture by id (prune mis-captures)."""
    with get_conn() as conn:
        return {"id": id, "deleted": store.delete_capture(conn, capture_id=id)}

@mcp.tool()
def update(id: str, summary: str | None = None, keywords: list[str] | None = None) -> dict:
    """Edit a capture: change its summary and/or keywords (re-embeds if summary changes)."""
    with get_conn() as conn:
        return {"id": id, "updated": store.update_capture(
            conn, capture_id=id, summary=summary, keywords=keywords)}

class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        expected = f"Bearer {OPENBRAIN_TOKEN}"
        if not OPENBRAIN_TOKEN or request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})

def build_app() -> Starlette:
    app = mcp.streamable_http_app()           # Starlette app serving MCP at /mcp
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(BearerAuthMiddleware)
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=8080)
