"""Import posts that already exist in the site repo, so they can be queried and edited (spec 10)."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from slugify import slugify

from .app import AppState
from .config import PostType
from .posts import normalise
from .publish import absolute_url
from .stores import FileSystemStore, Store
from .util import to_list

_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?\Z", re.DOTALL)
_LINKS = {
    "bookmark_of": "bookmark-of",
    "like_of": "like-of",
    "repost_of": "repost-of",
    "in_reply_to": "in-reply-to",
    "quote_of": "quotation-of",
}
_TOKEN_PATTERNS = {
    "yyyy": r"\d{4}",
    "MM": r"\d{2}",
    "dd": r"\d{2}",
    "HH": r"\d{2}",
    "mm": r"\d{2}",
    "ss": r"\d{2}",
    "hh": r"\d{2}",
    "M": r"\d{1,2}",
    "d": r"\d{1,2}",
    "H": r"\d{1,2}",
    "m": r"\d{1,2}",
    "s": r"\d{1,2}",
    "h": r"\d{1,2}",
    "y": r"\d+",
    "n": r"\d+",
}
_TOKEN = re.compile(r"\{(\w+)\}")


@dataclass
class Report:
    imported: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)
    by_type: Counter = field(default_factory=Counter)
    samples: list[tuple[str, str]] = field(default_factory=list)

    def fail(self, path: str, reason: str) -> None:
        self.failed += 1
        self.problems.append((path, reason))


class _Skip(Exception):
    pass


# --- helpers ---------------------------------------------------------------------
def _prefix(cfg: PostType) -> str | None:
    head = cfg.post.path.split("{", 1)[0]
    return head.rsplit("/", 1)[0] + "/" if "/" in head else None


def _template_regex(template: str) -> re.Pattern[str]:
    out, pos, seen = "", 0, set()
    for m in _TOKEN.finditer(template):
        out += re.escape(template[pos : m.start()])
        name = m[1]
        if name in seen:
            out += f"(?P={name})"
        else:
            seen.add(name)
            out += f"(?P<{name}>{_TOKEN_PATTERNS.get(name, '[^/]+')})"
        pos = m.end()
    return re.compile(out + re.escape(template[pos:]) + r"\Z")


def _default_url_path(path: str) -> str:
    rel = path.removeprefix("content/")
    rel = rel.removesuffix("/index.md") if rel.endswith("/index.md") else rel.removesuffix(".md")
    return rel.strip("/") + "/"


def _url_path(cfg: PostType, path: str, slug: str) -> str:
    match = _template_regex(cfg.post.path).match(path)
    template = cfg.post.url or cfg.post.path
    if match:
        tokens = {
            **match.groupdict(),
            "slug": slug,
        }  # the front matter slug decides, not the folder
        needed = set(_TOKEN.findall(template))
        if needed <= set(tokens):
            return _TOKEN.sub(lambda m: tokens[m[1]], template)
    return _default_url_path(path)


def _slug_for(cfg: PostType, fm: dict[str, Any], fm_slug: Any, path: str) -> str:
    if fm_slug:
        return str(fm_slug)
    if cfg.import_slug == "title" and fm.get("title"):
        return slugify(str(fm["title"])) or _file_slug(path)
    return _file_slug(path)


def _file_slug(path: str) -> str:
    stem = posixpath.basename(path)
    if stem == "index.md":
        return posixpath.basename(posixpath.dirname(path))
    return stem.removesuffix(".md")


def _as_text_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00"
    return str(value)


def _normalise_offset(value: str) -> str:
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip())


def _split(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT.match(text)
    if not match:
        raise ValueError("no YAML front matter")
    try:
        data = YAML(typ="safe").load(match[1]) or {}
    except YAMLError as exc:
        raise ValueError(f"front matter is not valid YAML: {str(exc).splitlines()[0]}") from exc
    if not isinstance(data, dict):
        raise ValueError("front matter is not a mapping")
    return data, (match[2] or "").strip()


def _front_matter_to_jf2(
    fm: dict[str, Any], body: str, type_name: str, cfg: PostType, path: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reverse the template mapping (spec 06). Returns (JF2 properties, extras kept verbatim)."""
    fm = dict(fm)
    props: dict[str, Any] = {"type": "entry"}
    published = _as_text_date(fm.pop("date", None))
    if not published:
        raise _Skip("no date in the front matter, so the post cannot be placed in time")
    props["published"] = _normalise_offset(published)

    if "title" in fm:
        props["name"] = str(fm.pop("title"))
    updated = _as_text_date(fm.pop("lastmod", None)) or _as_text_date(fm.pop("modified", None))
    fm.pop("modified", None)
    if updated:
        props["updated"] = _normalise_offset(updated)
    if "tags" in fm:
        props["category"] = to_list(fm.pop("tags"))
    if "syndication" in fm:
        props["syndication"] = [str(u) for u in to_list(fm.pop("syndication"))]
    if "description" in fm:
        props["summary"] = str(fm.pop("description"))
    for key, jf2_key in _LINKS.items():
        if key in fm:
            props[jf2_key] = str(fm.pop(key))
    if fm.pop("draft", False) is True:
        props["post-status"] = "draft"

    if "images" in fm:
        photos = []
        for item in to_list(fm.pop("images")):
            if isinstance(item, dict) and item.get("file"):
                extra = {
                    k: v for k, v in item.items() if k not in {"file", "alt"}
                }  # width, height ...
                alt = {"alt": str(item["alt"])} if item.get("alt") else {}
                photos.append({"url": str(item["file"]), **alt, **extra})
            elif isinstance(item, str):
                photos.append({"url": item})
        if photos:
            props["photo"] = photos
    if "videos" in fm:
        props["video"] = [{"url": str(v)} for v in to_list(fm.pop("videos"))]

    geo = fm.get("place_geo")
    if isinstance(geo, dict) and "lat" in geo and "lng" in geo:
        fm.pop("place_geo")
        lat, lng = str(geo["lat"]), str(geo["lng"])
        if type_name == "checkin" or cfg.discovery == "checkin":
            card = {
                "type": "card",
                "name": str(fm.pop("place_name", "")),
                "latitude": lat,
                "longitude": lng,
            }
            props["checkin"] = {k: v for k, v in card.items() if v != ""}
        else:
            props["location"] = {"type": "geo", "latitude": lat, "longitude": lng}
    elif type_name == "checkin" and "place_name" in fm:
        props["checkin"] = {"type": "card", "name": str(fm.pop("place_name"))}

    fm.pop("uid", None)  # ours is derived from the URL
    if body:
        props["content"] = body.replace("\r\n", "\n")
    return props, fm


def _media_type(filename: str) -> tuple[str, str] | None:
    mime = mimetypes.guess_type(filename)[0] or ""
    family = mime.split("/", 1)[0]
    kind = {"image": "photo", "video": "video", "audio": "audio"}.get(family)
    return (kind, mime) if kind else None


# --- the import ------------------------------------------------------------------------
async def import_posts(
    s: AppState, source: Store, *, dry_run: bool = False, only_type: str | None = None
) -> Report:
    report = Report()
    tz = s.config.application.time_zone
    prefixes = sorted(
        ((p, name) for name, cfg in s.post_types.items() if (p := _prefix(cfg))),
        key=lambda item: -len(item[0]),
    )
    root = prefixes[0][0].split("/", 1)[0] if prefixes else "content"
    files = await source.list_files(root)
    by_dir: dict[str, list[str]] = {}
    for f in files:
        by_dir.setdefault(posixpath.dirname(f), []).append(f)

    for path in files:
        name = posixpath.basename(path)
        if not name.endswith(".md") or name == "_index.md":
            continue
        type_name = next(
            (
                t
                for p, t in prefixes
                if path.startswith(p)
                # A type rooted at the content directory itself (pages) only claims files there, not
                # everything below it that no other type wants.
                and (p != f"{root}/" or _template_regex(s.post_types[t].post.path).match(path))
            ),
            None,
        )
        if type_name is None or (only_type and type_name != only_type):
            if not only_type or type_name is None:
                report.skipped += 1
            continue
        cfg = s.post_types[type_name]
        try:
            text = (await source.read(path)).decode("utf-8")
            fm, body = _split(text)
            fm_slug = fm.get("slug")
            props, extras = _front_matter_to_jf2(fm, body, type_name, cfg, path)
            slug = _slug_for(cfg, fm, fm_slug, path)
            props["slug"] = slug
            if cfg.channel:
                props["mp-channel"] = cfg.channel
            props = normalise(
                props,
                tz=tz,
                me=s.me,
                slug_separator=s.config.publication.slug_separator,
                channels=list(s.config.channels),
            )
            if extras:
                props["front-matter"] = extras
            url = absolute_url(s.me, _url_path(cfg, path, slug))
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
            props.update({"url": url, "uid": uid, "post-type": type_name})
            attachments = await _attachments(source, by_dir, path, url, props["published"])
            outcome = _store(s, path, type_name, props, attachments, dry_run)
        except _Skip as exc:
            if _prefix(cfg) == f"{root}/":
                report.skipped += 1  # an undated page (about, contact) is not a post
            else:
                report.fail(path, str(exc))
            continue
        except (ValueError, UnicodeDecodeError, KeyError) as exc:
            report.fail(
                path,
                f"{type(exc).__name__}: {exc}" if not isinstance(exc, ValueError) else str(exc),
            )
            continue
        if outcome == "conflict":
            report.fail(
                path,
                "a post already exists at this path or URL (it was created by the server, or by another file)",
            )
        elif outcome == "unchanged":
            report.unchanged += 1
        else:
            report.imported += 1
            report.by_type[type_name] += 1
            if len(report.samples) < 5:
                report.samples.append((path, url))
    return report


async def _attachments(
    source: Store, by_dir: dict[str, list[str]], path: str, url: str, published: str
):
    if posixpath.basename(path) != "index.md":
        return []
    out = []
    for f in by_dir.get(posixpath.dirname(path), []):
        kind = _media_type(f)
        if not kind or f == path:
            continue
        md5 = ""
        if isinstance(
            source, FileSystemStore
        ):  # reading binaries from GitHub one by one is not worth it
            md5 = hashlib.md5(await source.read(f)).hexdigest()  # noqa: S324
        out.append((f, f"{url}{posixpath.basename(f)}", kind[0], kind[1], md5, published))
    return out


def _store(
    s: AppState, path: str, type_name: str, props: dict[str, Any], attachments: list, dry_run: bool
) -> str:
    row = s.db.query(
        "SELECT uid, path, source, properties FROM posts WHERE url = ? OR path = ?",
        (props["url"], path),
    )
    if row and (row[0]["source"] != "import" or row[0]["uid"] != props["uid"]):
        return "conflict"
    payload = json.dumps(props, default=str)
    if row and row[0]["properties"] == payload and row[0]["path"] == path:
        return "unchanged"
    if dry_run:
        return "imported"
    published = props["published"]
    status = props.get("post-status", "published")
    try:
        with s.db.transaction():
            if row:
                s.db.execute(
                    "UPDATE posts SET post_type=?, post_status=?, published=?, updated=?, properties=? WHERE uid=?",
                    (type_name, status, published, props.get("updated"), payload, props["uid"]),
                )
            else:
                s.db.execute(
                    "INSERT INTO posts (uid, url, path, post_type, post_status, published, updated, properties, source)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'import')",
                    (
                        props["uid"],
                        props["url"],
                        path,
                        type_name,
                        status,
                        published,
                        props.get("updated"),
                        payload,
                    ),
                )
            for file_path, url, kind, mime, md5, when in attachments:
                s.db.execute(
                    "INSERT OR IGNORE INTO media (uid, url, path, media_type, content_type, md5, published, post_uid)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid5(uuid.NAMESPACE_URL, url)),
                        url,
                        file_path,
                        kind,
                        mime,
                        md5,
                        when,
                        props["uid"],
                    ),
                )
    except sqlite3.IntegrityError:
        return "conflict"
    return "imported"


async def check_urls(s: AppState, *, concurrency: int = 8) -> list[tuple[str, int]]:
    """Ask the live site for every imported URL; return the ones that are not served (url, status)."""
    import asyncio

    import httpx

    urls = [
        r["url"]
        for r in s.db.query(
            "SELECT url FROM posts WHERE source = 'import' AND post_status = 'published' ORDER BY url"
        )
    ]
    gate = asyncio.Semaphore(concurrency)
    bad: list[tuple[str, int]] = []

    async def one(client: httpx.AsyncClient, url: str) -> None:
        async with gate:
            try:
                status = (await client.get(url)).status_code
            except httpx.HTTPError:
                status = 0
        if status != 200:
            bad.append((url, status))

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    return sorted(bad)
