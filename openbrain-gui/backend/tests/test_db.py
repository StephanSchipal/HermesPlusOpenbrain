# tests/test_db.py
from app.db import init_db, get_conn

def test_init_db_creates_prompts_and_delete_log_tables(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"prompts", "delete_log"} <= tables

def test_init_db_is_idempotent(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    init_db(db_path)  # must not raise on a second call (CREATE TABLE IF NOT EXISTS)


def test_init_db_creates_cost_tables(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"external_costs", "fx_rate", "usage_ledger", "usage_watermark"} <= names


def test_init_db_is_idempotent_on_existing_db(tmp_path):
    """The deployed gui.db already has prompts/delete_log -- re-running
    init_db must add the new tables without touching existing rows."""
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    with get_conn(db_path) as conn:
        conn.execute("INSERT INTO prompts (text, created_at) VALUES ('keep me', '2026-07-31')")
        conn.commit()
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT text FROM prompts").fetchall()
    assert [r["text"] for r in rows] == ["keep me"]
