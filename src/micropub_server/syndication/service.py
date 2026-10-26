"""Share waiting posts to their targets and record the copies (spec 08)."""

from __future__ import annotations

import json
import logging
import posixpath
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..app import AppState
from ..auth.client import FetchError, assert_fetchable, guarded_client
from ..publish import MicropubError, update_post
from .base import NotApplicable, SyndicationError

log = logging.getLogger("micropub_server.syndication")


@dataclass
class Result:
    message: str
    url: str | None = None
    failed: list[str] = field(default_factory=list)
    syndicated: list[str] = field(default_factory=list)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _next_waiting(s: AppState, source_url: str | None):
    if source_url:
        rows = s.db.query(
            "SELECT * FROM posts WHERE deleted IS NULL AND url IN (?, ?)",
            (source_url, source_url.rstrip("/") + "/"),
        )
        if not rows:
            raise MicropubError("invalid_request", f"No post found at {source_url}")
        return rows[0]
    rows = s.db.query(
        "SELECT * FROM posts WHERE deleted IS NULL AND post_status = 'published'"
        " AND COALESCE(json_extract(properties, '$.visibility'), '') != 'private'"
        " AND json_array_length(json_extract(properties, '$.\"mp-syndicate-to\"')) > 0"
        " ORDER BY published ASC, uid ASC LIMIT 1"
    )
    return rows[0] if rows else None


async def _is_live(s: AppState, url: str) -> bool:
    try:
        assert_fetchable(url, resolver=s.resolver)
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            return (await client.get(url)).status_code == 200
    except (FetchError, httpx.HTTPError):
        return False


def _fetcher(s: AppState, post_url: str):
    async def fetch(entry: dict[str, Any]) -> tuple[bytes, str]:
        url = urljoin(post_url, entry["url"])  # bundle-relative, site-relative or absolute
        row = s.db.query("SELECT path FROM media WHERE url = ?", (url,))
        if row:
            return await s.store.read(row[0]["path"]), posixpath.basename(row[0]["path"])
        assert_fetchable(url, resolver=s.resolver)
        async with guarded_client(s.resolver, timeout=30, follow_redirects=False) as client:
            response = await client.get(url)
        if response.status_code != 200:
            raise SyndicationError(f"could not fetch {url}: HTTP {response.status_code}")
        return response.content, posixpath.basename(urlsplit(url).path) or "image"

    return fetch


async def run_syndication(s: AppState, source_url: str | None = None) -> Result:
    if not s.targets:
        return Result("No syndication targets have been configured")
    row = _next_waiting(s, source_url)
    if row is None:
        return Result("No posts awaiting syndication")
    props = json.loads(row["properties"])
    queued: list[str] = props.get("mp-syndicate-to") or []
    if not queued:
        return Result("No posts awaiting syndication")
    if row["post_status"] != "published":
        return Result(f"The post at {row['url']} is a draft and is not syndicated")
    if props.get("visibility") == "private":
        return Result(f"The post at {row['url']} is private and is not syndicated")
    if s.config.syndication.require_live and not await _is_live(s, row["url"]):
        return Result(
            f"The post at {row['url']} is not live yet, so it was left for later", url=row["url"]
        )

    done = {_origin(u) for u in (props.get("syndication") or []) if isinstance(u, str)}
    by_uid = {t.uid: t for t in s.targets}
    copies: list[str] = []
    failed: list[str] = []
    for uid in queued:
        target = by_uid.get(uid)
        if target is None or _origin(uid) in done:
            continue  # unknown now, or already shared there
        try:
            copies.append(await target.syndicate(props, _fetcher(s, row["url"])))
        except NotApplicable as exc:
            log.info("%s: not mirrored on %s: %s", row["url"], uid, exc)
        except (SyndicationError, FetchError) as exc:
            log.warning("%s: could not share to %s: %s", row["url"], uid, exc)
            failed.append(uid)

    ops: dict[str, Any] = {}
    if copies:
        ops["add"] = {"syndication": copies}
    if failed:
        ops["replace"] = {"mp-syndicate-to": failed}
    else:
        ops["delete"] = ["mp-syndicate-to"]
    await update_post(s, row["url"], **ops)
    shared = f"Post updated at {row['url']}"
    return Result(shared, url=row["url"], failed=failed, syndicated=copies)
