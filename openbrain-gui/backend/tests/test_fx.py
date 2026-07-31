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

    def fake_get(url, timeout, **kwargs):
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

    def boom(url, timeout, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(fx.httpx, "get", boom)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.90


def test_refresh_rate_rejects_nonsense_payload(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)

    def fake_get(url, timeout, **kwargs):
        return httpx.Response(200, json={"rates": {}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path) is None


@pytest.mark.parametrize("bad_value", [-1, 0, "0.86"])
def test_refresh_rate_rejects_unusable_value_and_keeps_cache(tmp_path, monkeypatch, bad_value):
    """A well-formed 200 whose EUR value is unusable must be refused by the
    explicit sanity check, not written to the cache."""
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    fx.set_manual_rate(0.90, path=db_path)

    def fake_get(url, timeout, **kwargs):
        return httpx.Response(200, json={"rates": {"EUR": bad_value}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    with pytest.raises(fx.FxUnavailable):
        fx.refresh_rate(path=db_path)
    assert fx.get_rate(path=db_path)["usd_to_eur"] == 0.90


@pytest.mark.parametrize("bad_value", [0, -0.5, None])
def test_set_manual_rate_rejects_non_positive(tmp_path, bad_value):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with pytest.raises(ValueError):
        fx.set_manual_rate(bad_value, path=db_path)
    assert fx.get_rate(path=db_path) is None


def test_default_url_targets_the_current_frankfurter_host():
    """frankfurter.app 301-redirects to frankfurter.dev/v1. httpx does not
    follow redirects by default and raise_for_status() treats a 3xx as an
    error, so the old host broke every refresh with a confusing
    'Redirect response 301 Moved Permanently' message."""
    from app import config

    assert "frankfurter.dev" in config.FRANKFURTER_URL
    assert "/v1/" in config.FRANKFURTER_URL


def test_refresh_rate_follows_a_redirect(tmp_path, monkeypatch):
    """Belt and braces: even if the host moves again, a redirect should be
    followed rather than surfaced as a failure."""
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    seen = {}

    def fake_get(url, timeout, follow_redirects=False):
        seen["follow_redirects"] = follow_redirects
        return httpx.Response(200, json={"rates": {"EUR": 0.8707}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    assert fx.refresh_rate(path=db_path)["usd_to_eur"] == 0.8707
    assert seen["follow_redirects"] is True
