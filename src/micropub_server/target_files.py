"""Add link-target facts to the front matter of a post file, changing nothing else in it.

For backfilling a site checkout directly (scripts/backfill_targets.py), without going through the
server. The new lines go in as text right after the `like_of` / `quote_of` / `bookmark_of` line, so
the diff is only the added keys.
"""

from __future__ import annotations

import io
import re

from ruamel.yaml import YAML

from .target_meta import KINDS

_SECTIONS = {"likes": "like", "quotes": "quote", "bookmarks": "bookmark"}
_ORDER = ("title", "author", "text", "oembed")


def kind_of_path(path: str) -> str | None:
    """`like`, `quote` or `bookmark` for a file under that section of content/, else None."""
    parts = path.split("/")
    if "content" in parts:
        parts = parts[parts.index("content") + 1 :]
    return _SECTIONS.get(parts[0]) if parts else None


def _dump(key: str, value: str) -> str:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 10_000
    out = io.StringIO()
    yaml.dump({key: value}, out)
    return out.getvalue()


def add_to_front_matter(text: str, kind: str, found: dict[str, str]) -> str | None:
    """The file with the new keys added, or None if there is nothing to do or nowhere to put them."""
    if not found or not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    lines = text[4 : end + 1].splitlines(keepends=True)
    if any(re.match(rf"{kind}_({'|'.join(KINDS[kind])})\s*:", line) for line in lines):
        return None  # something is there already: never overwrite
    target = next((n for n, line in enumerate(lines) if re.match(rf"{kind}_of\s*:", line)), None)
    if target is None:
        return None
    j = target + 1
    while j < len(lines) and lines[j][:1] in {" ", "\t"}:  # a value that runs over several lines
        j += 1
    wanted = [(f"{kind}_{n}", found[f"{kind}-{n}"]) for n in _ORDER if f"{kind}-{n}" in found]
    if not wanted:
        return None
    added = "".join(_dump(key, value) for key, value in wanted)
    return "---\n" + "".join(lines[:j]) + added + "".join(lines[j:]) + text[end + 1 :]
