# tests/test_external_costs_store.py
import pytest
from app import external_costs_store as store

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
