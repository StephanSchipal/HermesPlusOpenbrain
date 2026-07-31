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


def test_init_db_upgrades_an_old_schema_in_place(tmp_path):
    """The deployed gui.db predates the cost tables. Re-running init_db must
    add them without disturbing existing rows -- this is why every statement
    in _SCHEMA is CREATE ... IF NOT EXISTS."""
    db_path = str(tmp_path / "gui.db")
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE delete_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id TEXT NOT NULL,
                subject_line TEXT,
                keywords TEXT,
                source_url TEXT,
                captured_at TEXT,
                deleted_at TEXT NOT NULL
            );
        """)
        conn.execute("INSERT INTO prompts (text, created_at) VALUES ('keep me', '2026-07-31')")
        conn.commit()

    init_db(db_path)

    with get_conn(db_path) as conn:
        assert [r["text"] for r in conn.execute("SELECT text FROM prompts")] == ["keep me"]
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"external_costs", "fx_rate", "usage_ledger", "usage_watermark"} <= names
