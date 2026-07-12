# app/keywords.py
def normalize_keywords(keywords: list[str], max_len: int = 8) -> list[str]:
    """Trim, drop blanks, dedupe case-insensitively, cap at max_len.

    Intentionally does NOT pad to a fixed count — the spec wants "around 5".
    """
    seen: set[str] = set()
    out: list[str] = []
    for k in keywords or []:
        k = (k or "").strip()
        if not k:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(k)
    return out[:max_len]
