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


def _dump(key: str, value: str | dict) -> str:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 10_000
    yaml.sort_base_mapping_type_on_output = False  # keep file, alt, width, height in order
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


def card_keys(kind: str) -> tuple[str, ...]:
    return tuple(f"{kind}_{name}" for name in ("site", "description", "image"))


def _after_target(lines: list[str], kind: str) -> int | None:
    """Where new card lines go: after `<kind>_of` and the title, author and card keys that follow."""
    target = next((n for n, line in enumerate(lines) if re.match(rf"{kind}_of\s*:", line)), None)
    if target is None:
        return None
    j = _block_end(lines, target)
    after = rf"{kind}_(title|author|site|description|image|card_archived)\s*:"
    while j < len(lines) and re.match(after, lines[j]):
        j = _block_end(lines, j)
    return j


def set_card_marker(text: str, kind: str, result: str | None, checked: str = "") -> str | None:
    """The post with its `<kind>_card` marker set (`gone` or `none`) or, if result is None, removed.

    Whatever else is in the file stays byte for byte. None if there is no front matter or no
    `<kind>_of` line to put it after.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    lines = text[4 : end + 1].splitlines(keepends=True)
    at = next((n for n, line in enumerate(lines) if re.match(rf"{kind}_card\s*:", line)), None)
    if at is not None:
        del lines[at : _block_end(lines, at)]
    else:
        at = _after_target(lines, kind)
        if at is None:
            return None
    if result:
        lines[at:at] = [f"{kind}_card:\n", f"  result: {result}\n", f"  checked: {checked}\n"]
    return "---\n" + "".join(lines) + text[end + 1 :]


def add_card_to_front_matter(
    text: str, card: dict[str, str | dict], kind: str = "bookmark"
) -> str | None:
    """The post with the link card keys added, or None if there is nothing to do.

    The keys (`<kind>_site`, `<kind>_description`, `<kind>_image`, the last a mapping, then
    `<kind>_card_archived`) go after `<kind>_of`, `<kind>_title` and `<kind>_author`. A post that
    has any card key is never touched.
    """
    keys = card_keys(kind)
    wanted = [(key, card[key]) for key in (*keys, f"{kind}_card_archived") if card.get(key)]
    if not any(card.get(key) for key in keys):
        return None
    if not wanted or not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    lines = text[4 : end + 1].splitlines(keepends=True)
    if any(re.match(rf"({'|'.join(keys)})\s*:", line) for line in lines):
        return None
    j = _after_target(lines, kind)
    if j is None:
        return None
    added = "".join(_dump(key, value) for key, value in wanted)
    return "---\n" + "".join(lines[:j]) + added + "".join(lines[j:]) + text[end + 1 :]


def add_image_to_front_matter(text: str, image: dict, kind: str = "bookmark") -> str | None:
    """The post with `<kind>_image` added after its card text, or None if it has an image already.

    The rest of the card is left as it is.
    """
    if not text.startswith("---\n") or (end := text.find("\n---\n", 3)) == -1:
        return None
    lines = text[4 : end + 1].splitlines(keepends=True)
    key = f"{kind}_image"
    if any(re.match(rf"{key}\s*:", line) for line in lines):
        return None
    j = _after_target(lines, kind)
    for i, line in enumerate(lines):
        if re.match(rf"({kind}_site|{kind}_description)\s*:", line):
            j = _block_end(lines, i)
    if j is None:
        return None
    return "---\n" + "".join(lines[:j]) + _dump(key, image) + "".join(lines[j:]) + text[end + 1 :]


def _block_end(lines: list[str], start: int) -> int:
    """The index after the key at `start` and the lines that continue its value."""
    j = start + 1
    while j < len(lines) and lines[j][:1] in {" ", "\t"}:
        j += 1
    return j
