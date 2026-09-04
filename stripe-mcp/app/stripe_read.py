"""Read-only Stripe access.

Every function here performs ONLY Stripe read operations (`.list`, `.retrieve`).
There is deliberately no `.create`, `.modify`, `.cancel`, `.Refund.create`, or
checkout/invoice creation anywhere in this module or the server. Adapted from
the read branches of github.com/sandraschi/stripe-mcp
(src/stripe_mcp/tools/*.py); the write branches were dropped, not ported.

Mock mode (no real key configured) returns small sample datasets -- derived
from the sister repo's MOCK_* fixtures -- so `hermes mcp test stripe` and the
unit tests run with no Stripe account.
"""

from typing import Any, Dict, List, Optional

from app import config
from app.austria_tax import calculate_austrian_tax
from app.models import AnalyticsMetric, AustrianVatType

# --- mock fixtures ---------------------------------------------------------
_MOCK_CUSTOMERS = [
    {"id": "cus_at_101", "name": "Sandra Mockinger", "email": "sandra@vienna-tech.at",
     "country": "AT", "vat_id": "ATU12345678", "created": 1700000000, "livemode": False,
     "balance_eur": 0.0},
    {"id": "cus_at_102", "name": "Joe Mocky GmbH", "email": "billing@mocky-solutions.at",
     "country": "AT", "vat_id": "ATU87654321", "created": 1700100000, "livemode": False,
     "balance_eur": -150.0},
]

_MOCK_CHARGES = [
    {"id": "ch_at_501", "customer_id": "cus_at_101", "customer_name": "Sandra Mockinger",
     "amount_eur": 299.0, "status": "succeeded", "payment_method": "card", "created": 1700000000},
    {"id": "ch_at_502", "customer_id": "cus_at_102", "customer_name": "Joe Mocky GmbH",
     "amount_eur": 49.0, "status": "succeeded", "payment_method": "eps", "created": 1700100000},
]

_MOCK_SUBSCRIPTIONS = [
    {"id": "sub_at_901", "customer_id": "cus_at_101", "customer_name": "Sandra Mockinger",
     "plan_name": "Enterprise AI Suite (DACH)", "amount_eur": 299.0, "interval": "month",
     "status": "active", "current_period_end": 1750000000, "cancel_at_period_end": False},
    {"id": "sub_at_902", "customer_id": "cus_at_102", "customer_name": "Joe Mocky GmbH",
     "plan_name": "Standard Fleet Agent Plan", "amount_eur": 49.0, "interval": "month",
     "status": "active", "current_period_end": 1749000000, "cancel_at_period_end": False},
]


def _client():
    import stripe

    stripe.api_key = config.STRIPE_API_KEY
    return stripe


def _wrap(data: Any, **extra: Any) -> Dict[str, Any]:
    out = {"success": True, "mode": "MOCK" if config.is_mock_mode() else config.STRIPE_MODE, "data": data}
    out.update(extra)
    return out


def _err(exc: Exception) -> Dict[str, Any]:
    return {"success": False, "error": str(exc)}


# --- customers -----------------------------------------------------------
def list_customers(limit: int = 20, email: Optional[str] = None) -> Dict[str, Any]:
    if config.is_mock_mode():
        rows = _MOCK_CUSTOMERS
        if email:
            rows = [c for c in rows if email.lower() in c["email"].lower()]
        return _wrap(rows, count=len(rows))
    try:
        s = _client()
        params: Dict[str, Any] = {"limit": min(max(limit, 1), 100)}
        if email:
            params["email"] = email
        res = s.Customer.list(**params)
        return _wrap([c.to_dict() for c in res.data], count=len(res.data))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def get_customer(customer_id: str) -> Dict[str, Any]:
    if config.is_mock_mode():
        found = next((c for c in _MOCK_CUSTOMERS if c["id"] == customer_id), None)
        return _wrap(found) if found else {"success": False, "error": f"no mock customer {customer_id}"}
    try:
        return _wrap(_client().Customer.retrieve(customer_id).to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- charges / disputes -------------------------------------------------
def list_charges(limit: int = 20, customer_id: Optional[str] = None) -> Dict[str, Any]:
    if config.is_mock_mode():
        rows = _MOCK_CHARGES
        if customer_id:
            rows = [c for c in rows if c["customer_id"] == customer_id]
        return _wrap(rows, count=len(rows))
    try:
        params: Dict[str, Any] = {"limit": min(max(limit, 1), 100)}
        if customer_id:
            params["customer"] = customer_id
        res = _client().Charge.list(**params)
        return _wrap([c.to_dict() for c in res.data], count=len(res.data))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def get_charge(charge_id: str) -> Dict[str, Any]:
    if config.is_mock_mode():
        found = next((c for c in _MOCK_CHARGES if c["id"] == charge_id), None)
        return _wrap(found) if found else {"success": False, "error": f"no mock charge {charge_id}"}
    try:
        return _wrap(_client().Charge.retrieve(charge_id).to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def list_disputes(limit: int = 20) -> Dict[str, Any]:
    if config.is_mock_mode():
        return _wrap([], count=0, note="No active disputes (mock)")
    try:
        res = _client().Dispute.list(limit=min(max(limit, 1), 100))
        return _wrap([d.to_dict() for d in res.data], count=len(res.data))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- subscriptions -----------------------------------------------------
def list_subscriptions(limit: int = 20, customer_id: Optional[str] = None,
                       status: Optional[str] = None) -> Dict[str, Any]:
    if config.is_mock_mode():
        rows = _MOCK_SUBSCRIPTIONS
        if customer_id:
            rows = [s for s in rows if s["customer_id"] == customer_id]
        if status:
            rows = [s for s in rows if s["status"] == status]
        return _wrap(rows, count=len(rows))
    try:
        params: Dict[str, Any] = {"limit": min(max(limit, 1), 100)}
        if customer_id:
            params["customer"] = customer_id
        if status:
            params["status"] = status
        res = _client().Subscription.list(**params)
        return _wrap([s.to_dict() for s in res.data], count=len(res.data))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def get_subscription(subscription_id: str) -> Dict[str, Any]:
    if config.is_mock_mode():
        found = next((s for s in _MOCK_SUBSCRIPTIONS if s["id"] == subscription_id), None)
        return _wrap(found) if found else {"success": False, "error": f"no mock subscription {subscription_id}"}
    try:
        return _wrap(_client().Subscription.retrieve(subscription_id).to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- balance / analytics ---------------------------------------------
def get_balance() -> Dict[str, Any]:
    if config.is_mock_mode():
        return _wrap({"available": [{"amount": 128000, "currency": "eur"}],
                      "pending": [{"amount": 4200, "currency": "eur"}]})
    try:
        return _wrap(_client().Balance.retrieve().to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def revenue_analytics(metric: AnalyticsMetric = AnalyticsMetric.ALL) -> Dict[str, Any]:
    if config.is_mock_mode():
        mrr = 348.0
        data = {
            "mrr_eur": mrr, "arr_eur": mrr * 12, "active_subscriptions": 2,
            "churn_rate_percent": 1.2, "active_disputes": 0,
            "austrian_vat_summary": {
                "currency": "EUR", "vat_collected_current_month": round(mrr * 0.20, 2),
                "reverse_charge_b2b_sales_eur": 150.0, "bao_compliance_status": "compliant",
            },
        }
        if metric == AnalyticsMetric.MRR:
            return _wrap({"mrr_eur": mrr, "arr_eur": mrr * 12}, metric="mrr")
        if metric == AnalyticsMetric.VAT_SUMMARY:
            return _wrap(data["austrian_vat_summary"], metric="vat_summary")
        return _wrap(data, metric=metric.value)
    try:
        s = _client()
        subs = s.Subscription.list(status="active", limit=100)
        total_cents = 0
        for item in subs.data:
            items = item.get("items", {}).get("data", [])
            if items:
                total_cents += items[0].get("price", {}).get("unit_amount", 0) or 0
        mrr = total_cents / 100.0
        return _wrap({"mrr_eur": mrr, "arr_eur": mrr * 12, "active_subscriptions": len(subs.data)},
                     metric=metric.value)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def austrian_vat(amount: float, vat_type: AustrianVatType = AustrianVatType.STANDARD_20,
                 customer_vat_id: Optional[str] = None) -> Dict[str, Any]:
    # Pure calculation -- never touches Stripe.
    return calculate_austrian_tax(amount, vat_type=vat_type, customer_vat_id=customer_vat_id)
