# tests/test_cost_reports_store.py
from app import cost_reports_store as store
from app.db import init_db


def _db(tmp_path):
    db_path = str(tmp_path / "gui.db")
    init_db(db_path)
    return db_path


def test_save_and_get_round_trip(tmp_path):
    db_path = _db(tmp_path)
    payload = {"summary": {"hermes": {"cost_usd": 1.23}}, "by_model": []}
    saved = store.save_report("CostReport_07_20.06.2026-27.06.2026", 7,
                              "20.06.2026 - 27.06.2026", payload, path=db_path)
    assert saved["name"] == "CostReport_07_20.06.2026-27.06.2026"
    assert saved["saved_at"]

    report = store.get_report("CostReport_07_20.06.2026-27.06.2026", path=db_path)
    assert report["days"] == 7
    assert report["range_label"] == "20.06.2026 - 27.06.2026"
    assert report["payload"] == payload


def test_get_report_missing_returns_none(tmp_path):
    db_path = _db(tmp_path)
    assert store.get_report("nope", path=db_path) is None


def test_saving_same_name_again_overwrites(tmp_path):
    db_path = _db(tmp_path)
    store.save_report("CostReport_01_01.07.2026", 1, "01.07.2026",
                      {"summary": {"hermes": {"cost_usd": 1.0}}}, path=db_path)
    store.save_report("CostReport_01_01.07.2026", 1, "01.07.2026",
                      {"summary": {"hermes": {"cost_usd": 2.0}}}, path=db_path)
    reports = store.list_reports(path=db_path)
    assert len(reports) == 1
    report = store.get_report("CostReport_01_01.07.2026", path=db_path)
    assert report["payload"]["summary"]["hermes"]["cost_usd"] == 2.0


def test_list_reports_newest_first(tmp_path):
    db_path = _db(tmp_path)
    store.save_report("a", 7, "range a", {}, path=db_path)
    store.save_report("b", 30, "range b", {}, path=db_path)
    names = [r["name"] for r in store.list_reports(path=db_path)]
    assert names == ["b", "a"]


def test_list_reports_excludes_the_payload(tmp_path):
    # The list endpoint backs the "Load stored report" picker, which shows
    # potentially many reports -- it must stay light, not ship every saved
    # dashboard's full JSON just to render a name.
    db_path = _db(tmp_path)
    store.save_report("a", 7, "range a", {"summary": {"big": "payload"}}, path=db_path)
    assert "payload" not in store.list_reports(path=db_path)[0]
