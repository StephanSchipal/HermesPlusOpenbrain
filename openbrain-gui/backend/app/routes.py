# app/routes.py
"""FastAPI routes for the OpenBrain GUI backend. Talks to openbrain-mcp
for capture data (search/stats/keywords/delete/update/graph) and to
gui.db (SQLite, via prompts_store/delete_log_store) for GUI-local
bookkeeping (saved prompts, delete log) -- design spec section 4,
"Architecture"."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import mcp_client, prompts_store, delete_log_store, subject_line, graph
from app.mcp_client import OpenBrainMCPError
from app.config import DEFAULT_SEARCH_K, DEFAULT_DELETE_LOG_LIMIT, GRAPH_MAX_CAPTURES

router = APIRouter(prefix="/api")

class SearchRequest(BaseModel):
    query: str | None = None
    capture_id: str | None = None
    k: int = DEFAULT_SEARCH_K
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    keywords: list[str] | None = None
    keyword_mode: Literal["and", "or"] = "or"

class UpdateRequest(BaseModel):
    summary: str | None = None
    keywords: list[str] | None = None

class PromptRequest(BaseModel):
    text: str

class CaptureSnapshot(BaseModel):
    subject_line: str | None = None
    keywords: list[str] = []
    source_url: str | None = None
    created_at: str | None = None

async def _call(name: str, arguments: dict):
    try:
        return await mcp_client.call_tool(name, arguments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"openbrain-mcp unreachable: {exc}") from exc

@router.get("/stats")
async def get_stats():
    result = await _call("stats", {})
    try:
        return mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.get("/keywords")
async def get_keywords(filter: str = ""):
    result = await _call("list_keywords", {})
    try:
        keywords = mcp_client.parse_list_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if filter:
        needle = filter.lower()
        keywords = [k for k in keywords if needle in k["keyword"].lower()]
    return keywords

def _rows_or_400(parsed: list[dict] | dict) -> list[dict]:
    """search/list_recent can return an {"error": ...} dict (bad capture_id,
    invalid keyword_mode, both/neither of query+capture_id) instead of a row
    list -- surface that as a 400 (caller's problem), distinct from the 502
    reserved for openbrain-mcp being unreachable."""
    if isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=parsed.get("error", "invalid request"))
    return parsed

@router.post("/search")
async def search(body: SearchRequest):
    result = await _call("search", {
        "query": body.query, "capture_id": body.capture_id, "k": body.k,
        "source": body.source, "date_from": body.date_from, "date_to": body.date_to,
        "keywords": body.keywords, "keyword_mode": body.keyword_mode,
    })
    try:
        rows = _rows_or_400(mcp_client.parse_list_result(result))
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    for row in rows:
        row["subject_line"] = subject_line.make_subject_line(row["summary"])
    return rows

@router.post("/captures/{capture_id}/delete")
async def delete_capture(capture_id: str, snapshot: CaptureSnapshot):
    # Snapshot written BEFORE the MCP delete call -- see delete_log_store's
    # module docstring for why this ordering is load-bearing.
    delete_log_store.log_deletion(
        capture_id=capture_id,
        subject_line=snapshot.subject_line,
        keywords=snapshot.keywords,
        source_url=snapshot.source_url,
        captured_at=snapshot.created_at,
    )
    result = await _call("delete", {"id": capture_id})
    try:
        return mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.patch("/captures/{capture_id}")
async def update_capture(capture_id: str, body: UpdateRequest):
    result = await _call("update", {
        "id": capture_id, "summary": body.summary, "keywords": body.keywords,
    })
    try:
        updated = mcp_client.parse_dict_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Only recompute when summary actually changed -- a keywords-only update
    # leaves the previously-displayed subject line accurate (design spec
    # section 6, "Data flow": "Save -> PATCH -> grid row updates in place,
    # subject line recomputed").
    if body.summary is not None:
        updated["subject_line"] = subject_line.make_subject_line(body.summary)
    return updated

@router.get("/prompts")
def get_prompts():
    return prompts_store.list_prompts()

@router.post("/prompts")
def post_prompt(body: PromptRequest):
    return prompts_store.create_prompt(body.text)

@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int):
    deleted = prompts_store.delete_prompt(prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="prompt not found")
    return {"id": prompt_id, "deleted": True}

@router.get("/delete-log")
def get_delete_log(limit: int = DEFAULT_DELETE_LOG_LIMIT):
    return delete_log_store.list_deletions(limit=limit)

@router.get("/graph")
async def get_graph():
    captures_result = await _call("list_recent", {"n": GRAPH_MAX_CAPTURES})
    try:
        captures = mcp_client.parse_list_result(captures_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cluster_result = await _call("cluster_captures", {"k": None})
    try:
        cluster_data = mcp_client.parse_dict_result(cluster_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if "error" in cluster_data:
        return {"error": "not_enough_captures", "count": len(captures)}

    return graph.build_keyword_graph(captures, cluster_data["clusters"])
