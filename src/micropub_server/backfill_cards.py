"""Backfill link cards on a site checkout (spec 16, `backfill-cards --checkout`).

Edits files in place, adding keys and files and nothing else: no commits, no database, no GitHub.
Three steps:

- `scraped` turns the old importer's `scraped/opengraph.json` and `scraped/files/<image>` into the
  card keys and `card.webp`, then removes `scraped/`. No network.
- `live` fetches each page that has no card yet, as on create, and remembers what it found in a
  `<kind>_card` marker in the post's own front matter.
- `archive` asks the Wayback Machine for the pages the marker says are gone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from ruamel.yaml import YAML

from .auth.client import FetchError, Resolver, guarded_client, system_resolver
from .card_image import CARD_FILENAME, CardImage, fetch_card, process_card
from .config import MediaCard
from .target_enrich import Card, _target_url
from .target_files import add_card_to_front_matter, card_keys, set_card_marker
from .target_meta import (
    _TWITTER_HOSTS,
    TARGET_PROPS,
    TOTAL_TIMEOUT,
    TargetMeta,
    _clean,
    _fetch,
    _get,
    extract_page,
)

if TYPE_CHECKING:
    from .app import AppState

log = logging.getLogger(__name__)
STEPS = ("scraped", "live", "archive")
LIVE_DELAY = 1.0
ARCHIVE_DELAY = 4.0  # the Wayback Machine blocks faster clients
ARCHIVE_TIMEOUT = 30  # it is slow
WAYBACK = "https://archive.org/wayback/available"
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


# --- live and archive ---

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class CardReport:
    found: int = 0  # got card keys (or would, in a dry run)
    gone: int = 0
    none: int = 0
    embed: int = 0  # a like whose page gave an embed: nothing written
    retry: int = 0
    failed: int = 0  # server mode: the edit could not be saved
    skipped: int = 0  # already have keys or a marker, or out of range
    retry_urls: list[str] = field(default_factory=list)
    changes: list[tuple[str, dict]] = field(default_factory=list)


def marker_of(front: dict, kind: str) -> str:
    """The result in a post's `<kind>_card` marker (gone, none), or "". `front` has `_` keys."""
    value = front.get(f"{kind}_card")
    return str(value.get("result", "")) if isinstance(value, dict) else ""


def has_card(front: dict, kind: str) -> bool:
    return any(front.get(key) for key in card_keys(kind))


def wants_live(front: dict, kind: str, recheck: bool = False) -> bool:
    """The skip rules of the live step, for a checkout and the server alike."""
    return not (
        has_card(front, kind)
        or (marker_of(front, kind) and not recheck)
        or (kind == "like" and front.get("like_oembed"))
    )


def wants_archive(front: dict, kind: str, day: str) -> bool:
    """The skip rules of the archive step: a post marked gone, with a date to look up."""
    return marker_of(front, kind) == "gone" and not has_card(front, kind) and bool(day)


@dataclass
class _Post:
    path: Path
    text: str
    front: dict
    url: str
    day: str  # YYYY-MM-DD, or ""

    def marker(self, kind: str) -> str:
        return marker_of(self.front, kind)

    def has_card(self, kind: str) -> bool:
        return has_card(self.front, kind)


def _read(path: Path, kind: str) -> _Post | None:
    try:
        text = path.read_text()
        if not text.startswith("---\n") or (end := text.find("\n---\n", 3)) == -1:
            return None
        front = YAML(typ="safe").load(text[4:end])
    except (OSError, ValueError, Exception):  # a post that does not parse is left alone
        return None
    if not isinstance(front, dict):
        return None
    url = front.get(f"{kind}_of")
    if not isinstance(url, str) or not url:
        return None
    return _Post(path, text, front, url, str(front.get("date") or "")[:10])


def _posts(root: Path, kind: str):
    for path in sorted((root / f"content/{kind}s").rglob("*.md")):
        if path.name != "_index.md" and (post := _read(path, kind)):
            yield post


def in_range(day: str, since: int | None, before: str | None) -> bool:
    if since is None and before is None:
        return True
    if not day:
        return False
    return (since is None or int(day[:4]) >= since) and (before is None or day < before)


def _in_range(post: _Post, since: int | None, before: str | None) -> bool:
    return in_range(post.day, since, before)


class _Throttle:
    """Sleeps `delay` seconds before every request but the first."""

    def __init__(self, delay: float, sleep: Sleep):
        self.delay, self.sleep, self.first = delay, sleep, True

    async def wait(self) -> None:
        if not self.first and self.delay:
            await self.sleep(self.delay)
        self.first = False


# What a live fetch of one page came to: found, gone, none, retry or embed.
def _classify(kind: str, meta: TargetMeta, code: int | None) -> str:
    if meta.oembed and kind == "like":
        return "embed"
    if code in (404, 410):
        return "gone"
    if code is None:
        return "none"
    if code in (403, 429) or code >= 500:
        return "retry"
    if code != 200:
        return "none"
    if not (meta.title or meta.description or meta.image_url):
        return "gone"  # a parked domain answers 200 with nothing in it
    return "found" if (meta.description or meta.image_url) else "none"


async def probe(url: str, kind: str, resolver: Resolver) -> tuple[str, TargetMeta]:
    """Fetch a page as on create but say how it went. Never raises."""
    status: dict = {}
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            meta = await _fetch(url, resolver, status)
    except FetchError as exc:
        text = str(exc)
        if "cannot resolve" in text:
            return "gone", TargetMeta()
        return ("retry" if "redirects" in text else "none"), TargetMeta()
    except httpx.ConnectError:  # refused, or a certificate that does not check out
        return "gone", TargetMeta()
    except (httpx.HTTPError, ValueError, TimeoutError):
        return "retry", TargetMeta()
    outcome = _classify(kind, meta, status.get("code"))
    if outcome == "gone" and (urlsplit(url).hostname or "").removeprefix("www.") in _TWITTER_HOSTS:
        outcome = "none"  # the page is empty without scripts, so a missing embed says nothing
    return outcome, meta


def _card_props(kind: str, meta: TargetMeta, image: CardImage | None) -> dict:
    props: dict = {f"{kind}_site": meta.site, f"{kind}_description": meta.description}
    if image:  # alt is empty: the image sits next to the title, inside the same link
        props[f"{kind}_image"] = {
            "file": image.filename,
            "alt": meta.image_alt,
            "width": image.width,
            "height": image.height,
        }
    return props


def _store(root: Path, post: _Post, text: str, image: CardImage | None) -> None:
    """Write the post; a flat `x.md` that gets an image becomes `x/index.md`."""
    path = post.path
    if image and path.name != "index.md":
        bundle = path.with_suffix("")
        bundle.mkdir(exist_ok=True)
        (bundle / "index.md").write_text(text)
        path.unlink()
        path = bundle / "index.md"
    else:
        path.write_text(text)
    if image:
        (path.parent / CARD_FILENAME).write_bytes(image.content)


def _mark(
    report: CardReport, root: Path, post: _Post, kind: str, result: str, dry_run: bool, today: str
) -> None:
    setattr(report, result, getattr(report, result) + 1)
    updated = set_card_marker(post.text, kind, result, today)
    if updated is None:
        report.skipped += 1
        setattr(report, result, getattr(report, result) - 1)
        return
    report.changes.append((post.path.relative_to(root).as_posix(), {f"{kind}_card": result}))
    if not dry_run:
        post.path.write_text(updated)


def _save_card(
    report: CardReport, root: Path, post: _Post, kind: str, text: str, props: dict,
    image: CardImage | None, dry_run: bool,
) -> bool:  # fmt: skip
    updated = add_card_to_front_matter(text, props, kind)
    if updated is None:
        report.skipped += 1
        return False
    report.found += 1
    report.changes.append(
        (post.path.relative_to(root).as_posix(), {k: v for k, v in props.items() if v})
    )
    if not dry_run:
        _store(root, post, updated, image)
    return True


FetchImage = Callable[[str], Awaitable["CardImage | None"]]  # None: no image to use


def _fetcher(resolver: Resolver, card: MediaCard, image: bool) -> FetchImage | None:
    return (lambda url: fetch_card(url, resolver, card)) if image else None


async def live_outcome(
    url: str, kind: str, resolver: Resolver, fetch_image: FetchImage | None, throttle: _Throttle
) -> tuple[str, TargetMeta, CardImage | None]:
    """Fetch a page and decide: (outcome, what it said, the card image if found). Shared by modes.

    `fetch_image` is None for text only.
    """
    outcome, meta = await probe(url, kind, resolver)
    fetched = None
    if outcome == "found" and fetch_image and meta.image_url:
        await throttle.wait()
        fetched = await fetch_image(meta.image_url)
    return outcome, meta, fetched


async def archive_outcome(
    url: str, day: str, kind: str, resolver: Resolver, fetch_image: FetchImage | None,
    throttle: _Throttle,
) -> tuple[str, TargetMeta | None, dict, CardImage | None]:  # fmt: skip
    """The Wayback Machine for one post: (outcome, meta, extra props, image). Never raises."""
    try:
        async with asyncio.timeout(ARCHIVE_TIMEOUT):
            return await _archived(url, day, kind, resolver, fetch_image, throttle)
    except (FetchError, httpx.HTTPError, ValueError, TimeoutError):
        return "retry", None, {}, None


async def backfill_live(
    root: Path,
    card: MediaCard,
    *,
    kind: str = "bookmark",
    dry_run: bool = False,
    limit: int | None = None,
    since: int | None = None,
    before: str | None = None,
    recheck: bool = False,
    image: bool = True,
    delay: float = LIVE_DELAY,
    sleep: Sleep = asyncio.sleep,
    resolver: Resolver | None = None,
    today: str | None = None,
) -> CardReport:
    """Fetch each post of this kind that has no card yet; keep the outcome in its front matter."""
    resolver = resolver or system_resolver
    today = today or date.today().isoformat()
    report, throttle, tried = CardReport(), _Throttle(delay, sleep), 0
    for post in _posts(root, kind):
        if not wants_live(post.front, kind, recheck) or not _in_range(post, since, before):
            report.skipped += 1
            continue
        if limit is not None and tried >= limit:
            break
        tried += 1
        await throttle.wait()
        outcome, meta, fetched = await live_outcome(
            post.url, kind, resolver, _fetcher(resolver, card, image), throttle
        )
        if outcome == "retry":
            report.retry += 1
            report.retry_urls.append(post.url)
        elif outcome == "embed":
            report.embed += 1  # the embed backfill (backfill-targets) owns this one
        elif outcome in ("gone", "none"):
            _mark(report, root, post, kind, outcome, dry_run, today)
        else:
            text = set_card_marker(post.text, kind, None) or post.text  # --recheck drops the marker
            _save_card(
                report, root, post, kind, text, _card_props(kind, meta, fetched), fetched, dry_run
            )
    return report


async def _wayback_lookup(
    client: httpx.AsyncClient, resolver: Resolver, url: str, day: str
) -> tuple[str, str] | None:
    """(timestamp, snapshot URL) of the capture nearest `day`, or None if there is none."""
    api = httpx.URL(WAYBACK, params={"url": url, "timestamp": day.replace("-", "")})
    response = await _get(client, str(api), resolver, "application/json")
    if response.status_code != 200:
        raise httpx.HTTPStatusError("wayback", request=response.request, response=response)
    closest = (response.json().get("archived_snapshots") or {}).get("closest") or {}
    stamp = str(closest.get("timestamp") or "")
    if not closest.get("available") or not re.fullmatch(r"\d{14}", stamp):
        return None
    if str(closest.get("status", "200")) != "200":
        return None
    return stamp, f"https://web.archive.org/web/{stamp}/{url}"


def _wayback(stamp: str, url: str) -> str:
    return f"https://web.archive.org/web/{stamp}id_/{url}"


async def backfill_archive(
    root: Path,
    card: MediaCard,
    *,
    kind: str = "bookmark",
    dry_run: bool = False,
    limit: int | None = None,
    since: int | None = None,
    before: str | None = None,
    recheck: bool = False,  # accepted for symmetry: archive only ever looks at `gone`
    image: bool = True,
    delay: float = ARCHIVE_DELAY,
    sleep: Sleep = asyncio.sleep,
    resolver: Resolver | None = None,
    today: str | None = None,
) -> CardReport:
    """Try the Wayback Machine for every post marked gone."""
    resolver = resolver or system_resolver
    today = today or date.today().isoformat()
    report, throttle, tried = CardReport(), _Throttle(delay, sleep), 0
    for post in _posts(root, kind):
        if not wants_archive(post.front, kind, post.day) or not _in_range(post, since, before):
            report.skipped += 1
            continue
        if limit is not None and tried >= limit:
            break
        tried += 1
        outcome, meta, props_extra, fetched = await archive_outcome(
            post.url, post.day, kind, resolver, _fetcher(resolver, card, image), throttle
        )
        if outcome == "retry":
            report.retry += 1
            report.retry_urls.append(post.url)
        elif outcome == "none":
            _mark(report, root, post, kind, "none", dry_run, today)
        else:
            props = _card_props(kind, meta, fetched) | props_extra
            text = set_card_marker(post.text, kind, None) or post.text
            _save_card(report, root, post, kind, text, props, fetched, dry_run)
    return report


async def _archived(url, day, kind, resolver, fetch_image, throttle):
    """(outcome, meta, extra props, image) from the nearest Wayback capture of one post."""
    async with guarded_client(resolver, timeout=10, follow_redirects=False) as client:
        await throttle.wait()
        found = await _wayback_lookup(client, resolver, url, day)
        if found is None:
            return "none", None, {}, None
        stamp, snapshot = found
        await throttle.wait()
        response = await _get(client, _wayback(stamp, url), resolver, "text/html")
    if response.status_code in (403, 429) or response.status_code >= 500:
        return "retry", None, {}, None
    if response.status_code != 200:
        return "none", None, {}, None
    meta = extract_page(response.text[:500_000], url)  # the original URL: relative paths
    if not (meta.description or meta.image_url):
        return "none", None, {}, None
    fetched = None
    if fetch_image and meta.image_url:
        await throttle.wait()
        fetched = await fetch_image(_wayback(stamp, meta.image_url))
    return "found", meta, {f"{kind}_card_archived": snapshot}, fetched


# --- through the server (no --checkout) ---


async def backfill_cards_server(
    s: AppState,
    step: str,
    *,
    kind: str = "bookmark",
    dry_run: bool = False,
    limit: int | None = None,
    since: int | None = None,
    before: str | None = None,
    recheck: bool = False,
    image: bool = True,
    delay: float | None = None,
    sleep: Sleep = asyncio.sleep,
    today: str | None = None,
) -> CardReport:
    """`live` or `archive` on the posts of this kind in the database, each saved as an edit.

    The decisions are the checkout mode's (`wants_live`, `live_outcome` ...). One commit each, with
    `card.webp` in it. A dry run fetches and reports but writes nothing. Raises ValueError for
    `scraped`, which needs a checkout.
    """
    from .publish import MicropubError, update_post

    if step not in ("live", "archive"):
        raise ValueError(f"--step {step} needs --checkout: only live and archive run on the server")
    today = today or date.today().isoformat()
    if delay is None:
        delay = LIVE_DELAY if step == "live" else ARCHIVE_DELAY
    resolver, card = s.resolver, s.config.media.card
    image = image and s.config.publication.fetch_target_image

    async def fetch_image(url: str) -> CardImage | None:
        try:
            return await s.image_fetcher(url, resolver, card, TOTAL_TIMEOUT)
        except Exception:  # the card just has no image
            return None

    report, throttle, tried = CardReport(), _Throttle(delay, sleep), 0
    rows = s.db.query(
        "SELECT url, properties, published FROM posts WHERE deleted IS NULL AND post_type = ?"
        " ORDER BY published",
        (kind,),
    )
    for row in rows:
        props = json.loads(row["properties"])
        extras = props.get("front-matter")
        front = {k.replace("-", "_"): v for k, v in props.items()} | (
            extras if isinstance(extras, dict) else {}
        )
        url, day = _target_url(props.get(TARGET_PROPS[kind])), str(row["published"])[:10]
        wanted = (
            wants_live(front, kind, recheck) if step == "live" else wants_archive(front, kind, day)
        )
        if not url or not wanted or not in_range(day, since, before):
            report.skipped += 1
            continue
        if limit is not None and tried >= limit:
            break
        tried += 1
        fetcher = fetch_image if image else None
        if step == "live":
            outcome, meta, fetched = await live_outcome(url, kind, resolver, fetcher, throttle)
            extra: dict = {}
        else:
            outcome, meta, extra, fetched = await archive_outcome(
                url, day, kind, resolver, fetcher, throttle
            )
        if outcome == "retry":
            report.retry += 1
            report.retry_urls.append(url)
            continue
        if outcome == "embed":
            report.embed += 1  # the embed backfill (backfill-targets) owns this one
            continue
        marker = f"{kind}-card"
        if outcome == "found":
            facts = {f"{kind}-site": meta.site, f"{kind}-description": meta.description}
            facts |= {k.replace("_", "-"): v for k, v in extra.items()}  # card-archived
            replace = {k: [v] for k, v in facts.items() if v}
            delete = [marker] if marker_of(front, kind) else None
            changes = {k: v[0] for k, v in replace.items()}
        else:
            replace = {marker: [{"result": outcome, "checked": today}]}
            delete, fetched = None, None
            changes = {marker: outcome}
        report.changes.append((row["url"], changes))
        setattr(report, outcome, getattr(report, outcome) + 1)
        if dry_run:
            continue
        try:
            await update_post(
                s,
                row["url"],
                replace=replace,
                delete=delete,
                card=Card(fetched, meta.image_alt, kind) if fetched else None,
            )
        except MicropubError:
            log.exception("could not update %s", row["url"])
            setattr(report, outcome, getattr(report, outcome) - 1)
            report.failed += 1
    return report
