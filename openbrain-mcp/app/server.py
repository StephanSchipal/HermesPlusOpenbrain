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
from app.fingerprint import content_fingerprint_debug

# host="0.0.0.0" (not the FastMCP default "127.0.0.1") disables the MCP SDK's
# DNS-rebinding host-header check, which otherwise 421s any request whose Host
# header isn't a localhost variant -- i.e. every real request once deployed
# (Hermes calls openbrain-mcp:8080, Traefik forwards the public hostname).
# The bearer-token middleware below is this server's actual security boundary.
mcp = FastMCP("openbrain", host="0.0.0.0")

@mcp.tool()
def save(raw_text: str, summary: str, keywords: list[str],
         source: str | None = None, source_url: str | None = None,
         lang: str | None = None, metadata: dict | None = None) -> dict:
    """Store a captured note. The summary is embedded for semantic search.
    Pass the original text as raw_text, a concise summary, and ~5 keywords.
    Idempotent: resending the same link/text returns the existing id (deduped)."""
    with get_conn() as conn:
        return store.save_capture(
            conn, raw_text=raw_text, summary=summary, keywords=keywords,
            source=source, source_url=source_url, lang=lang, metadata=metadata,
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
def update(id: str, summary: str | None = None, keywords: list[str] | None = None,
           metadata: dict | None = None) -> dict:
    """Edit a capture: change its summary and/or keywords (re-embeds if summary changes)."""
    with get_conn() as conn:
        return {"id": id, "updated": store.update_capture(
            conn, capture_id=id, summary=summary, keywords=keywords, metadata=metadata)}

@mcp.tool()
def find_near_duplicates(threshold: float = 0.95, limit: int = 50) -> list[dict]:
    """Find near-duplicate capture pairs by embedding similarity (cosine).
    Read-only -- use the existing `delete` tool to remove one side of a pair."""
    with get_conn() as conn:
        return store.find_near_duplicates(conn, threshold=threshold, limit=limit)

@mcp.tool()
def compute_fingerprint(raw_text: str, source_url: str | None = None) -> dict:
    """Show the dedup fingerprint `save` would compute for this input, and the
    normalized string it's based on. Read-only, no DB access -- does not check
    whether this fingerprint already exists (use `save` or `find_near_duplicates`
    for that)."""
    # No get_conn()/store.py here (unlike the other tools above) -- this
    # computation is pure and needs no DB access.
    return content_fingerprint_debug(source_url=source_url, raw_text=raw_text)

@mcp.tool()
def cluster_captures(k: int | None = None) -> dict:
    """Group all captures into thematic clusters by embedding similarity.
    Read-only. If k is omitted, the number of clusters is chosen
    automatically via silhouette score. Returns each cluster's full
    membership (id + summary), with a `central` flag marking the 3 entries
    closest to that cluster's centroid -- use those to label the cluster's
    theme, since this tool does not generate labels itself."""
    with get_conn() as conn:
        return store.cluster_captures(conn, k=k)

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
