"""Add topics to the tagged posts of a local site checkout, from its data/topics.yaml (spec 15).

    uv run python scripts/backfill_topics.py ../awfulwoman.com --dry-run
    uv run python scripts/backfill_topics.py ../awfulwoman.com

Edits the files in place (only adding lines) so the result is an ordinary git diff to review and
commit. Posts that already have topics are skipped. After pushing, load the changes into the
server's database with `micropub-server import --source <checkout>` on the host.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ruamel.yaml import YAML

from micropub_server.topics import add_topics, load_topic_map, tags_of, topics_for_tags


def main(args: argparse.Namespace) -> int:
    root = Path(args.site)
    topic_map = load_topic_map(YAML(typ="safe").load((root / "data" / "topics.yaml").read_text()))
    if not topic_map:
        print("data/topics.yaml has no topics", file=sys.stderr)
        return 1
    done = skipped = 0
    per_topic: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    for path in sorted((root / "content").rglob("*.md")):
        if path.name == "_index.md":
            continue
        text = path.read_text()
        tags = tags_of(text)
        if not tags:
            continue
        topics = topics_for_tags(tags, topic_map)
        known = {t for ts in topic_map.values() for t in ts}
        unmapped.update(t.lower() for t in tags if t.lower() not in known)
        updated = add_topics(text, topics)
        if updated is None:
            skipped += 1  # no topic matched, or it has topics already
            continue
        done += 1
        per_topic.update(topics)
        print(f"{path.relative_to(root).as_posix()}  {', '.join(topics)}")
        if not args.dry_run:
            path.write_text(updated)
    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {done}, skipped {skipped}")
    print("per topic: " + ", ".join(f"{k}={v}" for k, v in per_topic.most_common()))
    if unmapped:
        print("tags in no topic: " + ", ".join(sorted(unmapped)))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("site", help="a checkout of the site repo")
    parser.add_argument("--dry-run", action="store_true")
    sys.exit(main(parser.parse_args()))
