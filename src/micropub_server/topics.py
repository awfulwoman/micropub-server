"""Topics: a small, fixed set of themes the site defines in data/topics.yaml (spec 15).

The map says which tags imply each topic. A post gets at most three topics: the ones with the most
matching tags, with the order of the map breaking ties.
"""

from __future__ import annotations

import re
from collections import Counter

from ruamel.yaml import YAML

MAX_TOPICS = 3


def load_topic_map(data: object) -> dict[str, list[str]]:
    """Topic key -> its tags, lowercased, in the file's order. Entries that are not mappings are skipped."""
    if not isinstance(data, dict):
        return {}
    return {
        str(key): [str(t).lower() for t in (entry.get("tags") or [])]
        for key, entry in data.items()
        if isinstance(entry, dict)
    }


def topics_for_tags(tags: list[str], topic_map: dict[str, list[str]]) -> list[str]:
    """Up to MAX_TOPICS topic keys for these tags, best match first."""
    wanted = {str(t).lower() for t in tags}
    order = list(topic_map)
    counts = Counter({k: len(wanted.intersection(v)) for k, v in topic_map.items()})
    hits = [k for k in order if counts[k]]
    return sorted(hits, key=lambda k: (-counts[k], order.index(k)))[:MAX_TOPICS]


def _front_matter(text: str) -> tuple[list[str], int] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    return text[4 : end + 1].splitlines(keepends=True), end


def tags_of(text: str) -> list[str]:
    """The `tags` in a post file's front matter, as strings."""
    found = _front_matter(text)
    if found is None:
        return []
    tags = (YAML(typ="safe").load("".join(found[0])) or {}).get("tags") or []
    return [str(t) for t in ([tags] if isinstance(tags, str) else tags)]


def add_topics(text: str, topics: list[str]) -> str | None:
    """The file with a `topics` list added after `tags` (or at the end of the front matter).

    None if there are no topics, no front matter, or the post has topics already: never overwrite.
    """
    found = _front_matter(text)
    if not topics or found is None:
        return None
    lines, end = found
    if any(re.match(r"topics\s*:", line) for line in lines):
        return None
    j = next((n for n, line in enumerate(lines) if re.match(r"tags\s*:", line)), None)
    if j is None:
        j = len(lines)
    else:
        j += 1
        while j < len(lines) and lines[j][:1] in {" ", "\t", "-"}:  # the tags' own lines
            j += 1
    added = "topics:\n" + "".join(f"  - {t}\n" for t in topics)
    return "---\n" + "".join(lines[:j]) + added + "".join(lines[j:]) + text[end + 1 :]
