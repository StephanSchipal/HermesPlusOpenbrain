# tests/test_prompts_store.py
from app.db import init_db
from app import prompts_store

def test_create_list_delete_prompt_roundtrip(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    created = prompts_store.create_prompt("ai agents this week", path=db_path)
    assert created["text"] == "ai agents this week"
    listed = prompts_store.list_prompts(path=db_path)
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert prompts_store.delete_prompt(created["id"], path=db_path) is True
    assert prompts_store.list_prompts(path=db_path) == []

def test_delete_prompt_returns_false_when_missing(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    assert prompts_store.delete_prompt(999, path=db_path) is False

def test_list_prompts_newest_first(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    prompts_store.create_prompt("first", path=db_path)
    prompts_store.create_prompt("second", path=db_path)
    listed = prompts_store.list_prompts(path=db_path)
    assert [p["text"] for p in listed] == ["second", "first"]
