# tests/test_routes.py
"""Route-level tests: FastAPI TestClient with openbrain-mcp mocked at the
module boundary (app.mcp_client) -- no real network calls. Subject-line
generation is pure/deterministic (app.subject_line), so it needs no
mocking. Real SQLite (a tmp_path file) is used for prompts/delete-log,
since that IS this backend's own data."""
import json
import pytest
from fastapi.testclient import TestClient
from mcp import types

import app.db as db_module
import app.mcp_client as mcp_client_module
from app.main import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "GUI_DB_PATH", str(tmp_path / "gui.db"))
    return TestClient(create_app())

def _dict_result(payload: dict) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))])

def _list_result(payload: list) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="unstructured, ignored")],
        structuredContent={"result": payload},
    )

def test_get_stats_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "stats" and arguments == {}
        return _dict_result({"total": 3, "by_source": {"youtube": 3}})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 3, "by_source": {"youtube": 3}}

def test_get_keywords_filters_case_insensitively(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_keywords"
        return _list_result([{"keyword": "AI", "count": 5}, {"keyword": "cooking", "count": 2}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/keywords", params={"filter": "ai"})
    assert resp.status_code == 200
    assert resp.json() == [{"keyword": "AI", "count": 5}]

def test_search_adds_subject_line_per_row(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "search"
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result(
            [{"id": "abc", "summary": "Sarah is considering a pivot", "keywords": ["career"]}]
        )
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"query": "career notes"})
    assert resp.status_code == 200
    assert resp.json() == [{
        "id": "abc", "summary": "Sarah is considering a pivot",
        "keywords": ["career"], "subject_line": "Sarah is considering a pivot",
    }]

def test_search_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "query": "career notes", "capture_id": None, "k": 25,
            "source": "whatsapp", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "keywords": ["sarah"], "keyword_mode": "and",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={
        "query": "career notes", "source": "whatsapp",
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "keywords": ["sarah"], "keyword_mode": "and",
    })
    assert resp.status_code == 200
    assert resp.json() == []

def test_search_by_capture_id(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments["capture_id"] == "abc" and arguments["query"] is None
        return _list_result([{"id": "def", "summary": "a neighbor", "keywords": []}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={"capture_id": "abc"})
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "def"

def test_search_error_dict_from_mcp_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # search's return annotation is a Union -- its error dict still
        # arrives via structuredContent, so _list_result (not _dict_result)
        # is the correct mock here (see the note above about Union handling).
        return _list_result({"error": "exactly one of query or capture_id must be given"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/search", json={})
    assert resp.status_code == 400
    assert "exactly one of" in resp.json()["detail"]

def test_search_rejects_invalid_keyword_mode(client):
    resp = client.post("/api/search", json={"query": "x", "keyword_mode": "xor"})
    assert resp.status_code == 422

def test_delete_capture_logs_before_calling_mcp(client, monkeypatch):
    calls = []
    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        return _dict_result({"id": "abc", "deleted": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.post("/api/captures/abc/delete", json={
        "subject_line": "Sarah's career pivot", "keywords": ["career"],
        "source_url": "https://example.com/post", "created_at": "2026-07-20T14:32:00+00:00",
    })
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "deleted": True}
    assert calls == [("delete", {"id": "abc"})]
    log = client.get("/api/delete-log").json()
    assert len(log) == 1
    assert log[0]["capture_id"] == "abc"
    assert log[0]["keywords"] == ["career"]

def test_update_capture_proxies_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {
            "id": "abc", "summary": "new summary", "raw_text": None, "keywords": ["x"],
        }
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"summary": "new summary", "keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True, "subject_line": "new summary"}

def test_update_capture_keywords_only_skips_subject_line(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {"id": "abc", "summary": None, "raw_text": None, "keywords": ["x"]}
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"keywords": ["x"]})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}
    assert "subject_line" not in resp.json()

def test_update_capture_forwards_raw_text(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "update"
        assert arguments == {
            "id": "abc", "summary": None, "raw_text": "corrected original text", "keywords": None,
        }
        return _dict_result({"id": "abc", "updated": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.patch("/api/captures/abc", json={"raw_text": "corrected original text"})
    assert resp.status_code == 200
    assert resp.json() == {"id": "abc", "updated": True}
    assert "subject_line" not in resp.json()

def test_prompts_crud_roundtrip(client):
    created = client.post("/api/prompts", json={"text": "ai agents this week"}).json()
    assert created["text"] == "ai agents this week"
    listed = client.get("/api/prompts").json()
    assert len(listed) == 1
    resp = client.delete(f"/api/prompts/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/prompts").json() == []

def test_delete_missing_prompt_returns_404(client):
    resp = client.delete("/api/prompts/999")
    assert resp.status_code == 404

def test_mcp_connection_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/stats")
    assert resp.status_code == 502

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

def test_delete_log_respects_limit(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        return _dict_result({"id": "abc", "deleted": True})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    for capture_id in ["a", "b", "c"]:
        client.post(f"/api/captures/{capture_id}/delete", json={"keywords": []})
    log = client.get("/api/delete-log", params={"limit": 2}).json()
    assert [entry["capture_id"] for entry in log] == ["c", "b"]

def test_get_graph_builds_keyword_and_cluster_data(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            assert arguments == {"n": 100_000}
            return _list_result([
                {"id": "a", "keywords": ["claude"]},
                {"id": "b", "keywords": ["claude", "ai"]},
            ])
        if name == "cluster_captures":
            assert arguments == {"k": None}
            return _dict_result({"k": 1, "clusters": [
                {"cluster_id": 0, "size": 2, "members": [
                    {"id": "a", "summary": "Claude does things", "central": True},
                    {"id": "b", "summary": "Claude and AI", "central": False},
                ]},
            ]})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["clusters"][0]["label"] == "claude"
    keywords_by_name = {k["keyword"]: k for k in body["keywords"]}
    assert keywords_by_name["claude"]["count"] == 2
    assert keywords_by_name["ai"]["count"] == 1

def test_get_graph_returns_not_enough_captures(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []}])
        if name == "cluster_captures":
            return _dict_result({"error": "need at least 4 captures to cluster, have 2"})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    assert resp.json() == {"error": "not_enough_captures", "count": 2}

def test_get_graph_forwards_explicit_k(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []},
                                  {"id": "c", "keywords": []}, {"id": "d", "keywords": []}])
        if name == "cluster_captures":
            assert arguments == {"k": 4}
            return _dict_result({"k": 4, "clusters": [
                {"cluster_id": i, "size": 1, "members": [{"id": cid, "summary": "s", "central": True}]}
                for i, cid in enumerate(["a", "b", "c", "d"])
            ]})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph", params={"k": 4})
    assert resp.status_code == 200
    assert len(resp.json()["clusters"]) == 4

def test_get_graph_rejects_k_above_capture_count(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_recent"
        return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph", params={"k": 5})
    assert resp.status_code == 400

def test_get_graph_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 502

def test_get_recent_passes_filters_to_mcp_tool(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert name == "list_recent"
        assert arguments == {
            "n": 25, "source": "whatsapp", "date_from": "2026-01-01",
            "date_to": None, "keywords": ["sarah", "job"], "keyword_mode": "or",
        }
        return _list_result([{"id": "a", "summary": "note", "keywords": ["sarah"]}])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent", params={
        "source": "whatsapp", "date_from": "2026-01-01",
        "keywords": ["sarah", "job"],
    })
    assert resp.status_code == 200
    assert resp.json()[0]["subject_line"] == "note"

def test_get_recent_defaults_to_no_filters(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        assert arguments == {
            "n": 25, "source": None, "date_from": None, "date_to": None,
            "keywords": None, "keyword_mode": "or",
        }
        return _list_result([])
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 200
    assert resp.json() == []

def test_get_recent_error_dict_becomes_400(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        # Exercises the _rows_or_400 branch for this route -- list_recent's
        # own keyword_mode check is stricter than nothing, even though the
        # GUI's Literal type already screens the common bad-value case.
        return _list_result({"error": "keyword_mode must be 'and' or 'or', got 'xor'"})
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 400

def test_get_recent_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/recent")
    assert resp.status_code == 502

def test_external_costs_round_trip(client):
    resp = client.put("/api/cost/external", json={"rows": [
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": "https://hpanel.hostinger.com",
         "comments": "KVM2", "compare_to_estimate": False, "sort_order": 0},
    ]})
    assert resp.status_code == 200
    listed = client.get("/api/cost/external").json()
    assert len(listed["rows"]) == 1
    assert listed["rows"][0]["name"] == "Hostinger"
    assert listed["totals"]["onetime_usd"] == 0.0
    assert listed["totals"]["incomplete"] is False


def test_external_totals_report_incomplete_without_a_rate(client):
    """A EUR row contributes 0 to the USD total until a rate exists. The API
    must say so -- see external_costs_store.totals."""
    client.put("/api/cost/external", json={"rows": [
        {"name": "Euro thing", "period": "monthly", "amount": 10.0,
         "entered_currency": "EUR", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    totals = client.get("/api/cost/external").json()["totals"]
    assert totals["incomplete"] is True
    assert totals["monthly_usd"] == pytest.approx(0.0)


def test_external_costs_reject_bad_url(client):
    resp = client.put("/api/cost/external", json={"rows": [
        {"name": "bad", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
         "url": "javascript:alert(1)", "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"]


def test_delete_external_cost_row(client):
    client.put("/api/cost/external", json={"rows": [
        {"name": "Gone", "period": "none", "amount": None, "entered_currency": "USD",
         "url": None, "comments": None, "compare_to_estimate": False, "sort_order": 0},
    ]})
    row_id = client.get("/api/cost/external").json()["rows"][0]["id"]
    assert client.delete(f"/api/cost/external/{row_id}").status_code == 200
    assert client.get("/api/cost/external").json()["rows"] == []
    assert client.delete(f"/api/cost/external/{row_id}").status_code == 404


def test_fx_manual_override_then_read(client):
    assert client.get("/api/cost/fx").json()["rate"] is None
    resp = client.put("/api/cost/fx", json={"usd_to_eur": 0.8607})
    assert resp.status_code == 200
    assert client.get("/api/cost/fx").json()["rate"]["usd_to_eur"] == 0.8607


def test_fx_refresh_failure_returns_503_and_keeps_rate(client, monkeypatch):
    import app.fx as fx_module
    client.put("/api/cost/fx", json={"usd_to_eur": 0.90})

    def boom(*, path=None):
        raise fx_module.FxUnavailable("no network")

    monkeypatch.setattr(fx_module, "refresh_rate", boom)
    assert client.post("/api/cost/fx/refresh").status_code == 503
    assert client.get("/api/cost/fx").json()["rate"]["usd_to_eur"] == 0.90


def test_external_totals_use_current_rate(client):
    client.put("/api/cost/fx", json={"usd_to_eur": 0.80})
    client.put("/api/cost/external", json={"rows": [
        {"name": "Yearly thing", "period": "yearly", "amount": 120.0,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    totals = client.get("/api/cost/external").json()["totals"]
    assert totals["monthly_usd"] == pytest.approx(10.0)
    assert totals["monthly_eur"] == pytest.approx(8.0)


def test_compare_flag_round_trips_as_a_boolean(client):
    client.put("/api/cost/external", json={"rows": [
        {"name": "Anthropic", "period": "monthly", "amount": 94.17,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": True, "sort_order": 0},
    ]})
    row = client.get("/api/cost/external").json()["rows"][0]
    assert row["compare_to_estimate"] is True


def test_put_handles_a_batch_of_new_and_existing_rows(client):
    """What the Save button actually sends: the whole visible grid, mixing
    already-persisted rows with freshly typed ones."""
    client.put("/api/cost/external", json={"rows": [
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    existing = client.get("/api/cost/external").json()["rows"][0]

    client.put("/api/cost/external", json={"rows": [
        {**existing, "amount": 14.99},
        {"name": "Anthropic", "period": "monthly", "amount": 94.17,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 1},
    ]})

    rows = {r["name"]: r for r in client.get("/api/cost/external").json()["rows"]}
    assert len(rows) == 2
    assert rows["Hostinger"]["id"] == existing["id"]   # updated in place, not duplicated
    assert rows["Hostinger"]["amount"] == 14.99
    assert rows["Anthropic"]["amount"] == 94.17


HERMES_ENDPOINTS = [
    "/api/cost/summary", "/api/cost/dashboard", "/api/cost/config",
]


@pytest.mark.parametrize("endpoint", HERMES_ENDPOINTS)
def test_hermes_endpoints_return_503_when_data_dir_absent(client, monkeypatch, tmp_path, endpoint):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    resp = client.get(endpoint)
    assert resp.status_code == 503
    assert "not found" in resp.json()["detail"]


def test_part2_still_works_when_hermes_data_absent(client, monkeypatch, tmp_path):
    """The whole point of the 503 design: the external cost grid must not
    break because the VPS mount is missing."""
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    assert client.get("/api/cost/external").status_code == 200
    assert client.get("/api/cost/fx").status_code == 200


def _fake_summary(**over):
    base = {"sessions": 10, "api_calls": 712, "input_tokens": 1_000,
            "output_tokens": 500, "cache_read_tokens": 69_000_000,
            "cache_write_tokens": 8_700_000, "reasoning_tokens": 0,
            "cost_usd": 94.17, "cache_hit_rate": 0.895, "cost_status": "estimated"}
    base.update(over)
    return base


def test_summary_combines_hermes_and_external_costs(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "summary",
                        lambda data_dir=None, **kw: _fake_summary())
    client.put("/api/cost/fx", json={"usd_to_eur": 0.80})
    client.put("/api/cost/external", json={"rows": [
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    body = client.get("/api/cost/summary?days=30").json()
    assert body["hermes"]["cost_usd"] == pytest.approx(94.17)
    assert body["external"]["monthly_usd"] == pytest.approx(12.99)
    assert body["total_cost_of_ownership_usd"] == pytest.approx(107.16)
    assert body["total_cost_of_ownership_eur"] == pytest.approx(85.728)
    assert body["total_cost_of_ownership_incomplete"] is False


def test_summary_marks_the_total_incomplete_when_a_euro_row_lacks_a_rate(client, monkeypatch):
    """A EUR row contributes 0 to the USD total until a rate exists, so the
    combined total is understated too -- it must say so."""
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "summary", lambda data_dir=None, **kw: _fake_summary())
    client.put("/api/cost/external", json={"rows": [
        {"name": "Euro thing", "period": "monthly", "amount": 10.0,
         "entered_currency": "EUR", "url": None, "comments": None,
         "compare_to_estimate": False, "sort_order": 0},
    ]})
    body = client.get("/api/cost/summary?days=30").json()
    assert body["total_cost_of_ownership_incomplete"] is True


def test_summary_reports_estimate_vs_actual_when_row_flagged(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "summary", lambda data_dir=None, **kw: _fake_summary())
    client.put("/api/cost/external", json={"rows": [
        {"name": "Anthropic", "period": "monthly", "amount": 91.40,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": True, "sort_order": 0},
    ]})
    comparison = client.get("/api/cost/summary?days=7").json()["estimate_vs_actual"]
    # Always the 30-day estimate regardless of the selected range.
    assert comparison["estimated_usd"] == pytest.approx(94.17)
    assert comparison["actual_usd"] == pytest.approx(91.40)
    assert comparison["delta_pct"] == pytest.approx((91.40 - 94.17) / 94.17 * 100)


def test_dashboard_endpoint_returns_all_panels(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "dashboard", lambda data_dir=None, **kw: {
        "summary": _fake_summary(), "by_model": [], "by_platform": [],
        "by_session": [], "efficiency": [], "top_tools": {"tools": [], "token_attribution_available": False},
        "prompt_budget": [],
    })
    body = client.get("/api/cost/dashboard?days=30").json()
    assert body["summary"]["cost_usd"] == pytest.approx(94.17)
    assert body["top_tools"]["token_attribution_available"] is False


def test_session_detail_404_for_unknown_id(client, monkeypatch):
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "session_detail", lambda sid, *, data_dir=None: None)
    assert client.get("/api/cost/session/nope").status_code == 404


def _ledger_row(**over):
    row = {"session_id": "s1", "model": "claude-sonnet-5", "task": "",
           "platform": "cli", "api_call_count": 1, "input_tokens": 1,
           "output_tokens": 1, "cache_read_tokens": 1, "cache_write_tokens": 1,
           "reasoning_tokens": 0, "estimated_cost_usd": 1.0}
    row.update(over)
    return row


def test_timeseries_endpoint_returns_points_and_collecting_since(client):
    from app import ledger_store
    ledger_store.apply_tick([_ledger_row()], observed_at="2026-08-01T00:00:00+00:00")
    ledger_store.apply_tick([_ledger_row(api_call_count=5, estimated_cost_usd=3.0)],
                            observed_at="2026-08-01T06:00:00+00:00")
    body = client.get("/api/cost/timeseries?days=3650&group=model").json()
    assert body["collecting_since"] == "2026-08-01"
    assert body["points"][0]["group"] == "claude-sonnet-5"
    assert body["points"][0]["cost_usd"] == pytest.approx(2.0)


def test_timeseries_rejects_bad_group(client):
    resp = client.get("/api/cost/timeseries?group=banana")
    assert resp.status_code == 400
    assert "group must be" in resp.json()["detail"]


def test_timeseries_works_without_hermes_data(client, monkeypatch, tmp_path):
    """The ledger lives in gui.db, so this endpoint never needs the mount --
    the chart keeps working even when every other Hermes panel is 503."""
    import app.hermes_usage as hu
    monkeypatch.setattr(hu, "HERMES_DATA_DIR", str(tmp_path / "not-mounted"))
    assert client.get("/api/cost/dashboard").status_code == 503
    assert client.get("/api/cost/timeseries?days=30&group=model").status_code == 200
