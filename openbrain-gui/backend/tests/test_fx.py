# tests/test_fx.py
import httpx
import pytest
from app.db import init_db
from app import fx


def test_get_rate_returns_none_when_never_fetched(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    assert fx.get_rate(path=db_path) is None


def test_set_manual_rate_then_get(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.8607, path=db_path)
    rate = fx.get_rate(path=db_path)
    assert rate["usd_to_eur"] == 0.8607
    assert rate["source"] == "manual"
    assert rate["fetched_at"]


def test_set_manual_rate_overwrites_single_row(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.90, path=db_path)
    fx.set_manual_rate(0.85, path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.85


def test_refresh_rate_stores_frankfurter_response(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)

    def fake_get(url, timeout):
        assert "USD" in url and "EUR" in url
        return httpx.Response(200, json={"rates": {"EUR": 0.8607}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    rate = fx.refresh_rate(path=db_path)
    assert rate["usd_to_eur"] == 0.8607
    assert rate["source"] == "frankfurter"


def test_refresh_rate_keeps_cached_rate_on_network_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.90, path=db_path)

    def boom(url, timeout):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(fx.httpx, "get", boom)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.90


def test_refresh_rate_rejects_nonsense_payload(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)

    def fake_get(url, timeout):
        return httpx.Response(200, json={"rates": {}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path) is None
