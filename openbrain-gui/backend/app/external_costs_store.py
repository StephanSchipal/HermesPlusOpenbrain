# app/external_costs_store.py
"""The Part 2 spreadsheet: manually-entered external costs (Hostinger, the
Anthropic invoice, ...) stored in gui.db.

Currency model (design spec section 6.1): a row stores exactly ONE amount plus
the currency it was typed in. The other column is derived at read time. So
refreshing the exchange rate moves the derived figure and never rewrites what
the user actually entered."""

PERIODS = ("yearly", "monthly", "onetime", "none")
CURRENCIES = ("USD", "EUR")


def amounts(row: dict, rate: float | None) -> dict:
    """Both currency columns for display. `rate` is USD->EUR; None when no
    rate has ever been fetched, in which case only the entered side is known."""
    amount = row.get("amount")
    if amount is None:
        return {"usd": None, "eur": None}
    if row.get("entered_currency") == "EUR":
        return {"usd": amount / rate if rate else None, "eur": amount}
    return {"usd": amount, "eur": amount * rate if rate else None}


def monthly_usd(row: dict, rate: float | None) -> float:
    """Contribution to the recurring monthly total. `onetime` and `none` are
    excluded by design (spec section 6.2)."""
    usd = amounts(row, rate)["usd"]
    if usd is None:
        return 0.0
    period = row.get("period")
    if period == "monthly":
        return usd
    if period == "yearly":
        return usd / 12.0
    return 0.0


def onetime_usd(row: dict, rate: float | None) -> float:
    if row.get("period") != "onetime":
        return 0.0
    return amounts(row, rate)["usd"] or 0.0


def totals(rows: list[dict], rate: float | None) -> dict:
    monthly = sum(monthly_usd(r, rate) for r in rows)
    onetime = sum(onetime_usd(r, rate) for r in rows)
    return {
        "monthly_usd": monthly,
        "monthly_eur": monthly * rate if rate else None,
        "onetime_usd": onetime,
        "onetime_eur": onetime * rate if rate else None,
    }
