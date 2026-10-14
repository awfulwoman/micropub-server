"""Render a JF2 post as a Hugo Markdown file (spec 06)."""

from __future__ import annotations

import io
from typing import Any
from urllib.parse import urlsplit

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .config import Template
from .util import to_list

# Keys never written to front matter, whatever the config says.
_INTERNAL = {"type", "post-type", "post-status", "content"}
_DATES = {"date", "lastmod", "expiryDate"}
_FRONT = ["title", "date", "lastmod", "draft"]
_TYPE_SPECIFIC = [
    "bookmark_of", "basedomain", "like_of", "repost_of", "in_reply_to", "quote_of",
    "place_name", "place_geo",
]  # fmt: skip
_AFTER = ["tags", "images", "videos", "syndication"]


def _yaml() -> YAML:
    y = YAML()
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 10_000
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _basedomain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _num(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _build_front_matter(props: dict[str, Any], tpl: Template) -> dict[str, Any]:
    out: dict[str, Any] = {}
    omit = set(tpl.omit)
    mapping = dict(tpl.mapping)

    for key, value in props.items():
        if (
            key in _INTERNAL
            or key in omit
            or key.startswith("mp-")
            or key in {"checkin", "location"}
        ):
            continue
        if key == "deleted":
            out["expiryDate"] = value
        elif key == "photo":
            out[mapping.get("photo", "images")] = [
                {
                    "file": item["url"].rsplit("/", 1)[-1],
                    **({"alt": item["alt"]} if item.get("alt") else {}),
                }
                for item in to_list(value)
            ]
        elif key == "video":
            out[mapping.get("video", "videos")] = [
                item["url"] if isinstance(item, dict) else item for item in to_list(value)
            ]
        elif key in mapping:
            out[mapping[key]] = value
        else:
            out[key.replace("-", "_")] = value

    if props.get("post-status") == "draft":
        out["draft"] = True

    for link_key in ("bookmark-of",):
        if props.get(link_key):
            out["basedomain"] = _basedomain(props[link_key])

    card = props.get("checkin")
    if isinstance(card, dict):
        if card.get("name"):
            out["place_name"] = card["name"]
        if card.get("latitude") and card.get("longitude"):
            out["place_geo"] = {"lat": _num(card["latitude"]), "lng": _num(card["longitude"])}
        if "title" not in out and card.get("name"):
            out["title"] = f"Checked in at {card['name']}"
    geo = props.get("location")
    if isinstance(geo, dict) and geo.get("type") == "geo" and "place_geo" not in out:
        out["place_geo"] = {"lat": _num(geo.get("latitude")), "lng": _num(geo.get("longitude"))}

    return _order(out)


def _order(fm: dict[str, Any]) -> dict[str, Any]:
    fixed = _FRONT + _TYPE_SPECIFIC + _AFTER
    ordered = {k: fm[k] for k in fixed if k in fm}
    ordered.update({k: fm[k] for k in sorted(fm) if k not in ordered})
    for key in _DATES:
        if key in ordered:
            ordered[key] = DoubleQuotedScalarString(ordered[key])
    return ordered


def _body(props: dict[str, Any]) -> str:
    content = props.get("content")
    if isinstance(content, dict):
        return content.get("text") or content.get("html") or ""
    return content or ""


def render_post(props: dict[str, Any], tpl: Template) -> str:
    fm = _build_front_matter(props, tpl)
    stream = io.StringIO()
    if fm:
        _yaml().dump(fm, stream)
    front = stream.getvalue()
    body = _body(props).strip()
    text = f"---\n{front}---\n"
    if body:
        text += f"\n{body}\n"
    return text
