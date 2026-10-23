"""Post normalisation, type discovery, slugs and path templates (spec 05)."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
from markdownify import markdownify
from slugify import slugify

from .config import PostType
from .util import to_list

_md = MarkdownIt("commonmark").enable(["table", "strikethrough"])

_GEO = re.compile(r"geo:(?P<lat>[\d+.?-]*),(?P<lon>[\d+.?-]*)(?:,(?P<alt>[\d+.?-]*))?")
_TOKEN = re.compile(r"\{(\w+)\}")
_SXG = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ_abcdefghijkmnopqrstuvwxyz"
_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


# --- helpers -----------------------------------------------------------------
def _zone(tz: str, fallback: datetime | None = None):
    return (
        fallback.tzinfo
        if tz == "client" and fallback
        else ZoneInfo(tz if tz != "client" else "UTC")
    )


def _parse_date(value: str, tz: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tz))
    return dt


def parse_geo(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("geo:"):
        m = _GEO.match(value)
        if m:
            out = {"type": "geo", "latitude": m["lat"], "longitude": m["lon"]}
            if m["alt"]:
                out["altitude"] = m["alt"]
            return out
    return value


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("text") or value.get("html") or ""
    return value or ""


# Unguessable: not derived from the date or a counter, so hidden posts cannot be enumerated.
_ID_ALPHABET = "abcdefghjkmnpqrstvwxyz23456789"
_ID_LENGTH = 8


def get_slug(props: dict[str, Any], separator: str) -> str:
    suggested = props.get("mp-slug")
    name = props.get("name")
    source = suggested or (" ".join(str(name).split()[:5]) if name else "")
    slug = slugify(str(source), separator=separator) if source else ""
    if not slug:
        slug = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))
    return slug


def _relative(url: str, me: str) -> str:
    base = me.rstrip("/")
    return url[len(base) :] if url.startswith(base + "/") else url


def _media_list(value: Any, me: str, alts: list[str] | None = None) -> list[dict[str, str]]:
    out = []
    for i, item in enumerate(to_list(value)):
        entry = dict(item) if isinstance(item, dict) else {"url": item}
        entry["url"] = _relative(entry["url"], me)
        if alts and i < len(alts) and alts[i]:
            entry["alt"] = alts[i].strip()
        elif entry.get("alt"):
            entry["alt"] = entry["alt"].strip()
        out.append(entry)
    return out


# --- normalise ---------------------------------------------------------------
def normalise(
    props: dict[str, Any],
    *,
    tz: str,
    me: str,
    slug_separator: str,
    channels: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    p = dict(props)
    now = now or datetime.now(UTC)

    # published
    if p.get("published"):
        if tz == "client":
            p["published"] = str(p["published"])
        else:
            p["published"] = (
                _parse_date(str(p["published"]), tz)
                .astimezone(ZoneInfo(tz))
                .isoformat(timespec="seconds")
            )
    else:
        zone = ZoneInfo(tz if tz != "client" else "UTC")
        p["published"] = now.astimezone(zone).isoformat(timespec="seconds")

    if p.get("updated") and tz != "client":
        p["updated"] = (
            _parse_date(str(p["updated"]), tz)
            .astimezone(ZoneInfo(tz))
            .isoformat(timespec="seconds")
        )

    # name
    if "name" in p:
        name = str(p["name"]).strip()
        if name:
            p["name"] = name
        else:
            del p["name"]

    # content
    if p.get("content"):
        content = p["content"]
        if isinstance(content, str):
            content = {"text": content}
        text, html = content.get("text"), content.get("html")
        if html and not text:
            text = markdownify(html).strip()
        elif text and not html:
            html = _md.render(text)
        p["content"] = {"text": text, "html": html}

    # categories
    if "category" in p:
        seen: set[str] = set()
        cats = []
        for value in to_list(p["category"]):
            name = str(value).strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                cats.append(name)
        if cats:
            p["category"] = cats
        else:
            del p["category"]

    if "location" in p:
        p["location"] = parse_geo(p["location"])

    alts = [str(a) for a in to_list(p.pop("mp-photo-alt", None))]
    if p.get("photo"):
        p["photo"] = _media_list(p["photo"], me, alts)
    for key in ("video", "audio"):
        if p.get(key):
            p[key] = _media_list(p[key], me)

    # syndication commands
    if "mp-syndicate-to" in p:
        targets = (
            to_list(p["mp-syndicate-to"]) if not isinstance(p["mp-syndicate-to"], dict) else []
        )
        if targets:
            p["mp-syndicate-to"] = targets
        else:
            del p["mp-syndicate-to"]
    if "syndication" in p:
        p["syndication"] = to_list(p["syndication"])

    # A slug is fixed once chosen: editing a title must not move a published post.
    if "mp-slug" in p or "slug" not in p:
        p["slug"] = get_slug(p, slug_separator)

    if channels:
        wanted = [c for c in to_list(p.pop("mp-channel", None)) if c in channels]
        wanted = wanted or [c for c in to_list(p.get("channel")) if c in channels]  # keep on update
        p["channel"] = list(dict.fromkeys(wanted)) or [channels[0]]
    else:
        p.pop("mp-channel", None)

    p.setdefault("post-status", "published")
    return p


# --- post type discovery -----------------------------------------------------
_BUILTIN_DISCOVERY = [
    ("rsvp", "rsvp"),
    ("repost", "repost-of"),
    ("like", "like-of"),
    ("reply", "in-reply-to"),
    ("video", "video"),
    ("photo", "photo"),
]


def discover_post_type(types: dict[str, PostType], props: dict[str, Any]) -> str:
    channels = to_list(props.get("channel"))
    for name, cfg in types.items():
        if cfg.channel and cfg.channel in channels:
            return name
        if cfg.discovery_first and cfg.discovery and cfg.discovery in props:
            return name

    if props.get("type") == "event":
        return "event"

    table = dict(_BUILTIN_DISCOVERY)
    for name, cfg in types.items():
        if cfg.discovery:
            table[name] = cfg.discovery
    instead = None
    for name, prop in table.items():
        if prop not in props:
            continue
        cfg = types.get(name)
        if cfg and any(p in props for p in cfg.discovery_without):
            instead = instead or cfg.discovery_without_type
            continue
        return name
    if instead:
        return instead

    if props.get("children"):
        return "collection"

    body = _text(props.get("content")) or props.get("summary")
    if body and props.get("name"):
        return "article"
    return "note"


# --- path templates ----------------------------------------------------------
def _d60(dt: datetime) -> str:
    days = (dt.date() - datetime(1970, 1, 1).date()).days
    if days == 0:
        return "0"
    out = ""
    while days:
        out = _SXG[days % 60] + out
        days //= 60
    return out


def render_template(
    template: str,
    props: dict[str, Any],
    *,
    tz: str,
    n: int = 1,
    separator: str = "-",
) -> str:
    dt = _parse_date(props["published"], tz)
    if tz != "client":
        dt = dt.astimezone(ZoneInfo(tz))
    hour12 = dt.hour % 12 or 12
    tokens = {
        "y": str(dt.year),
        "yyyy": f"{dt.year:04d}",
        "M": str(dt.month),
        "MM": f"{dt.month:02d}",
        "MMM": _MONTHS[dt.month - 1][:3],
        "MMMM": _MONTHS[dt.month - 1],
        "w": str(dt.isocalendar().week),
        "ww": f"{dt.isocalendar().week:02d}",
        "D": str(dt.timetuple().tm_yday),
        "DDD": f"{dt.timetuple().tm_yday:03d}",
        "d": str(dt.day),
        "dd": f"{dt.day:02d}",
        "h": str(hour12),
        "hh": f"{hour12:02d}",
        "H": str(dt.hour),
        "HH": f"{dt.hour:02d}",
        "m": str(dt.minute),
        "mm": f"{dt.minute:02d}",
        "s": str(dt.second),
        "ss": f"{dt.second:02d}",
        "t": str(int(dt.timestamp())),
        "T": str(int(dt.timestamp() * 1000)),
        "D60": _d60(dt),
        "n": str(n),
        "slug": str(props.get("slug", "")),
        "uuid": str(props.get("uid", "")),
        "channel": separator.join(to_list(props.get("channel"))),
    }
    for key in ("filename", "ext", "md5"):
        if key in props:
            tokens[key] = str(props[key])
    return _TOKEN.sub(lambda m: tokens.get(m[1], m[0]), template)
