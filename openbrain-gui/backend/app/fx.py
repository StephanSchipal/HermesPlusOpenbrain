# app/fx.py
"""USD->EUR rate for the cost page. One row in `fx_rate`, refreshed from
frankfurter.app (European Central Bank daily reference rates -- free, no API
key) or set manually. A failed refresh NEVER clears the cached rate: the
external-cost grid must stay usable with a stale rate rather than break on a
network hiccup (design spec section 9)."""
import httpx
from datetime import datetime, timezone

from app.config import FRANKFURTER_URL, FX_TIMEOUT_SECONDS
from app.db import get_conn


class FxUnavailable(RuntimeError):
    """Raised when a fresh rate could not be obtained. The caller should keep
    showing whatever `get_rate` returns."""


def get_rate(*, path: str | None = None) -> dict | None:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT usd_to_eur, source, fetched_at FROM fx_rate WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def _store(usd_to_eur: float, source: str, *, path: str | None = None) -> dict:
    fetched_at = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO fx_rate (id, usd_to_eur, source, fetched_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                usd_to_eur = excluded.usd_to_eur,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (usd_to_eur, source, fetched_at),
        )
        conn.commit()
    return {"usd_to_eur": usd_to_eur, "source": source, "fetched_at": fetched_at}


def set_manual_rate(usd_to_eur: float, *, path: str | None = None) -> dict:
    if not usd_to_eur or usd_to_eur <= 0:
        raise ValueError("rate must be a positive number")
    return _store(float(usd_to_eur), "manual", path=path)


def refresh_rate(*, path: str | None = None) -> dict:
    try:
        resp = httpx.get(FRANKFURTER_URL, timeout=FX_TIMEOUT_SECONDS)
        resp.raise_for_status()
        value = resp.json()["rates"]["EUR"]
    except Exception as exc:
        raise FxUnavailable(f"could not fetch rate: {exc}") from exc
    if not isinstance(value, (int, float)) or value <= 0:
        raise FxUnavailable(f"frankfurter returned an unusable rate: {value!r}")
    return _store(float(value), "frankfurter", path=path)
