"""Prove REPORT-04: messages > 4096 chars are split at paragraph boundaries."""
from __future__ import annotations
import pytest
from src.reports.splitter import split_html_message


def test_short_message_not_split():
    assert split_html_message("hello world") == ["hello world"]


def test_exactly_limit_not_split():
    text = "x" * 4096
    assert split_html_message(text) == [text]


def test_paragraph_boundary_split():
    text = "first paragraph\n\nsecond paragraph"
    parts = split_html_message(text, limit=20)
    assert len(parts) == 2
    assert parts[0] == "first paragraph"
    assert parts[1] == "second paragraph"


def test_single_newline_fallback():
    """Falls back to single newline when no double-newline within limit."""
    text = "line one\nline two\nline three"
    parts = split_html_message(text, limit=12)
    assert len(parts) >= 2
    assert all(len(p) <= 12 for p in parts)


def test_hard_split_fallback():
    """Hard character split when no newline within limit."""
    text = "abcdefghij"
    parts = split_html_message(text, limit=3)
    assert "".join(parts) == "abcdefghij"
    assert all(len(p) <= 3 for p in parts)


def test_empty_string():
    assert split_html_message("") == [""]


def test_custom_limit():
    text = "hello world"
    parts = split_html_message(text, limit=5)
    assert len(parts) >= 2
    assert all(len(p) <= 5 for p in parts)
