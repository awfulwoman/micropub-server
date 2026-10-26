"""Backfill link cards on a site checkout (spec 16, `backfill-cards --checkout`).

Edits files in place, adding keys and files and nothing else: no commits, no database, no network.
Only the `scraped` step exists so far: it turns the old importer's `scraped/opengraph.json` and
`scraped/files/<image>` into the card keys and `card.webp`, then removes `scraped/`.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .card_image import CARD_FILENAME, process_card
from .config import MediaCard
from .target_files import add_card_to_front_matter
from .target_meta import _clean

STEPS = ("scraped",)
MAX_SITE_CHARS = 100
MAX_DESCRIPTION_CHARS = 300


@dataclass
class Report:
    converted: int = 0  # posts that got card keys (or would, in a dry run)
    skipped: int = 0  # already have card keys, or nothing usable in the json
    no_image: int = 0  # of the converted, those that got text keys only
    changes: list[tuple[str, dict]] = field(default_factory=list)


def _image(bundle: Path, og: dict, card: MediaCard):
    """The processed old image, or None if there was none or it is missing or unusable."""
    image = og.get("image")
    name = image.get("filename") if isinstance(image, dict) else None
    if not isinstance(name, str) or not name:
        return None
    path = bundle / "scraped/files" / Path(name).name
    try:
        return process_card(path.read_bytes(), card)
    except OSError:
        return None


def backfill_scraped(
    root: Path, card: MediaCard, dry_run: bool = False, kind: str = "bookmark"
) -> Report:
    """Convert every bundle under root/content/<kind>s that has a `scraped/` directory."""
    report = Report()
    for og_path in sorted((root / f"content/{kind}s").rglob("scraped/opengraph.json")):
        bundle = og_path.parent.parent
        post = bundle / "index.md"
        try:
            og = json.loads(og_path.read_text())
            text = post.read_text()
        except (OSError, ValueError):
            report.skipped += 1
            continue
        if not isinstance(og, dict):
            report.skipped += 1
            continue
        image = _image(bundle, og, card)
        props: dict[str, str | dict] = {
            f"{kind}_site": _clean(og.get("siteName"), MAX_SITE_CHARS),
            f"{kind}_description": _clean(og.get("description"), MAX_DESCRIPTION_CHARS),
        }
        if image:  # alt is empty: the image sits next to the title, inside the same link
            props[f"{kind}_image"] = {
                "file": image.filename,
                "alt": "",
                "width": image.width,
                "height": image.height,
            }
        updated = add_card_to_front_matter(text, props, kind)
        if updated is None:
            report.skipped += 1
            continue
        report.converted += 1
        report.no_image += image is None
        report.changes.append(
            (post.relative_to(root).as_posix(), {k: v for k, v in props.items() if v})
        )
        if dry_run:
            continue
        if image:
            (bundle / CARD_FILENAME).write_bytes(image.content)
        post.write_text(updated)
        shutil.rmtree(bundle / "scraped")
    return report
