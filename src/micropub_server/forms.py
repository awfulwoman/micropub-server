"""Turn a browser form into a post, and say what is wrong with it (spec 13)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import FormData, UploadFile

from .config import Media, PostType
from .media import MediaError, Processed, process_upload
from .posts import VISIBILITY

PHOTO_SLOTS = 4
LINKS = {
    "bookmark-of": "Link",
    "like-of": "Liking",
    "repost-of": "Reposting",
    "in-reply-to": "Replying to",
    "quotation-of": "Quoting",
}
LABELS = {
    **LINKS,
    "name": "Title",
    "content": "Text",
    "summary": "Summary",
    "photo": "Photo",
    "location": "Location",
    "checkin": "Place",
    "category": "Tags",
}
VISIBILITY_LABELS = {
    "public": "Public: on the site's lists and in RSS",
    "rss": "RSS only: in the feeds, not on the site's lists",
    "private": "Private: listed nowhere, only reachable by its address",
}
ORDER = [*LINKS, "name", "content", "summary", "photo", "location", "checkin", "category"]

# Used when a post type in the config does not list its own properties.
DEFAULT_PROPERTIES = {
    "note": ["content", "category", "location"],
    "article": ["name", "content", "summary", "category"],
    "bookmark": ["bookmark-of", "name", "content", "category"],
    "like": ["like-of"],
    "reply": ["in-reply-to", "content"],
    "repost": ["repost-of", "content"],
    "quote": ["quotation-of", "content"],
    "photo": ["photo", "name", "content", "category", "location"],
    "checkin": ["checkin", "content", "photo", "category"],
    "page": ["name", "content"],
}
DEFAULT_REQUIRED = {
    "article": ["name", "content"],
    "bookmark": ["bookmark-of"],
    "like": ["like-of"],
    "reply": ["in-reply-to"],
    "repost": ["repost-of"],
    "quote": ["quotation-of", "content"],
    "photo": ["photo"],
    "checkin": ["checkin"],
    "page": ["name", "content"],
    "note": ["content"],
}


def type_fields(name: str, cfg: PostType) -> list[str]:
    wanted = cfg.properties or DEFAULT_PROPERTIES.get(name) or ["content", "category"]
    return [p for p in ORDER if p in wanted]


def type_required(name: str, cfg: PostType) -> list[str]:
    return cfg.required_properties or DEFAULT_REQUIRED.get(name, [])


@dataclass
class Built:
    jf2: dict[str, Any] = field(default_factory=dict)
    uploads: dict[str, list[Processed]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)


def _is_web_address(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _coordinates(lat: str, lng: str) -> tuple[float, float] | None:
    try:
        a, b = float(lat), float(lng)
    except ValueError:
        return None
    return (a, b) if -90 <= a <= 90 and -180 <= b <= 180 else None


async def build_post(
    name: str,
    cfg: PostType,
    form: FormData,
    media: Media,
    channel: str | None,
    *,
    editing: bool = False,
) -> Built:
    """Read a form for one post type. Nothing is written; errors are returned per field."""
    built = Built()
    text = lambda key: str(form.get(key, "")).strip()  # noqa: E731
    # Photos are not changed from the edit form: they belong to the post's bundle.
    fields = [f for f in type_fields(name, cfg) if not (editing and f == "photo")]
    required = set(type_required(name, cfg)) - ({"photo"} if editing else set())
    built.values = {k: v for k, v in form.items() if isinstance(v, str) and k not in {"csrf"}}
    built.values["photo_alt"] = [str(v) for v in form.getlist("photo_alt")]
    built.values["syndicate"] = [str(v) for v in form.getlist("syndicate")]
    built.values["topic"] = [str(v) for v in form.getlist("topic") if v]
    jf2: dict[str, Any] = {"type": "entry"}
    errors = built.errors

    for prop in fields:
        if prop in LINKS:
            value = text(prop)
            if value and not _is_web_address(value):
                errors[prop] = (
                    f"{LABELS[prop]} must be a web address starting with http:// or https://"
                )
            elif value:
                jf2[prop] = value
        elif prop in {"name", "content", "summary"}:
            if value := text(prop):
                jf2[prop] = value
        elif prop == "category":
            seen: dict[str, str] = {}
            for tag in text("category").split(","):
                if tag.strip():
                    seen.setdefault(tag.strip().lower(), tag.strip())
            if seen:
                jf2["category"] = list(seen.values())
        elif prop == "location":
            lat, lng = text("location_lat"), text("location_lng")
            if lat or lng:
                point = _coordinates(lat, lng)
                if point is None:
                    errors["location"] = (
                        "Enter valid coordinates: latitude -90 to 90, longitude -180 to 180"
                    )
                else:
                    jf2["location"] = f"geo:{point[0]},{point[1]}"
        elif prop == "checkin":
            place, lat, lng = text("checkin_name"), text("checkin_lat"), text("checkin_lng")
            if place or lat or lng:
                card: dict[str, Any] = {"type": "card"}
                if place:
                    card["name"] = place
                if lat or lng:
                    point = _coordinates(lat, lng)
                    if point is None:
                        errors["checkin"] = (
                            "Enter valid coordinates: latitude -90 to 90, longitude -180 to 180"
                        )
                    else:
                        card["latitude"], card["longitude"] = str(point[0]), str(point[1])
                jf2["checkin"] = card
        elif prop == "photo":
            await _read_photos(form, media, built)

    if published := text("published"):
        jf2["published"] = published
    if slug := text("mp-slug"):
        jf2["mp-slug"] = slug
    if (visibility := text("visibility")) in VISIBILITY:
        jf2["visibility"] = visibility
    if channel:
        jf2["mp-channel"] = channel
    if targets := [str(v) for v in form.getlist("syndicate") if v]:
        jf2["mp-syndicate-to"] = targets
    if built.values["topic"]:  # none ticked: the server works them out from the tags (spec 15)
        jf2["mp-topic"] = built.values["topic"]
    if "mp-photo-alt" in built.jf2:
        jf2["mp-photo-alt"] = built.jf2["mp-photo-alt"]

    for prop in required & set(fields):
        have = (
            bool(built.uploads.get("photo"))
            if prop == "photo"
            else (jf2.get(prop) or (prop == "checkin" and "checkin" in jf2))
        )
        if not have and prop not in errors:
            errors[prop] = f"{LABELS[prop]} is required"

    built.jf2 = jf2
    return built


async def _read_photos(form: FormData, media: Media, built: Built) -> None:
    files = form.getlist("photo")
    alts = [str(a).strip() for a in form.getlist("photo_alt")]
    processed: list[Processed] = []
    chosen_alts: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, UploadFile) or not item.filename:
            continue  # an empty slot
        try:
            processed.append(process_upload(await item.read(), item.filename, media))
        except MediaError as exc:
            built.errors["photo"] = f"{item.filename}: {exc}"
            return
        chosen_alts.append(alts[index] if index < len(alts) else "")
    if processed:
        built.uploads["photo"] = processed
        if any(chosen_alts):
            built.jf2["mp-photo-alt"] = chosen_alts
