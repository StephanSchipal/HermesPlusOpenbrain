# tests/test_fingerprint.py
from app.fingerprint import content_fingerprint

def test_same_url_same_fingerprint_regardless_of_case_or_trailing_slash():
    a = content_fingerprint(source_url="https://YouTube.com/watch?v=abc/", raw_text="x")
    b = content_fingerprint(source_url="https://youtube.com/watch?v=abc", raw_text="y")
    assert a == b  # url normalized; raw_text ignored when url present

def test_falls_back_to_raw_text_when_no_url():
    a = content_fingerprint(source_url=None, raw_text="  Hello World  ")
    b = content_fingerprint(source_url=None, raw_text="hello world")
    assert a == b  # text normalized (trim + lowercase + collapse spaces)

def test_different_content_differs():
    a = content_fingerprint(source_url=None, raw_text="note one")
    b = content_fingerprint(source_url=None, raw_text="note two")
    assert a != b

def test_is_hex_sha256():
    fp = content_fingerprint(source_url=None, raw_text="anything")
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)
