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
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from ruamel.yaml import YAML

from .auth.client import FetchError, Resolver, guarded_client, system_resolver
from .card_image import CARD_FILENAME, CardImage, fetch_card, process_card
from .config import MediaCard
from .target_files import add_card_to_front_matter, card_keys, set_card_marker
from .target_meta import (
    _TWITTER_HOSTS,
    TOTAL_TIMEOUT,
    TargetMeta,
    _clean,
    _fetch,
    _get,
    extract_page,
)

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
    skipped: int = 0  # already have keys or a marker, or out of range
    retry_urls: list[str] = field(default_factory=list)
    changes: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class _Post:
    path: Path
    text: str
    front: dict
    url: str
    day: str  # YYYY-MM-DD, or ""

    def marker(self, kind: str) -> str:
        value = self.front.get(f"{kind}_card")
        return str(value.get("result", "")) if isinstance(value, dict) else ""

    def has_card(self, kind: str) -> bool:
        return any(self.front.get(key) for key in card_keys(kind))


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


def _in_range(post: _Post, since: int | None, before: str | None) -> bool:
    if since is None and before is None:
        return True
    if not post.day:
        return False
    return (since is None or int(post.day[:4]) >= since) and (before is None or post.day < before)


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
        if (
            post.has_card(kind)
            or (post.marker(kind) and not recheck)
            or (kind == "like" and post.front.get("like_oembed"))
            or not _in_range(post, since, before)
        ):
            report.skipped += 1
            continue
        if limit is not None and tried >= limit:
            break
        tried += 1
        await throttle.wait()
        outcome, meta = await probe(post.url, kind, resolver)
        if outcome == "retry":
            report.retry += 1
            report.retry_urls.append(post.url)
        elif outcome == "embed":
            report.embed += 1  # the embed backfill (backfill-targets) owns this one
        elif outcome in ("gone", "none"):
            _mark(report, root, post, kind, outcome, dry_run, today)
        else:
            fetched = None
            if image and meta.image_url:
                await throttle.wait()
                fetched = await fetch_card(meta.image_url, resolver, card)
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
        if (
            post.marker(kind) != "gone"
            or post.has_card(kind)
            or not post.day
            or not _in_range(post, since, before)
        ):
            report.skipped += 1
            continue
        if limit is not None and tried >= limit:
            break
        tried += 1
        try:
            async with asyncio.timeout(ARCHIVE_TIMEOUT):
                outcome, meta, props_extra, fetched = await _archived(
                    post, kind, card, image, resolver, throttle
                )
        except (FetchError, httpx.HTTPError, ValueError, TimeoutError):
            outcome, meta, props_extra, fetched = "retry", None, {}, None
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


async def _archived(post, kind, card, image, resolver, throttle):
    """(outcome, meta, extra props, image) from the nearest Wayback capture of one post."""
    async with guarded_client(resolver, timeout=10, follow_redirects=False) as client:
        await throttle.wait()
        found = await _wayback_lookup(client, resolver, post.url, post.day)
        if found is None:
            return "none", None, {}, None
        stamp, snapshot = found
        await throttle.wait()
        response = await _get(client, _wayback(stamp, post.url), resolver, "text/html")
    if response.status_code in (403, 429) or response.status_code >= 500:
        return "retry", None, {}, None
    if response.status_code != 200:
        return "none", None, {}, None
    meta = extract_page(response.text[:500_000], post.url)  # the original URL: relative paths
    if not (meta.description or meta.image_url):
        return "none", None, {}, None
    fetched = None
    if image and meta.image_url:
        await throttle.wait()
        fetched = await fetch_card(_wayback(stamp, meta.image_url), resolver, card)
    return "found", meta, {f"{kind}_card_archived": snapshot}, fetched
