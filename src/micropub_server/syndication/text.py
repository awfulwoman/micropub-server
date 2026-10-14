"""Turn a post into the text of a short status."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

URL_LENGTH = 23  # Mastodon counts every URL as this many characters
_URL = re.compile(r"https?://\S+")
_WORD_SPLIT = re.compile(r"[^0-9A-Za-z]+")


class _Plain(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "ul", "ol", "pre"}:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            text = "".join(self._link_text).strip()
            self.parts.append(
                self._href if not text or text == self._href else f"{text} {self._href}"
            )
            self._href = None
        elif tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "pre"}:
            self.parts.append("\n\n")

    def handle_data(self, data):
        (self._link_text if self._href is not None else self.parts).append(data)


def plain_text(content: Any) -> str:
    """Plain text from a post's content (JF2 dict with html/text, or a string)."""
    if isinstance(content, dict):
        content = content.get("html") or content.get("text") or ""
    if not content:
        return ""
    parser = _Plain()
    parser.feed(str(content))
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def counted_length(text: str, url_length: int | None = URL_LENGTH) -> int:
    """Length as the network counts it. Mastodon counts every URL as 23; Bluesky counts them in full."""
    return len(_URL.sub("x" * url_length, text)) if url_length is not None else len(text)


def truncate(text: str, room: int, url_length: int | None = URL_LENGTH) -> str:
    """Cut at a word boundary so the text, plus an ellipsis, fits in room characters."""
    if counted_length(text, url_length) <= room:
        return text
    words = text.split(" ")
    while words and counted_length(" ".join(words), url_length) + 1 > room:
        words.pop()
    return (" ".join(words).rstrip(" ,.;:-\n") + "…") if words else "…"


def hashtags(categories: Any, room: int) -> str:
    """CamelCase hashtags, in order, as many as fit in room characters."""
    seen: set[str] = set()
    tags: list[str] = []
    for category in categories if isinstance(categories, list) else [categories]:
        words = [w for w in _WORD_SPLIT.split(str(category or "")) if w]
        tag = "".join(w[0].upper() + w[1:] for w in words)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append("#" + tag)
    kept: list[str] = []
    for tag in tags:
        candidate = " ".join([*kept, tag])
        if len(candidate) > room:
            break
        kept.append(tag)
    return " ".join(kept)
