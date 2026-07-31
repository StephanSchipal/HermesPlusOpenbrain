# app/hermes_usage.py
"""Read-only access to Hermes' own `state.db`, mounted at /hermes-data.

`state.db` is WAL-mode, and reading a WAL database makes SQLite create and
maintain a `-shm` coordination file NEXT TO the database. That needs write
access to the containing directory -- not to the database file, and not
avoidable via `mode=ro` (verified: `mode=ro` on a writable directory succeeds
and silently creates the `-shm`). Our mount is `:ro`, so the open fails there;
and were it writable we would be depositing files into Hermes' own data dir,
which is exactly what this module exists to avoid.

Every read therefore copies state.db (+ -wal) into a TemporaryDirectory and
opens the COPY, letting SQLite build its `-shm` in scratch space. Hermes' files
are only ever read.

`state.db-shm` is deliberately NOT copied: it is derived state that SQLite
rebuilds from the WAL, and a stale copy is worse than none.

A copy taken mid-write can land torn. That is an expected failure, not an
exception path worth special-casing -- the caller serves the previous snapshot.
"""
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from app.config import HERMES_DATA_DIR


class HermesDataUnavailable(RuntimeError):
    """/hermes-data is not mounted, or state.db is not readable there."""


@contextmanager
def snapshot(data_dir: str | None = None) -> Iterator[sqlite3.Connection]:
    src = Path(data_dir or HERMES_DATA_DIR) / "state.db"
    if not src.is_file():
        raise HermesDataUnavailable(f"Hermes state.db not found at {src}")
    with tempfile.TemporaryDirectory(prefix="hermes-snap-") as tmp:
        dst = Path(tmp) / "state.db"
        try:
            shutil.copy2(src, dst)
            wal = src.with_name("state.db-wal")
            if wal.is_file():
                shutil.copy2(wal, dst.with_name("state.db-wal"))
        except OSError as exc:
            raise HermesDataUnavailable(f"could not copy state.db: {exc}") from exc
        conn = sqlite3.connect(str(dst))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def read_usage_rows(data_dir: str | None = None) -> list[dict]:
    """Raw per-(session, model, task) lifetime counters, plus the session's
    platform. Used by the ledger poller, which needs the numbers unaggregated
    and unfiltered -- and needs `platform` denormalised, because the chart must
    group by it later without re-reading state.db.

    LEFT JOIN, not JOIN: a usage row whose session has been pruned still
    carries real spend and must not vanish from the ledger."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT u.session_id, u.model, COALESCE(u.task, '') AS task,
                   COALESCE(s.source, '')            AS platform,
                   COALESCE(u.api_call_count, 0)     AS api_call_count,
                   COALESCE(u.input_tokens, 0)       AS input_tokens,
                   COALESCE(u.output_tokens, 0)      AS output_tokens,
                   COALESCE(u.cache_read_tokens, 0)  AS cache_read_tokens,
                   COALESCE(u.cache_write_tokens, 0) AS cache_write_tokens,
                   COALESCE(u.reasoning_tokens, 0)   AS reasoning_tokens,
                   COALESCE(u.estimated_cost_usd, 0) AS estimated_cost_usd,
                   u.last_seen
            FROM session_model_usage u
            LEFT JOIN sessions s ON s.id = u.session_id
            """
        ).fetchall()
    return [dict(r) for r in rows]


# A session's usage row spans its whole life, so it cannot be split across days.
# The window rule is: include the row when `last_seen` falls inside it, and
# count the whole row. Exact totals, slightly late attribution -- proportional
# splitting would invent numbers the source does not contain.
_SUM_COLUMNS = """
    COUNT(DISTINCT u.session_id)               AS sessions,
    SUM(COALESCE(u.api_call_count, 0))         AS api_calls,
    SUM(COALESCE(u.input_tokens, 0))           AS input_tokens,
    SUM(COALESCE(u.output_tokens, 0))          AS output_tokens,
    SUM(COALESCE(u.cache_read_tokens, 0))      AS cache_read_tokens,
    SUM(COALESCE(u.cache_write_tokens, 0))     AS cache_write_tokens,
    SUM(COALESCE(u.reasoning_tokens, 0))       AS reasoning_tokens,
    SUM(COALESCE(u.estimated_cost_usd, 0))     AS cost_usd
"""


def _window(days: int, now: float | None) -> tuple[float, float]:
    """The window is `[now - days, now]`, not just `>= cutoff` -- a lower
    bound alone would let rows leak in whenever `now` is set earlier than the
    data (e.g. probing an empty window in the past), since their real
    `last_seen` values still satisfy `>= cutoff`."""
    effective_now = now if now is not None else time.time()
    return effective_now - days * 86400.0, effective_now


def _grouped(data_dir: str | None, days: int, now: float | None,
             group_sql: str, label: str) -> list[dict]:
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT {group_sql} AS {label}, {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY {group_sql}
            ORDER BY cost_usd DESC
            """,
            _window(days, now),
        ).fetchall()
    return [dict(r) for r in rows]


def by_model(data_dir: str | None = None, *, days: int = 30,
             now: float | None = None) -> list[dict]:
    return _grouped(data_dir, days, now, "u.model", "model")


def by_platform(data_dir: str | None = None, *, days: int = 30,
                now: float | None = None) -> list[dict]:
    return _grouped(data_dir, days, now, "s.source", "platform")


def summary(data_dir: str | None = None, *, days: int = 30,
            now: float | None = None) -> dict:
    with snapshot(data_dir) as conn:
        row = dict(conn.execute(
            f"""
            SELECT {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            """,
            _window(days, now),
        ).fetchone())
    for key, value in row.items():
        if value is None:
            row[key] = 0
    denominator = (row["cache_read_tokens"] + row["cache_write_tokens"]
                   + row["input_tokens"])
    row["cache_hit_rate"] = (
        row["cache_read_tokens"] / denominator if denominator else None
    )
    row["cost_status"] = "estimated"
    return row


def efficiency(data_dir: str | None = None, *, days: int = 30,
               now: float | None = None) -> list[dict]:
    """Per-platform per-call averages. Prompt size times call count is what
    drives the bill, and cache WRITE volume per call is where the money
    actually goes -- a write costs 12.5x a read."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT s.source AS platform, AVG(s.message_count) AS avg_messages_per_session,
                   {_SUM_COLUMNS}
            FROM session_model_usage u
            JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY s.source
            HAVING api_calls > 0
            ORDER BY cost_usd DESC
            """,
            _window(days, now),
        ).fetchall()

    result = []
    for raw in rows:
        row = dict(raw)
        calls = row["api_calls"]
        total_tokens = (row["input_tokens"] + row["output_tokens"]
                        + row["cache_read_tokens"] + row["cache_write_tokens"])
        # `HAVING api_calls > 0` already excludes the divide-by-zero case;
        # the guard keeps that true if the HAVING clause is ever relaxed.
        row["tokens_per_call"] = total_tokens / calls if calls else None
        row["cache_write_per_call"] = row["cache_write_tokens"] / calls if calls else None
        row["cost_per_call"] = row["cost_usd"] / calls if calls else None
        result.append(row)
    return result
