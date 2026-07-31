# app/external_costs_store.py
"""The Part 2 spreadsheet: manually-entered external costs (Hostinger, the
Anthropic invoice, ...) stored in gui.db.

Currency model (design spec section 6.1): a row stores exactly ONE amount plus
the currency it was typed in. The other column is derived at read time. So
refreshing the exchange rate moves the derived figure and never rewrites what
the user actually entered."""

from datetime import datetime, timezone
from urllib.parse import urlparse

from app.db import get_conn

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


_ALLOWED_URL_SCHEMES = ("http", "https")

_FIELDS = ("name", "period", "amount", "entered_currency", "url",
           "comments", "compare_to_estimate", "sort_order")


def _validate(row: dict) -> None:
    if row.get("period") not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {row.get('period')!r}")
    if row.get("entered_currency") not in CURRENCIES:
        raise ValueError(
            f"entered_currency must be one of {CURRENCIES}, got {row.get('entered_currency')!r}"
        )
    amount = row.get("amount")
    if amount is not None and amount < 0:
        raise ValueError(f"amount must be >= 0, got {amount}")
    url = row.get("url")
    if url:
        # Blocks javascript:/file:/data: URLs, which the grid renders as a
        # clickable anchor -- see design spec section 9.1.
        if urlparse(url).scheme not in _ALLOWED_URL_SCHEMES:
            raise ValueError(f"url scheme must be http or https, got {url!r}")


def list_rows(*, path: str | None = None) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, period, amount, entered_currency, url, comments,
                   compare_to_estimate, sort_order, created_at, updated_at
            FROM external_costs ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def save_rows(rows: list[dict], *, path: str | None = None) -> list[dict]:
    """Insert rows without an `id`, update those with one. Validation runs over
    every row BEFORE anything is written, so a bad row in the batch cannot leave
    a half-saved table behind."""
    for row in rows:
        _validate(row)

    now = datetime.now(timezone.utc).isoformat()
    saved: list[dict] = []
    with get_conn(path) as conn:
        for row in rows:
            values = tuple(row.get(f) for f in _FIELDS)
            row_id = row.get("id")
            if row_id is None:
                cur = conn.execute(
                    f"""
                    INSERT INTO external_costs
                        ({", ".join(_FIELDS)}, created_at, updated_at)
                    VALUES ({", ".join("?" * len(_FIELDS))}, ?, ?)
                    """,
                    values + (now, now),
                )
                row_id = cur.lastrowid
            else:
                conn.execute(
                    f"""
                    UPDATE external_costs
                    SET {", ".join(f"{f} = ?" for f in _FIELDS)}, updated_at = ?
                    WHERE id = ?
                    """,
                    values + (now, row_id),
                )
            if row.get("compare_to_estimate"):
                # Invariant (design spec section 6.3): at most one row carries
                # the flag. Enforced here rather than in the UI so it holds no
                # matter who writes.
                conn.execute(
                    "UPDATE external_costs SET compare_to_estimate = 0 WHERE id != ?",
                    (row_id,),
                )
            saved.append({**row, "id": row_id})
        conn.commit()
    return saved


def delete_row(row_id: int, *, path: str | None = None) -> bool:
    with get_conn(path) as conn:
        cur = conn.execute("DELETE FROM external_costs WHERE id = ?", (row_id,))
        conn.commit()
        return cur.rowcount > 0


def flagged_row(*, path: str | None = None) -> dict | None:
    """The row marked 'compare to Hermes estimate', if any."""
    for row in list_rows(path=path):
        if row["compare_to_estimate"]:
            return row
    return None
