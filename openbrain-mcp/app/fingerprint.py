# app/fingerprint.py
import hashlib
import re

def _normalize_url(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)      # scheme-agnostic
    u = u.rstrip("/")                      # ignore trailing slash
    return u

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

def content_fingerprint(*, source_url: str | None, raw_text: str) -> str:
    """Stable dedup key. Prefer the normalized URL; fall back to normalized text.

    Same link (or same text) -> same fingerprint -> deduped on save.
    """
    basis = _normalize_url(source_url) if source_url else _normalize_text(raw_text)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
