# app/ledger_store.py
"""Builds a real time series out of Hermes' lifetime-only counters.

`session_model_usage` holds ONE row per (session, model, task) covering that
session's whole life, so a live read cannot say what was spent on a given day.
This module samples: each tick diffs the current counters against a stored
watermark and appends only what changed, stamped with a real observation time.
That also outlives Hermes' own session pruning, which would otherwise erase
history from the live source.

The FIRST tick seeds watermarks and writes nothing. Without that rule, day one
records a fabricated spike representing all of Hermes' prior history."""
import logging
from datetime import datetime, timezone

from app import hermes_usage
from app.db import get_conn

_log = logging.getLogger(__name__)

_COUNTERS = (
    ("api_call_count", "d_api_calls"),
    ("input_tokens", "d_input"),
    ("output_tokens", "d_output"),
    ("cache_read_tokens", "d_cache_read"),
    ("cache_write_tokens", "d_cache_write"),
    ("reasoning_tokens", "d_reasoning"),
    ("estimated_cost_usd", "d_cost_usd"),
)

_KEY = ("session_id", "model", "task")

_GROUPS = ("model", "platform")


def apply_tick(rows: list[dict], *, path: str | None = None,
               observed_at: str | None = None) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        marks = {
            (r["session_id"], r["model"], r["task"]): dict(r)
            for r in conn.execute("SELECT * FROM usage_watermark")
        }
        seeding = not marks
        written = 0

        for row in rows:
            key = (row["session_id"], row["model"], row.get("task") or "")
            previous = marks.get(key)

            if not seeding:
                deltas = {}
                for source, target in _COUNTERS:
                    current = row.get(source) or 0
                    before = (previous or {}).get(source) or 0
                    # A counter that went backwards means the session was reset
                    # or replaced. Clamp rather than record negative spend.
                    deltas[target] = max(current - before, 0)
                if any(deltas.values()):
                    conn.execute(
                        f"""
                        INSERT INTO usage_ledger
                            (observed_at, session_id, model, task, platform,
                             {", ".join(t for _, t in _COUNTERS)})
                        VALUES (?, ?, ?, ?, ?, {", ".join("?" * len(_COUNTERS))})
                        """,
                        (observed_at, *key, row.get("platform") or "",
                         *(deltas[t] for _, t in _COUNTERS)),
                    )
                    written += 1

            conn.execute(
                f"""
                INSERT INTO usage_watermark
                    ({", ".join(_KEY)}, {", ".join(s for s, _ in _COUNTERS)})
                VALUES ({", ".join("?" * (len(_KEY) + len(_COUNTERS)))})
                ON CONFLICT(session_id, model, task) DO UPDATE SET
                    {", ".join(f"{s} = excluded.{s}" for s, _ in _COUNTERS)}
                """,
                (*key, *(row.get(s) or 0 for s, _ in _COUNTERS)),
            )
        conn.commit()
    return {"seeded": seeding, "rows_written": written}


def timeseries(*, path: str | None = None, days: int = 30,
               group: str = "model", now_iso: str | None = None) -> dict:
    if group not in _GROUPS:
        raise ValueError(f"group must be one of {_GROUPS}, got {group!r}")
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
    cutoff_iso = datetime.fromtimestamp(
        now.timestamp() - days * 86400.0, tz=timezone.utc
    ).isoformat()

    # `group` is validated against a two-item tuple immediately above, so
    # interpolating it into the SQL cannot inject anything. Everything the
    # caller supplies still goes through a `?` placeholder.
    with get_conn(path) as conn:
        rows = conn.execute(
            f"""
            SELECT SUBSTR(observed_at, 1, 10) AS day, {group} AS grp,
                   SUM(d_api_calls)   AS api_calls,
                   SUM(d_input)       AS input_tokens,
                   SUM(d_output)      AS output_tokens,
                   SUM(d_cache_read)  AS cache_read_tokens,
                   SUM(d_cache_write) AS cache_write_tokens,
                   SUM(d_cost_usd)    AS cost_usd
            FROM usage_ledger
            WHERE observed_at >= ?
            GROUP BY day, grp
            ORDER BY day ASC
            """,
            (cutoff_iso,),
        ).fetchall()
        first = conn.execute("SELECT MIN(observed_at) AS first FROM usage_ledger").fetchone()

    points = [
        {"day": r["day"], "group": r["grp"], "cost_usd": r["cost_usd"],
         "api_calls": r["api_calls"], "cache_read_tokens": r["cache_read_tokens"],
         "cache_write_tokens": r["cache_write_tokens"]}
        for r in rows
    ]
    return {
        "points": points,
        "collecting_since": first["first"][:10] if first and first["first"] else None,
        "group": group,
    }


def run_once(*, path: str | None = None, data_dir: str | None = None) -> dict:
    """One poll cycle. Never raises: an absent mount, a torn snapshot copy or a
    locked database are all expected on a small VPS, and the poller must not
    take the app down with it. The next tick retries five minutes later."""
    try:
        rows = hermes_usage.read_usage_rows(data_dir)
    except hermes_usage.HermesDataUnavailable as exc:
        _log.info("ledger tick skipped: %s", exc)
        return {"skipped": str(exc)}
    except Exception as exc:
        _log.warning("ledger tick failed reading state.db: %s", exc)
        return {"skipped": str(exc)}
    try:
        return apply_tick(rows, path=path)
    except Exception as exc:
        _log.warning("ledger tick failed writing gui.db: %s", exc)
        return {"skipped": str(exc)}
