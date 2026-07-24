# tests/test_subject_line.py
import asyncio
import app.subject_line as subject_line

def test_truncate_fallback_leaves_short_summary_unchanged():
    assert subject_line.truncate_fallback("short summary here") == "short summary here"

def test_truncate_fallback_truncates_at_ten_words_by_default():
    summary = "one two three four five six seven eight nine ten eleven"
    assert subject_line.truncate_fallback(summary) == (
        "one two three four five six seven eight nine ten..."
    )

class _FakeTextBlock:
    def __init__(self, text):
        self.text = text

class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]

class _FakeMessages:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error

    async def create(self, **kwargs):
        if self._error:
            raise self._error
        return _FakeMessage(self._text)

class _FakeAnthropic:
    def __init__(self, text=None, error=None):
        self.messages = _FakeMessages(text=text, error=error)

def test_generate_subject_line_uses_model_output(monkeypatch):
    monkeypatch.setattr(
        subject_line.anthropic, "AsyncAnthropic",
        lambda **kwargs: _FakeAnthropic(text="Sarah's career pivot"),
    )
    result = asyncio.run(subject_line.generate_subject_line("Sarah is considering a pivot"))
    assert result == "Sarah's career pivot"

def test_generate_subject_line_falls_back_on_api_error(monkeypatch):
    monkeypatch.setattr(
        subject_line.anthropic, "AsyncAnthropic",
        lambda **kwargs: _FakeAnthropic(error=RuntimeError("rate limited")),
    )
    summary = "one two three four five six seven eight nine ten eleven"
    result = asyncio.run(subject_line.generate_subject_line(summary))
    assert result == subject_line.truncate_fallback(summary)
