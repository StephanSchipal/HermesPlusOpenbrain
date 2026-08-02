# app/routes.py
"""FastAPI routes for the OpenBrain GUI backend. Talks to openbrain-mcp
for capture data (search/stats/keywords/delete/update/graph) and to
gui.db (SQLite, via prompts_store/delete_log_store) for GUI-local
bookkeeping (saved prompts, delete log) -- design spec section 4,
"Architecture"."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import mcp_client, prompts_store, delete_log_store, subject_line, graph
from app import external_costs_store, fx, hermes_usage, ledger_store
from app.mcp_client import OpenBrainMCPError
from app.hermes_usage import HermesDataUnavailable
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
    raw_text: str | None = None
    keywords: list[str] | None = None

class PromptRequest(BaseModel):
    text: str

class CaptureSnapshot(BaseModel):
    subject_line: str | None = None
    keywords: list[str] = []
    source_url: str | None = None
    created_at: str | None = None

class ExternalCostRow(BaseModel):
    id: int | None = None
    name: str = ""
    period: Literal["yearly", "monthly", "onetime", "none"] = "monthly"
    amount: float | None = None
    entered_currency: Literal["USD", "EUR"] = "USD"
    url: str | None = None
    comments: str | None = None
    compare_to_estimate: bool = False
    sort_order: int = 0

class ExternalCostSaveRequest(BaseModel):
    rows: list[ExternalCostRow]

class FxRateRequest(BaseModel):
    usd_to_eur: float

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
        parsed = mcp_client.parse_list_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = _rows_or_400(parsed)
    for row in rows:
        row["subject_line"] = subject_line.make_subject_line(row["summary"])
    return rows

@router.get("/recent")
async def get_recent(
    # Filter-only browsing (source/date/keyword, no search text) via
    # openbrain-mcp's list_recent -- /api/search requires exactly one of
    # query/capture_id and errors when given neither, so it can't serve this.
    n: int = DEFAULT_SEARCH_K,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    keywords: list[str] | None = Query(default=None),
    keyword_mode: Literal["and", "or"] = "or",
    ids: list[str] | None = Query(default=None),
):
    result = await _call("list_recent", {
        "n": n, "source": source, "date_from": date_from, "date_to": date_to,
        "keywords": keywords, "keyword_mode": keyword_mode, "ids": ids,
    })
    try:
        parsed = mcp_client.parse_list_result(result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = _rows_or_400(parsed)
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
        "id": capture_id, "summary": body.summary, "raw_text": body.raw_text,
        "keywords": body.keywords,
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
async def get_graph(k: int | None = Query(default=None, ge=1)):
    captures_result = await _call("list_recent", {"n": GRAPH_MAX_CAPTURES})
    try:
        captures = mcp_client.parse_list_result(captures_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if k is not None and k > len(captures):
        raise HTTPException(status_code=400, detail=f"k must be between 1 and {len(captures)}, got {k}")

    cluster_result = await _call("cluster_captures", {"k": k})
    try:
        cluster_data = mcp_client.parse_dict_result(cluster_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if "error" in cluster_data:
        return {"error": "not_enough_captures", "count": len(captures)}

    return graph.build_keyword_graph(captures, cluster_data["clusters"])

@router.get("/cost/external")
def get_external_costs():
    rate_row = fx.get_rate()
    rate = rate_row["usd_to_eur"] if rate_row else None
    rows = external_costs_store.list_rows()
    for row in rows:
        row.update(external_costs_store.amounts(row, rate))
        # SQLite has no boolean type; return the same shape the request declares.
        row["compare_to_estimate"] = bool(row["compare_to_estimate"])
    return {
        "rows": rows,
        "totals": external_costs_store.totals(rows, rate),
        "rate": rate_row,
    }

@router.put("/cost/external")
def put_external_costs(body: ExternalCostSaveRequest):
    # Upsert only -- deliberately does NOT delete rows missing from the payload.
    # The Save button always PUTs the whole visible grid, and removing a row is a
    # separate DELETE driven by the radio selection. Making this full-replace
    # would silently drop any row not currently rendered.
    payload = [r.model_dump() for r in body.rows]
    try:
        external_costs_store.save_rows(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_external_costs()

@router.delete("/cost/external/{row_id}")
def delete_external_cost(row_id: int):
    if not external_costs_store.delete_row(row_id):
        raise HTTPException(status_code=404, detail="row not found")
    return {"id": row_id, "deleted": True}

@router.get("/cost/fx")
def get_fx():
    return {"rate": fx.get_rate()}

@router.post("/cost/fx/refresh")
def post_fx_refresh():
    try:
        return {"rate": fx.refresh_rate()}
    except fx.FxUnavailable as exc:
        # 503, not 500: the cached rate is still valid and the UI keeps working.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.put("/cost/fx")
def put_fx(body: FxRateRequest):
    try:
        return {"rate": fx.set_manual_rate(body.usd_to_eur)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

def _hermes(fn, *args, **kwargs):
    """Every state.db-backed endpoint degrades to 503 rather than 500 when the
    mount is missing or a snapshot copy is unreadable -- the external cost grid
    must stay usable regardless."""
    try:
        return fn(*args, **kwargs)
    except HermesDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.get("/cost/dashboard")
def get_cost_dashboard(days: int = 30, limit: int = 50):
    return _hermes(hermes_usage.dashboard, days=days, limit=limit)

@router.get("/cost/session/{session_id}")
def get_cost_session(session_id: str):
    detail = _hermes(hermes_usage.session_detail, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return detail

@router.get("/cost/config")
def get_cost_config():
    return _hermes(hermes_usage.config_snapshot)

@router.get("/cost/timeseries")
def get_cost_timeseries(days: int = 30, group: str = "model"):
    # Reads gui.db, not state.db -- deliberately no _hermes() wrapper, so the
    # chart survives the mount being absent. `group` is annotated `str` rather
    # than Literal so a bad value gives a 400 with a plain `detail`, matching
    # every other /api/cost/* validation failure instead of FastAPI's 422.
    try:
        return ledger_store.timeseries(days=days, group=group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/cost/summary")
def get_cost_summary(days: int = 30):
    hermes = _hermes(hermes_usage.summary, days=days)
    rate_row = fx.get_rate()
    rate = rate_row["usd_to_eur"] if rate_row else None
    rows = external_costs_store.list_rows()
    external = external_costs_store.totals(rows, rate)

    total_usd = hermes["cost_usd"] + external["monthly_usd"]
    # A EUR row with no rate contributes 0 to external["monthly_usd"], so the
    # combined figure is understated too -- carry the flag rather than let the
    # UI print a confident wrong number.
    total_incomplete = external["incomplete"]

    comparison = None
    flagged = external_costs_store.flagged_row()
    if flagged:
        # An invoice covers a billing month, so this always compares against
        # the 30-day figure regardless of the selected range. When 30 days is
        # already what was asked for -- the default, and so the common case --
        # reuse it rather than copying state.db a second time.
        baseline = (hermes["cost_usd"] if days == 30
                    else _hermes(hermes_usage.summary, days=30)["cost_usd"])
        actual = external_costs_store.amounts(flagged, rate)["usd"]
        if actual is not None and baseline:
            comparison = {
                "name": flagged["name"],
                "estimated_usd": baseline,
                "actual_usd": actual,
                "delta_pct": (actual - baseline) / baseline * 100,
            }

    return {
        "days": days,
        "hermes": hermes,
        "external": external,
        "rate": rate_row,
        "total_cost_of_ownership_usd": total_usd,
        "total_cost_of_ownership_eur": total_usd * rate if rate else None,
        "total_cost_of_ownership_incomplete": total_incomplete,
        "estimate_vs_actual": comparison,
    }
