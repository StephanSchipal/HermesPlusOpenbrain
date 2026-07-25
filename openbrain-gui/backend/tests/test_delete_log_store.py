# tests/test_delete_log_store.py
from app.db import init_db
from app import delete_log_store

def test_log_deletion_then_list_returns_snapshot(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    delete_log_store.log_deletion(
        capture_id="abc-123", subject_line="Sarah's career pivot",
        keywords=["career", "consulting"], source_url="https://example.com/post",
        captured_at="2026-07-20T14:32:00+00:00", path=db_path,
    )
    entries = delete_log_store.list_deletions(path=db_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["capture_id"] == "abc-123"
    assert entry["keywords"] == ["career", "consulting"]
    assert entry["subject_line"] == "Sarah's career pivot"

def test_list_deletions_newest_first(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    delete_log_store.log_deletion(
        capture_id="first", subject_line=None, keywords=[], source_url=None,
        captured_at=None, path=db_path,
    )
    delete_log_store.log_deletion(
        capture_id="second", subject_line=None, keywords=[], source_url=None,
        captured_at=None, path=db_path,
    )
    entries = delete_log_store.list_deletions(path=db_path)
    assert [e["capture_id"] for e in entries] == ["second", "first"]

def test_list_deletions_respects_limit(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    for capture_id in ["first", "second", "third"]:
        delete_log_store.log_deletion(
            capture_id=capture_id, subject_line=None, keywords=[], source_url=None,
            captured_at=None, path=db_path,
        )
    entries = delete_log_store.list_deletions(path=db_path, limit=2)
    assert [e["capture_id"] for e in entries] == ["third", "second"]
