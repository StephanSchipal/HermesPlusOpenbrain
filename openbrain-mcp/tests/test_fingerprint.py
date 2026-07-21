# tests/test_fingerprint.py
from app.fingerprint import content_fingerprint, content_fingerprint_debug

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

def test_strips_known_tracking_params():
    a = content_fingerprint(source_url="https://youtu.be/abc123?si=XYZ789", raw_text="x")
    b = content_fingerprint(source_url="https://youtu.be/abc123", raw_text="y")
    assert a == b  # YouTube share links append a per-share ?si= token

def test_strips_www_prefix():
    a = content_fingerprint(source_url="https://www.youtube.com/watch?v=abc", raw_text="x")
    b = content_fingerprint(source_url="https://youtube.com/watch?v=abc", raw_text="y")
    assert a == b

def test_different_urls_still_differ_after_normalization():
    a = content_fingerprint(source_url="https://youtu.be/abc123?si=XYZ789", raw_text="x")
    b = content_fingerprint(source_url="https://youtu.be/def456?si=XYZ789", raw_text="x")
    assert a != b  # stripping tracking params must not cause distinct content to collide

def test_debug_matches_content_fingerprint():
    kwargs = dict(source_url="https://youtu.be/abc123?si=XYZ789", raw_text="x")
    assert content_fingerprint_debug(**kwargs)["fingerprint"] == content_fingerprint(**kwargs)

def test_debug_reports_url_basis_when_url_present():
    result = content_fingerprint_debug(source_url="https://WWW.Example.com/page/", raw_text="ignored")
    assert result["basis_source"] == "url"
    assert result["normalized_basis"] == "example.com/page"

def test_debug_reports_text_basis_when_no_url():
    result = content_fingerprint_debug(source_url=None, raw_text="  Hello World  ")
    assert result["basis_source"] == "text"
    assert result["normalized_basis"] == "hello world"
