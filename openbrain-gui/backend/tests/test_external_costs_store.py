# tests/test_external_costs_store.py
import pytest
from app import external_costs_store as store
from app.db import init_db

RATE = 0.80  # 1 USD = 0.80 EUR, chosen so the arithmetic is obvious by eye


def test_usd_row_derives_eur():
    row = {"amount": 10.0, "entered_currency": "USD"}
    assert store.amounts(row, RATE) == {"usd": 10.0, "eur": 8.0}


def test_eur_row_derives_usd():
    row = {"amount": 8.0, "entered_currency": "EUR"}
    assert store.amounts(row, RATE) == {"usd": 10.0, "eur": 8.0}


def test_amounts_without_rate_leaves_derived_side_none():
    row = {"amount": 10.0, "entered_currency": "USD"}
    assert store.amounts(row, None) == {"usd": 10.0, "eur": None}


def test_amounts_with_null_amount():
    row = {"amount": None, "entered_currency": "USD"}
    assert store.amounts(row, RATE) == {"usd": None, "eur": None}


@pytest.mark.parametrize("period,expected_monthly,expected_onetime", [
    ("monthly", 12.0, 0.0),
    ("yearly", 1.0, 0.0),
    ("onetime", 0.0, 12.0),
    ("none", 0.0, 0.0),
])
def test_period_rules(period, expected_monthly, expected_onetime):
    row = {"amount": 12.0, "entered_currency": "USD", "period": period}
    assert store.monthly_usd(row, RATE) == pytest.approx(expected_monthly)
    assert store.onetime_usd(row, RATE) == pytest.approx(expected_onetime)


def test_totals_sums_recurring_and_onetime_separately():
    rows = [
        {"amount": 12.99, "entered_currency": "USD", "period": "monthly"},
        {"amount": 120.0, "entered_currency": "USD", "period": "yearly"},
        {"amount": 50.0, "entered_currency": "USD", "period": "onetime"},
        {"amount": 999.0, "entered_currency": "USD", "period": "none"},
    ]
    totals = store.totals(rows, RATE)
    assert totals["monthly_usd"] == pytest.approx(22.99)   # 12.99 + 120/12
    assert totals["monthly_eur"] == pytest.approx(18.392)
    assert totals["onetime_usd"] == pytest.approx(50.0)


def _db(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    return db_path


def test_save_assigns_ids_and_lists_back(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": "https://hpanel.hostinger.com",
         "comments": "KVM2", "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    assert saved[0]["id"] is not None
    rows = store.list_rows(path=db_path)
    assert len(rows) == 1
    assert rows[0]["name"] == "Hostinger"
    assert rows[0]["amount"] == 12.99


def test_save_updates_existing_row_in_place(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    row_id = saved[0]["id"]
    store.save_rows([{**saved[0], "amount": 14.99}], path=db_path)
    rows = store.list_rows(path=db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["amount"] == 14.99


def test_delete_row(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Gone", "period": "none", "amount": None, "entered_currency": "USD",
         "url": None, "comments": None, "compare_to_estimate": 0, "sort_order": 0},
    ], path=db_path)
    assert store.delete_row(saved[0]["id"], path=db_path) is True
    assert store.list_rows(path=db_path) == []
    assert store.delete_row(9999, path=db_path) is False


def test_only_one_row_can_carry_the_compare_flag(tmp_path):
    db_path = _db(tmp_path)
    saved = store.save_rows([
        {"name": "Anthropic", "period": "monthly", "amount": 94.17,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 1, "sort_order": 0},
        {"name": "Hostinger", "period": "monthly", "amount": 12.99,
         "entered_currency": "USD", "url": None, "comments": None,
         "compare_to_estimate": 0, "sort_order": 1},
    ], path=db_path)
    # Now flag the second row too -- the first must be cleared.
    store.save_rows([{**saved[1], "compare_to_estimate": 1}], path=db_path)
    rows = {r["name"]: r for r in store.list_rows(path=db_path)}
    assert rows["Hostinger"]["compare_to_estimate"] == 1
    assert rows["Anthropic"]["compare_to_estimate"] == 0


@pytest.mark.parametrize("bad", [
    {"period": "weekly"},
    {"entered_currency": "GBP"},
    {"amount": -5.0},
    {"url": "javascript:alert(1)"},
    {"url": "file:///etc/passwd"},
])
def test_save_rejects_invalid_field(tmp_path, bad):
    db_path = _db(tmp_path)
    row = {"name": "x", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "url": None, "comments": None, "compare_to_estimate": 0, "sort_order": 0}
    with pytest.raises(ValueError):
        store.save_rows([{**row, **bad}], path=db_path)


def test_save_accepts_http_and_https_urls(tmp_path):
    db_path = _db(tmp_path)
    row = {"name": "x", "period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "comments": None, "compare_to_estimate": 0, "sort_order": 0}
    store.save_rows([
        {**row, "url": "https://example.com/billing"},
        {**row, "url": "http://example.com/billing"},
        {**row, "url": ""},
    ], path=db_path)
    assert len(store.list_rows(path=db_path)) == 3


def test_list_rows_ordered_by_sort_order_then_id(tmp_path):
    db_path = _db(tmp_path)
    row = {"period": "monthly", "amount": 1.0, "entered_currency": "USD",
           "url": None, "comments": None, "compare_to_estimate": 0}
    store.save_rows([
        {**row, "name": "third", "sort_order": 2},
        {**row, "name": "first", "sort_order": 0},
        {**row, "name": "second", "sort_order": 1},
    ], path=db_path)
    assert [r["name"] for r in store.list_rows(path=db_path)] == ["first", "second", "third"]
