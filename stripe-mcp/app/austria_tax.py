"""Vendored verbatim from github.com/sandraschi/stripe-mcp
(src/stripe_mcp/austria_tax.py). Only the import path changed
(stripe_mcp.models -> app.models). Pure functions, no I/O, no Stripe calls.
"""

import re
from typing import Any, Dict, Optional

from app.models import AustrianVatType

VAT_RATES = {
    AustrianVatType.STANDARD_20: 0.20,
    AustrianVatType.REDUCED_10: 0.10,
    AustrianVatType.REDUCED_13: 0.13,
}

# Syntax regex for Austrian UID / ATU numbers
ATU_REGEX = re.compile(r"^ATU\d{8}$", re.IGNORECASE)


def validate_atu_vat_id(vat_id: str) -> Dict[str, Any]:
    """
    Validates Austrian UID (Umsatzsteuer-Identifikationsnummer) format.
    Format: ATU followed by 8 digits.
    """
    cleaned = vat_id.strip().upper()
    is_valid = bool(ATU_REGEX.match(cleaned))
    return {
        "vat_id": cleaned,
        "is_valid": is_valid,
        "country": "AT",
        "reverse_charge_eligible": is_valid,
        "message": "Valid Austrian ATU VAT ID (Reverse Charge Eligible)"
        if is_valid
        else "Invalid ATU format (expected ATU12345678)",
    }


def calculate_austrian_tax(
    net_amount: float,
    vat_type: AustrianVatType = AustrianVatType.STANDARD_20,
    customer_vat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Calculates Austrian VAT, applies Reverse Charge if valid VAT ID provided,
    and returns BAO § 132 compliant invoice metadata.
    """
    reverse_charge = False
    rate = VAT_RATES.get(vat_type, 0.20)

    if customer_vat_id:
        validation = validate_atu_vat_id(customer_vat_id)
        if validation["reverse_charge_eligible"]:
            reverse_charge = True
            rate = 0.0

    vat_amount = round(net_amount * rate, 2)
    gross_amount = round(net_amount + vat_amount, 2)

    return {
        "net_amount": net_amount,
        "vat_type": vat_type.value,
        "vat_rate_percent": int(rate * 100),
        "vat_amount": vat_amount,
        "gross_amount": gross_amount,
        "currency": "EUR",
        "reverse_charge_applied": reverse_charge,
        "legal_note": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)"
        if reverse_charge
        else "Inkl. österreichischer Umsatzsteuer",
        "bao_compliance": {
            "retention_years": 7,
            "legal_basis": "§ 132 BAO (Bundesabgabenordnung)",
            "seller_uid_default": "ATU78901234",
        },
    }
