# tests/test_subject_line.py
import app.subject_line as subject_line

def test_make_subject_line_leaves_short_summary_unchanged():
    assert subject_line.make_subject_line("short summary here") == "short summary here"

def test_make_subject_line_truncates_at_ten_words_by_default():
    summary = "one two three four five six seven eight nine ten eleven"
    assert subject_line.make_subject_line(summary) == (
        "one two three four five six seven eight nine ten..."
    )
