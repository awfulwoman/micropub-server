"""Add topics to the posts of a local site checkout, from its data/topics.yaml (spec 15).

Step 1, tagged posts, topics from the map:

    uv run python scripts/backfill_topics.py ../awfulwoman.com --dry-run
    uv run python scripts/backfill_topics.py ../awfulwoman.com

Step 2, untagged notes and articles: an LLM suggests topics into a review file, which you edit and
then apply. Set OLLAMA_URL (and OLLAMA_MODEL). Running --suggest again carries on where it stopped.

    OLLAMA_URL=http://malcolm:11434 uv run python scripts/backfill_topics.py ../awfulwoman.com \\
        --suggest topics-review.yaml
    uv run python scripts/backfill_topics.py ../awfulwoman.com --apply topics-review.yaml

Edits the files in place (only adding lines) so the result is an ordinary git diff to review and
commit. Posts that already have topics are skipped. After pushing, load the changes into the
server's database with `micropub-server import --source <checkout>` on the host.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

import httpx
from ruamel.yaml import YAML

from micropub_server.topics import (
    MAX_TOPICS,
    add_topics,
    load_topic_map,
    parse_suggestion,
    tags_of,
    topics_for_tags,
)

SUGGEST_SECTIONS = ("notes", "posts")
PROMPT = """You sort blog posts into topics. Choose 0 to {n} topics for the post below, only from
this list (key: title). Choose a topic only if the post is clearly about it. Many short posts fit
no topic: then answer [].

{topics}

Reply with a JSON list of keys only, for example ["web"] or [].

Post title: {title}
Post text:
{body}
"""


def _yaml() -> YAML:
    yaml = YAML()
    yaml.allow_unicode = True
    yaml.width = 100
    return yaml


def _posts(root: Path, sections: tuple[str, ...] | None = None):
    base = root / "content"
    dirs = [base / s for s in sections] if sections else [base]
    for d in dirs:
        for path in sorted(d.rglob("*.md")):
            if path.name != "_index.md":
                yield path


def _split(text: str) -> tuple[dict, str]:
    match = re.match(r"---\n(.*?\n)---\n(.*)", text, re.S)
    if not match:
        return {}, text
    return YAML(typ="safe").load(match.group(1)) or {}, match.group(2)


def from_tags(root: Path, topic_map: dict[str, list[str]], dry_run: bool) -> int:
    done = skipped = 0
    per_topic: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    known = {t for ts in topic_map.values() for t in ts}
    for path in _posts(root):
        text = path.read_text()
        tags = tags_of(text)
        if not tags:
            continue
        topics = topics_for_tags(tags, topic_map)
        unmapped.update(t.lower() for t in tags if t.lower() not in known)
        updated = add_topics(text, topics)
        if updated is None:
            skipped += 1  # no topic matched, or it has topics already
            continue
        done += 1
        per_topic.update(topics)
        print(f"{path.relative_to(root).as_posix()}  {', '.join(topics)}")
        if not dry_run:
            path.write_text(updated)
    verb = "would update" if dry_run else "updated"
    print(f"{verb} {done}, skipped {skipped}")
    print("per topic: " + ", ".join(f"{k}={v}" for k, v in per_topic.most_common()))
    if unmapped:
        print("tags in no topic: " + ", ".join(sorted(unmapped)))
    return 0


def suggest(root: Path, topic_map: dict[str, list[str]], titles: dict[str, str], out: Path) -> int:
    url, model = os.environ.get("OLLAMA_URL"), os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")
    if not url:
        print("set OLLAMA_URL", file=sys.stderr)
        return 1
    yaml = _yaml()
    review = (yaml.load(out.read_text()) if out.exists() else None) or {}
    listing = "\n".join(f"- {k}: {titles.get(k, k)}" for k in topic_map)
    todo = []
    for path in _posts(root, SUGGEST_SECTIONS):
        front, body = _split(path.read_text())
        label = path.relative_to(root).as_posix()
        if not front.get("tags") and not front.get("topics") and label not in review:
            todo.append((label, front, body))
    with httpx.Client(timeout=120) as client:
        for n, (label, front, body) in enumerate(todo, 1):
            prompt = PROMPT.format(
                n=MAX_TOPICS,
                topics=listing,
                title=front.get("title") or "(none)",
                body=body.strip()[:3000],
            )
            reply = client.post(
                f"{url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            reply.raise_for_status()
            topics = parse_suggestion(reply.json().get("response", ""), topic_map)
            excerpt = " ".join(body.split())[:200]
            review[label] = {"title": str(front.get("title") or ""), "excerpt": excerpt}
            review[label]["suggested"] = topics
            with out.open("w") as f:  # after every post, so a stopped run loses nothing
                yaml.dump(review, f)
            print(f"[{n}/{len(todo)}] {label}  {', '.join(topics) or '-'}", flush=True)
    print(f"suggested {len(todo)}; review {out}, then run with --apply {out}")
    return 0


def apply(root: Path, topic_map: dict[str, list[str]], review_file: Path, dry_run: bool) -> int:
    review = YAML(typ="safe").load(review_file.read_text()) or {}
    done = skipped = 0
    for label, entry in review.items():
        wanted = [str(t).lower() for t in (entry or {}).get("suggested") or []]
        unknown = [t for t in wanted if t not in topic_map]
        if unknown:
            print(f"{label}: unknown topics {unknown}, skipped", file=sys.stderr)
            skipped += 1
            continue
        if len(wanted) > MAX_TOPICS:
            print(f"{label}: more than {MAX_TOPICS} topics, skipped", file=sys.stderr)
            skipped += 1
            continue
        path = root / label
        updated = add_topics(path.read_text(), list(dict.fromkeys(wanted)))
        if updated is None:
            skipped += 1  # none wanted, or it has topics already
            continue
        done += 1
        if not dry_run:
            path.write_text(updated)
    verb = "would update" if dry_run else "updated"
    print(f"{verb} {done}, skipped {skipped}")
    return 0


def main(args: argparse.Namespace) -> int:
    root = Path(args.site)
    data = YAML(typ="safe").load((root / "data" / "topics.yaml").read_text())
    topic_map = load_topic_map(data)
    if not topic_map:
        print("data/topics.yaml has no topics", file=sys.stderr)
        return 1
    titles = {str(k): str(v.get("title") or k) for k, v in data.items() if isinstance(v, dict)}
    if args.suggest:
        return suggest(root, topic_map, titles, Path(args.suggest))
    if args.apply:
        return apply(root, topic_map, Path(args.apply), args.dry_run)
    return from_tags(root, topic_map, args.dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("site", help="a checkout of the site repo")
    parser.add_argument("--dry-run", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--suggest", metavar="FILE", help="write LLM suggestions to a review file")
    mode.add_argument("--apply", metavar="FILE", help="apply a reviewed suggestions file")
    sys.exit(main(parser.parse_args()))
