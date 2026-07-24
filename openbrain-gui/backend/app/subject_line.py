# app/subject_line.py
"""Generates a short subject line from a capture's summary using Claude
Haiku, falling back to a truncation heuristic on any failure -- one
slow/failed row must not block the rest of a search's results (design
spec section 7, "Error handling")."""
import logging

import anthropic
from app.config import ANTHROPIC_API_KEY, SUBJECT_LINE_MODEL

logger = logging.getLogger(__name__)

_PROMPT = (
    "Write a short, plain subject line (under 8 words, no quotes, no "
    "trailing period) that captures the essence of this note:\n\n{summary}"
)

def truncate_fallback(summary: str, max_words: int = 10) -> str:
    words = summary.split()
    if len(words) <= max_words:
        return summary
    return " ".join(words[:max_words]) + "..."

async def generate_subject_line(summary: str) -> str:
    try:
        # Explicit short timeout: this runs once per search-result row in a
        # sequential loop (up to DEFAULT_SEARCH_K rows), so the SDK's default
        # of a 10-minute timeout with retries could stall the whole endpoint
        # for far longer than the fallback path is meant to allow.
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=15.0)
        response = await client.messages.create(
            model=SUBJECT_LINE_MODEL,
            max_tokens=30,
            messages=[{"role": "user", "content": _PROMPT.format(summary=summary)}],
        )
        text = response.content[0].text.strip()
        return text or truncate_fallback(summary)
    except Exception as exc:
        logger.warning("subject-line generation failed, falling back to truncation: %s", exc)
        return truncate_fallback(summary)
