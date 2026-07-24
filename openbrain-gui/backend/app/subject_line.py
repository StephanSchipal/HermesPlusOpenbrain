# app/subject_line.py
"""Generates a short subject line from a capture's summary using Claude
Haiku, falling back to a truncation heuristic on any failure -- one
slow/failed row must not block the rest of a search's results (design
spec section 7, "Error handling")."""
import anthropic
from app.config import ANTHROPIC_API_KEY, SUBJECT_LINE_MODEL

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
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=SUBJECT_LINE_MODEL,
            max_tokens=30,
            messages=[{"role": "user", "content": _PROMPT.format(summary=summary)}],
        )
        text = response.content[0].text.strip()
        return text or truncate_fallback(summary)
    except Exception:
        return truncate_fallback(summary)
