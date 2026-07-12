# app/fingerprint.py
import hashlib
import re
from urllib.parse import parse_qsl, urlencode

_TRACKING_PARAMS = {
    "si", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid",
}

def _normalize_url(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)      # scheme-agnostic
    if u.startswith("www."):
        u = u[4:]                          # www. vs bare domain is the same site
    u = u.rstrip("/")                      # ignore trailing slash
    base, sep, query = u.partition("?")
    if sep:
        pairs = sorted(
            (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k not in _TRACKING_PARAMS
        )
        u = f"{base}?{urlencode(pairs)}" if pairs else base
    return u

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

def content_fingerprint(*, source_url: str | None, raw_text: str) -> str:
    """Stable dedup key. Prefer the normalized URL; fall back to normalized text.

    Same link (or same text) -> same fingerprint -> deduped on save. URL
    normalization strips scheme, www., trailing slash, and known per-share
    tracking params (YouTube's `si`, UTM params, fbclid/gclid) so the same
    content forwarded twice on WhatsApp still dedupes even though each share
    link carries a different tracking token.
    """
    basis = _normalize_url(source_url) if source_url else _normalize_text(raw_text)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
