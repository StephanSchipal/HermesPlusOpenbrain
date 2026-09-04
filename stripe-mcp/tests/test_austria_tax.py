"""Vendored from github.com/sandraschi/stripe-mcp (tests/test_austria_tax.py).
Import path adjusted (stripe_mcp -> app)."""

from app.austria_tax import calculate_austrian_tax, validate_atu_vat_id
from app.models import AustrianVatType


def test_standard_vat_calculation():
    res = calculate_austrian_tax(100.0, vat_type=AustrianVatType.STANDARD_20)
    assert res["net_amount"] == 100.0
    assert res["vat_rate_percent"] == 20
    assert res["vat_amount"] == 20.0
    assert res["gross_amount"] == 120.0
    assert res["reverse_charge_applied"] is False


def test_reduced_vat_calculation():
    res_10 = calculate_austrian_tax(100.0, vat_type=AustrianVatType.REDUCED_10)
    assert res_10["vat_amount"] == 10.0

    res_13 = calculate_austrian_tax(100.0, vat_type=AustrianVatType.REDUCED_13)
    assert res_13["vat_amount"] == 13.0


def test_atu_vat_id_validation():
    assert validate_atu_vat_id("ATU12345678")["is_valid"] is True
    assert validate_atu_vat_id("atu87654321")["is_valid"] is True
    assert validate_atu_vat_id("ATU1234")["is_valid"] is False
    assert validate_atu_vat_id("DE123456789")["is_valid"] is False


def test_reverse_charge_vat_calculation():
    res = calculate_austrian_tax(100.0, customer_vat_id="ATU12345678")
    assert res["vat_rate_percent"] == 0
    assert res["vat_amount"] == 0.0
    assert res["gross_amount"] == 100.0
    assert res["reverse_charge_applied"] is True
    assert "Reverse Charge" in res["legal_note"]
