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
