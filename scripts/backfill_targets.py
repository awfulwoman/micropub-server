"""Add title, author and embed of each liked, quoted or bookmarked page to a local site checkout.

    uv run python scripts/backfill_targets.py ../awfulwoman.com --type like --dry-run
    uv run python scripts/backfill_targets.py ../awfulwoman.com --type like

Edits the files in place (only adding lines) so the result is an ordinary git diff to review and
commit. Posts that already have any of the keys are skipped. After pushing, load the changes into
the server's database with `micropub-server import --source <checkout>` on the host.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ruamel.yaml import YAML

from micropub_server.target_files import add_to_front_matter, kind_of_path
from micropub_server.target_meta import fetch_target_meta


def _target(text: str, kind: str) -> str:
    if not text.startswith("---\n") or text.find("\n---\n", 3) == -1:
        return ""
    front = YAML(typ="safe").load(text[4 : text.find("\n---\n", 3) + 1]) or {}
    value = front.get(f"{kind}_of")
    return value if isinstance(value, str) else ""


async def main(args: argparse.Namespace) -> int:
    root = Path(args.site)
    files = sorted(
        p
        for p in (root / "content").rglob("*.md")
        if p.name != "_index.md" and kind_of_path(p.relative_to(root).as_posix()) == args.type
    )
    done = nothing = skipped = 0
    for n, path in enumerate(files, 1):
        text = path.read_text()
        url = _target(text, args.type)
        if not url:
            skipped += 1
            continue
        if add_to_front_matter(text, args.type, {f"{args.type}-title": "x"}) is None:
            skipped += 1  # already has keys (or no target line)
            continue
        found = (await fetch_target_meta(url)).as_props(args.type)
        updated = add_to_front_matter(text, args.type, found)
        label = path.relative_to(root).as_posix()
        if updated is None:
            nothing += 1
            print(f"[{n}/{len(files)}] nothing found  {label}  {url}", flush=True)
        else:
            done += 1
            summary = ", ".join(f"{k.split('-')[1]}={v[:40]!r}" for k, v in found.items())
            print(f"[{n}/{len(files)}] {label}  {summary}", flush=True)
            if not args.dry_run:
                path.write_text(updated)
        await asyncio.sleep(args.delay)
    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {done}, nothing found {nothing}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("site", help="a checkout of the site repo")
    parser.add_argument("--type", required=True, choices=["like", "quote", "bookmark"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between fetches")
    sys.exit(asyncio.run(main(parser.parse_args())))
