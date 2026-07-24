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
