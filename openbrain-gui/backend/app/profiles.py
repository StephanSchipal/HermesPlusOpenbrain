# app/profiles.py
"""Filesystem discovery of Hermes profiles for the Cost page.

Each Hermes profile has its own state.db: the root profile at
HERMES_DATA_DIR/state.db, named profiles at HERMES_DATA_DIR/profiles/<name>/state.db.
The db file is the partition -- do NOT filter by sessions.profile_name (it is
NULL for almost every row in the root db).
"""
from pathlib import Path

from app.config import HERMES_DATA_DIR

_ROOT_KEY = "default"
_ROOT_LABEL = "Hermes-Agent"

# The Cost page's "every bot" sentinel -- a profile key that means "don't filter,
# merge across all profiles". Routes compare `?profile=` against this.
ALL = "all"


def list_profiles() -> list[dict]:
    """[{key, label, data_dir}] -- root first, then named profiles alphabetically.
    Empty when HERMES_DATA_DIR has no state.db (local dev, no mount)."""
    root = Path(HERMES_DATA_DIR)
    out: list[dict] = []
    if (root / "state.db").is_file():
        out.append({"key": _ROOT_KEY, "label": _ROOT_LABEL, "data_dir": str(root)})
    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir(), key=lambda p: p.name):
            if child.name in (_ROOT_KEY, ALL):
                continue
            if child.is_dir() and (child / "state.db").is_file():
                out.append({"key": child.name, "label": child.name,
                            "data_dir": str(child)})
    return out


def resolve(key: str) -> str | None:
    """data_dir for a profile key, or None if unknown."""
    for p in list_profiles():
        if p["key"] == key:
            return p["data_dir"]
    return None
