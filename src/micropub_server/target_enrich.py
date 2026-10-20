"""Fill in a post's facts about its link target (title, author, embed), and backfill old posts."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .target_meta import KINDS, TARGET_PROPS
from .util import to_list

if TYPE_CHECKING:
    from .app import AppState

log = logging.getLogger(__name__)


def _target_url(value: Any) -> str:
    first = to_list(value)[0] if to_list(value) else ""
    if isinstance(first, dict):
        first = first.get("url", "")
    return first if isinstance(first, str) else ""


def derived_keys(kind: str) -> list[str]:
    return [f"{kind}-{name}" for name in KINDS[kind]]


def _extras(props: dict[str, Any]) -> dict[str, Any]:
    extras = props.get("front-matter")
    return extras if isinstance(extras, dict) else {}


def has_derived(props: dict[str, Any], kind: str) -> bool:
    return any(k in props or k.replace("-", "_") in _extras(props) for k in derived_keys(kind))


async def lookup(s: AppState, kind: str, url: str) -> dict[str, str]:
    """What the target says about itself, as properties for this kind. Never raises."""
    try:
        return (await s.target_fetcher(url, s.resolver)).as_props(kind)
    except Exception:  # a bug or a hostile page must never stop a post being saved
        log.exception("could not read %s", url)
        return {}


async def enrich_props(s: AppState, props: dict[str, Any]) -> None:
    """On create: fetch each link target and fill the gaps the client left."""
    if not s.config.publication.fetch_target_meta:
        return
    for kind, prop in TARGET_PROPS.items():
        url = _target_url(props.get(prop))
        if url:
            for key, value in (await lookup(s, kind, url)).items():
                props.setdefault(key, value)


async def refresh_props(s: AppState, props: dict[str, Any], old: dict[str, Any]) -> None:
    """On edit: only when a link changed, drop what belonged to the old page and fetch the new."""
    if not s.config.publication.fetch_target_meta:
        return
    for kind, prop in TARGET_PROPS.items():
        if props.get(prop) == old.get(prop):
            continue
        for key in derived_keys(kind):
            if props.get(key) == old.get(key):  # unless this very edit set it
                props.pop(key, None)
            _extras(props).pop(key.replace("-", "_"), None)
        url = _target_url(props.get(prop))
        if url:
            for key, value in (await lookup(s, kind, url)).items():
                props.setdefault(key, value)


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
