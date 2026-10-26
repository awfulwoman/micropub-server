"""Fill in a post's facts about its link target (title, author, embed), and backfill old posts."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .card_image import CARD_FILENAME, CardImage
from .target_meta import KINDS, TARGET_PROPS, TOTAL_TIMEOUT
from .util import to_list

if TYPE_CHECKING:
    from .app import AppState

log = logging.getLogger(__name__)


def _target_url(value: Any) -> str:
    first = to_list(value)[0] if to_list(value) else ""
    if isinstance(first, dict):
        first = first.get("url", "")
    return first if isinstance(first, str) else ""


CARD_EXTRAS = ("image", "card", "card-archived")  # a bookmark's card keys beyond KINDS (spec 16)
IMAGE_KEY = "bookmark-image"


def derived_keys(kind: str) -> list[str]:
    names = [*KINDS[kind], *(CARD_EXTRAS if kind == "bookmark" else ())]
    return [f"{kind}-{name}" for name in names]


@dataclass
class Card:
    """A bookmark's downloaded image, waiting to be committed beside its index.md."""

    image: CardImage
    alt: str = ""

    def as_prop(self) -> dict[str, Any]:
        return {
            "file": CARD_FILENAME,
            "alt": self.alt,
            "width": self.image.width,
            "height": self.image.height,
        }


def card_image(props: dict[str, Any]) -> Any:
    """The bookmark-image property, or the same key as an untouched front matter extra."""
    return props.get(IMAGE_KEY) or _extras(props).get(IMAGE_KEY.replace("-", "_"))


def _extras(props: dict[str, Any]) -> dict[str, Any]:
    extras = props.get("front-matter")
    return extras if isinstance(extras, dict) else {}


def has_derived(props: dict[str, Any], kind: str) -> bool:
    return any(k in props or k.replace("-", "_") in _extras(props) for k in derived_keys(kind))


async def lookup_card(s: AppState, kind: str, url: str, *, image: bool = False):
    """The properties for this kind and, if `image`, the card image, from one fetch of the page.

    The image comes from what is left of the 12 second budget. Never raises.
    """
    started = time.monotonic()
    try:
        meta = await s.target_fetcher(url, s.resolver)
        found = meta.as_props(kind)
    except Exception:  # a bug or a hostile page must never stop a post being saved
        log.exception("could not read %s", url)
        return {}, None
    card = None
    left = TOTAL_TIMEOUT - (time.monotonic() - started)
    if image and meta.image_url and left > 0:
        try:
            fetched = await s.image_fetcher(meta.image_url, s.resolver, s.config.media.card, left)
        except Exception:  # the same: the card just has no image
            log.exception("could not fetch the image %s", meta.image_url)
            fetched = None
        card = Card(fetched, meta.image_alt) if fetched else None
    return found, card


async def lookup(s: AppState, kind: str, url: str) -> dict[str, str]:
    """What the target says about itself, as properties for this kind. Never raises."""
    return (await lookup_card(s, kind, url))[0]


def _wants_image(s: AppState, kind: str) -> bool:
    return kind == "bookmark" and s.config.publication.fetch_target_image


async def enrich_props(s: AppState, props: dict[str, Any]) -> Card | None:
    """On create: fetch each link target and fill the gaps the client left.

    Returns a bookmark's card image for the caller to commit; it is not yet in `props`.
    """
    card = None
    if not s.config.publication.fetch_target_meta:
        return card
    for kind, prop in TARGET_PROPS.items():
        url = _target_url(props.get(prop))
        if url:
            found, fetched = await lookup_card(
                s, kind, url, image=_wants_image(s, kind) and IMAGE_KEY not in props
            )
            for key, value in found.items():
                props.setdefault(key, value)
            card = fetched or card
    return card


async def refresh_props(s: AppState, props: dict[str, Any], old: dict[str, Any]) -> Card | None:
    """On edit: only when a link changed, drop what belonged to the old page and fetch the new."""
    card = None
    if not s.config.publication.fetch_target_meta:
        return card
    for kind, prop in TARGET_PROPS.items():
        if props.get(prop) == old.get(prop):
            continue
        for key in derived_keys(kind):
            if props.get(key) == old.get(key):  # unless this very edit set it
                props.pop(key, None)
            _extras(props).pop(key.replace("-", "_"), None)
        url = _target_url(props.get(prop))
        if url:
            found, fetched = await lookup_card(
                s, kind, url, image=_wants_image(s, kind) and IMAGE_KEY not in props
            )
            for key, value in found.items():
                props.setdefault(key, value)
            card = fetched or card
    return card


@dataclass
class BackfillReport:
    updated: int = 0
    skipped: int = 0
    nothing_found: int = 0
    failed: int = 0
    changes: list[tuple[str, dict[str, str]]] = field(default_factory=list)


async def backfill_targets(
    s: AppState, *, dry_run: bool = False, delay: float = 1.0, only_type: str | None = None
) -> BackfillReport:
    """Fetch metadata for existing likes, quotes and bookmarks that have none yet."""
    from .publish import MicropubError, update_post

    report = BackfillReport()
    rows = s.db.query(
        "SELECT url, post_type, properties FROM posts WHERE deleted IS NULL AND post_type IN (?, ?, ?)",
        tuple(KINDS),
    )
    if only_type:
        rows = [row for row in rows if row["post_type"] == only_type]
    fetched = False
    for row in rows:
        kind = row["post_type"]
        props = json.loads(row["properties"])
        url = _target_url(props.get(TARGET_PROPS[kind]))
        if not url or has_derived(props, kind):
            report.skipped += 1
            continue
        if fetched and delay:
            await asyncio.sleep(delay)
        fetched = True
        found = await lookup(s, kind, url)
        if not found:
            report.nothing_found += 1
            continue
        report.changes.append((row["url"], found))
        if dry_run:
            report.updated += 1
            continue
        try:
            await update_post(s, row["url"], replace={k: [v] for k, v in found.items()})
            report.updated += 1
        except MicropubError:
            log.exception("could not update %s", row["url"])
            report.failed += 1
    return report
