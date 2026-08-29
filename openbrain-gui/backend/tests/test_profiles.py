# tests/test_profiles.py
import sqlite3
from app import profiles


def _mk_statedb(d):
    d.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(d / "state.db")).close()


def test_root_only(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    got = profiles.list_profiles()
    assert got == [{"key": "default", "label": "Hermes-Agent", "data_dir": str(tmp_path)}]


def test_root_plus_named_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    for name in ("writer", "coder", "openbrain"):
        _mk_statedb(tmp_path / "profiles" / name)
    keys = [p["key"] for p in profiles.list_profiles()]
    assert keys == ["default", "coder", "openbrain", "writer"]


def test_profile_dir_without_statedb_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    (tmp_path / "profiles" / "empty").mkdir(parents=True)
    assert [p["key"] for p in profiles.list_profiles()] == ["default"]


def test_reserved_names_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    _mk_statedb(tmp_path / "profiles" / "default")
    _mk_statedb(tmp_path / "profiles" / "all")
    _mk_statedb(tmp_path / "profiles" / "coder")
    keys = [p["key"] for p in profiles.list_profiles()]
    assert keys == ["default", "coder"]


def test_absent_mount_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path / "nope"))
    assert profiles.list_profiles() == []


def test_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "HERMES_DATA_DIR", str(tmp_path))
    _mk_statedb(tmp_path)
    _mk_statedb(tmp_path / "profiles" / "coder")
    assert profiles.resolve("default") == str(tmp_path)
    assert profiles.resolve("coder") == str(tmp_path / "profiles" / "coder")
    assert profiles.resolve("ghost") is None
