"""HTML message splitter for Telegram 4096-character limit.

REPORT-04: Auto-splits messages at paragraph boundaries; hard-splits if no boundary found.
CLAUDE.md pitfall: Telegram hard-truncates or errors on messages > 4096 chars.
"""
from __future__ import annotations

_HTML_LIMIT = 4096


def split_html_message(text: str, limit: int = _HTML_LIMIT) -> list[str]:
    """Split a long HTML-formatted string at paragraph boundaries (double-newline).

    Falls back to single-newline split, then to hard character split if no newline found.
    NOTE: Does not attempt to close/reopen HTML tags across boundaries — keep block-level
    tags (e.g. <b>header</b>) short enough that they do not span a split boundary.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    while len(text) > limit:
        split_at = text.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts
