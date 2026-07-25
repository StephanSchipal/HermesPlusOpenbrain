# app/subject_line.py
"""Derives a short subject line from a capture's summary by truncation.
Summaries are already concise, LLM-authored text at capture time (written
by Hermes when the note is saved) -- a live model call to re-summarize
them for the grid would just be re-deriving what's already there."""

def make_subject_line(summary: str, max_words: int = 10) -> str:
    words = summary.split()
    if len(words) <= max_words:
        return summary
    return " ".join(words[:max_words]) + "..."
