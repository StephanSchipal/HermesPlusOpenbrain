# tests/test_keywords.py
from app.keywords import normalize_keywords

def test_trims_and_drops_blanks():
    assert normalize_keywords(["  ai ", "", "  ", "memory"]) == ["ai", "memory"]

def test_dedupes_case_insensitively_preserving_first():
    assert normalize_keywords(["AI", "ai", "Memory"]) == ["AI", "Memory"]

def test_caps_at_max_but_does_not_force_exactly_five():
    out = normalize_keywords(["a", "b", "c"])  # fewer than 5 is fine ("around 5")
    assert out == ["a", "b", "c"]
    many = normalize_keywords([f"k{i}" for i in range(20)])
    assert len(many) == 8  # capped, not padded
