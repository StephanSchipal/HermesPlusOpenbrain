# app/cost_reports_store.py
"""Saved Part 1 cost-dashboard snapshots, stored as an opaque JSON payload in
gui.db. The comparison workflow is "open the GUI in two windows, load a
different saved report in each" (or one live, one saved) -- not a SQL diff
between reports -- so the payload is never queried into; it is written and
read back whole. `name` is the natural key: saving again under a name already
in use overwrites that row (deterministic per day+range, so re-saving today's
"Today" report is just refreshing it, not creating a duplicate)."""

from datetime import datetime, timezone
import json

from app.db import get_conn

_LIST_FIELDS = ("name", "days", "range_label", "saved_at")


def save_report(name: str, days: int, range_label: str, payload: dict,
                *, path: str | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO cost_reports (name, days, range_label, saved_at, payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                days = excluded.days,
                range_label = excluded.range_label,
                saved_at = excluded.saved_at,
                payload = excluded.payload
            """,
            (name, days, range_label, now, json.dumps(payload)),
        )
        conn.commit()
    return {"name": name, "days": days, "range_label": range_label, "saved_at": now}


def list_reports(*, path: str | None = None) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_LIST_FIELDS)} FROM cost_reports ORDER BY saved_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_report(name: str, *, path: str | None = None) -> dict | None:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT name, days, range_label, saved_at, payload FROM cost_reports WHERE name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    return result
