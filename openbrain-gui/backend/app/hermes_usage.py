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

A copy taken mid-write can land torn -- `shutil.copy2` can race Hermes' own
WAL checkpoint. That is an expected failure: `snapshot()` converts the
resulting `sqlite3.DatabaseError` into `HermesDataUnavailable`, so callers
degrade (503) instead of crashing (500).
"""
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

import yaml

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
        try:
            conn = sqlite3.connect(str(dst))
            conn.row_factory = sqlite3.Row
        except sqlite3.DatabaseError as exc:
            # sqlite3.connect() is lazy and essentially never fails here in
            # practice (verified: it opens even a garbage file without error);
            # the torn-copy failure actually surfaces at query time, below.
            # This is kept as a second line of defence in case that ever changes.
            raise HermesDataUnavailable(f"snapshot copy is unreadable: {exc}") from exc
        try:
            yield conn
        except sqlite3.DatabaseError as exc:
            # A copy taken mid-checkpoint can be torn. Surface it as the
            # module's own unavailable-error so callers degrade to 503 rather
            # than 500 -- this is the "expected failure" the module docstring
            # means.
            raise HermesDataUnavailable(f"snapshot copy is unreadable: {exc}") from exc
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
    # LEFT JOIN, not JOIN: a usage row whose session has been pruned still
    # carries real spend and must not be dropped from the aggregate (same
    # rationale as `read_usage_rows`). `by_platform` passes
    # `COALESCE(s.source, '')` so orphaned rows group under '' instead of
    # vanishing.
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT {group_sql} AS {label}, {_SUM_COLUMNS}
            FROM session_model_usage u
            LEFT JOIN sessions s ON s.id = u.session_id
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
    return _grouped(data_dir, days, now, "COALESCE(s.source, '')", "platform")


def summary(data_dir: str | None = None, *, days: int = 30,
            now: float | None = None) -> dict:
    # No join to `sessions` -- `_SUM_COLUMNS` reads only `u.*`, so a join here
    # would exist purely to filter out pruned sessions, and their spend is
    # real money that must not disappear from the total (matches the LEFT
    # JOIN rationale in `read_usage_rows`).
    with snapshot(data_dir) as conn:
        row = dict(conn.execute(
            f"""
            SELECT {_SUM_COLUMNS}
            FROM session_model_usage u
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
    cutoff, until = _window(days, now)
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(s.source, '') AS platform,
                   -- A session has one usage row per (model, task), so joining
                   -- and averaging directly would count a session's messages
                   -- once per row. Average over DISTINCT sessions instead.
                   --
                   -- For a pruned session, s.source is NULL, so
                   -- `s2.source = s.source` matches nothing and this is NULL
                   -- for that group -- there is no session row left to
                   -- average, which is honest given the data.
                   (SELECT AVG(s2.message_count)
                      FROM sessions s2
                     WHERE s2.source = s.source
                       AND s2.id IN (SELECT session_id FROM session_model_usage
                                      WHERE last_seen >= ? AND last_seen <= ?)
                   ) AS avg_messages_per_session,
                   {_SUM_COLUMNS}
            FROM session_model_usage u
            LEFT JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY COALESCE(s.source, '')
            HAVING api_calls > 0
            ORDER BY cost_usd DESC
            """,
            (cutoff, until, cutoff, until),
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


def by_session(data_dir: str | None = None, *, days: int = 30,
               now: float | None = None, limit: int = 50) -> list[dict]:
    # LEFT JOIN, not JOIN: a pruned session's usage row still carries real
    # spend. `title`/`platform` are left NULL rather than invented -- the UI
    # can render "(pruned)".
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT u.session_id, s.title, s.source AS platform,
                   s.message_count, s.tool_call_count,
                   GROUP_CONCAT(DISTINCT u.model) AS models,
                   MAX(u.last_seen) AS last_seen,
                   {_SUM_COLUMNS}
            FROM session_model_usage u
            LEFT JOIN sessions s ON s.id = u.session_id
            WHERE u.last_seen >= ? AND u.last_seen <= ?
            GROUP BY u.session_id
            ORDER BY cost_usd DESC
            LIMIT ?
            """,
            (*_window(days, now), limit),
        ).fetchall()
    # GROUP_CONCAT gives an unordered delimited string; turn it into a
    # deterministic sorted list rather than leaving callers to parse it.
    return [
        {**dict(r),
         "models": sorted((r["models"] or "").split(",")) if r["models"] else []}
        for r in rows
    ]


def session_detail(session_id: str, *, data_dir: str | None = None) -> dict | None:
    with snapshot(data_dir) as conn:
        session = conn.execute(
            """
            SELECT id, title, source AS platform, model, message_count,
                   tool_call_count, cwd, git_branch, profile_name, system_prompt,
                   compression_fallback_streak, compression_failure_error,
                   compression_failure_cooldown_until
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        models = conn.execute(
            """
            SELECT model, COALESCE(task, '') AS task,
                   COALESCE(api_call_count, 0)     AS api_calls,
                   COALESCE(input_tokens, 0)       AS input_tokens,
                   COALESCE(output_tokens, 0)      AS output_tokens,
                   COALESCE(cache_read_tokens, 0)  AS cache_read_tokens,
                   COALESCE(cache_write_tokens, 0) AS cache_write_tokens,
                   COALESCE(estimated_cost_usd, 0) AS cost_usd,
                   first_seen, last_seen
            FROM session_model_usage WHERE session_id = ?
            ORDER BY cost_usd DESC
            """,
            (session_id,),
        ).fetchall()

    detail = dict(session)
    detail["system_prompt_chars"] = len(detail.get("system_prompt") or "")
    detail["models"] = [dict(m) for m in models]
    return detail


# Only these keys are ever read out of config.yaml. The file also holds API
# keys and auth material; an explicit whitelist means a future edit to the
# panel cannot accidentally start returning them.
_CONFIG_KEYS = (
    ("model", "default"),
    ("compression", "threshold"),
    ("compression", "threshold_tokens"),
    ("tool_output", "max_bytes"),
    ("prompt_caching", "cache_ttl"),
    ("agent", "max_turns"),
    ("agent", "disabled_toolsets"),
    ("sessions", "auto_prune"),
    ("sessions", "retention_days"),
)


def top_tools(data_dir: str | None = None, *, days: int = 30,
              now: float | None = None, limit: int = 15) -> dict:
    """Call counts by tool. Counts ONLY -- `messages.token_count` is NULL in
    every row of the live database, so tokens cannot be attributed to tools.
    The flag is returned so the UI states this rather than implying precision
    the data does not have."""
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT tool_name, COUNT(*) AS calls
            FROM messages
            WHERE tool_name IS NOT NULL AND tool_name != ''
              AND timestamp >= ? AND timestamp <= ?
            GROUP BY tool_name
            ORDER BY calls DESC
            LIMIT ?
            """,
            (*_window(days, now), limit),
        ).fetchall()
    return {
        "tools": [dict(r) for r in rows],
        "token_attribution_available": False,
    }


def prompt_budget(data_dir: str | None = None, *, days: int = 30,
                  now: float | None = None) -> list[dict]:
    with snapshot(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT s.source AS platform,
                   COUNT(*)                       AS sessions,
                   AVG(LENGTH(s.system_prompt))   AS avg_system_prompt_chars,
                   MAX(LENGTH(s.system_prompt))   AS max_system_prompt_chars
            FROM sessions s
            WHERE s.system_prompt IS NOT NULL
              AND s.id IN (SELECT session_id FROM session_model_usage
                            WHERE last_seen >= ? AND last_seen <= ?)
            GROUP BY s.source
            ORDER BY avg_system_prompt_chars DESC
            """,
            _window(days, now),
        ).fetchall()
    return [dict(r) for r in rows]


def config_snapshot(data_dir: str | None = None) -> dict:
    path = Path(data_dir or HERMES_DATA_DIR) / "config.yaml"
    if not path.is_file():
        raise HermesDataUnavailable(f"Hermes config.yaml not found at {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise HermesDataUnavailable(f"config.yaml is not valid YAML: {exc}") from exc

    snap: dict = {}
    for section, key in _CONFIG_KEYS:
        block = loaded.get(section)
        value = block.get(key) if isinstance(block, dict) else None
        if value is not None:
            snap[f"{section}.{key}"] = value
    return snap
