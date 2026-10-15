"""Micropub queries: config, categories, channels, post types, source (spec 03)."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from typing import Any

import httpx

from .app import AppState
from .auth.client import FetchError, assert_fetchable
from .jf2 import jf2_to_mf2
from .publish import MicropubError

DEFAULT_LIMIT = 40
MAX_LIMIT = 100


async def get_categories(s: AppState) -> list[str]:
    """Tags to suggest: the configured list or URL, then every tag already used, commonest first."""
    configured = await _configured_categories(s)
    seen = {t.lower() for t in configured}
    rows = s.db.query(
        "SELECT j.value AS tag, count(*) AS n FROM posts, json_each(posts.properties, '$.category') j"
        " WHERE posts.deleted IS NULL AND j.type = 'text' GROUP BY lower(j.value) ORDER BY n DESC, tag"
    )
    used = [r["tag"] for r in rows if r["tag"].lower() not in seen]
    return [*configured, *used]


async def _configured_categories(s: AppState) -> list[str]:
    source = s.config.publication.categories
    if isinstance(source, list):
        return source
    key = f"categories:{source}"
    now = s.clock().isoformat(timespec="seconds")
    row = s.db.query("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, now))
    if row:
        return json.loads(row[0][0])
    try:
        assert_fetchable(source, resolver=s.resolver)
        async with httpx.AsyncClient(timeout=5) as client:
            data = (await client.get(source)).json()
    except (FetchError, httpx.HTTPError, ValueError):
        return []
    values = [str(v) for v in data] if isinstance(data, list) else []
    expires = (s.clock() + timedelta(seconds=s.config.application.cache_ttl_seconds)).isoformat(
        timespec="seconds"
    )
    s.db.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(values), expires),
    )
    return values


def channels_list(s: AppState) -> list[dict[str, str]]:
    return [{"uid": uid, "name": ch.name} for uid, ch in s.config.channels.items()]


def post_types_list(s: AppState) -> list[dict[str, Any]]:
    return [
        {
            "type": name,
            "name": cfg.name,
            "h": "entry",
            "properties": cfg.properties,
            "required-properties": cfg.required_properties,
        }
        for name, cfg in s.post_types.items()
    ]


def syndicate_to_list(s: AppState) -> list[dict[str, Any]]:
    return [t.info for t in s.targets]


def slice_list(items: list[Any], params: dict[str, str]) -> list[Any]:
    needle = (params.get("filter") or "").lower()
    if needle:
        items = [i for i in items if needle in json.dumps(i).lower()]
    offset = int(params.get("offset") or 0)
    limit = int(params.get("limit") or 0)
    items = items[offset:]
    return items[:limit] if limit else items


async def build_config(s: AppState) -> dict[str, Any]:
    return {
        "media-endpoint": f"{s.base}media",
        "syndicate-to": syndicate_to_list(s),
        "post-types": post_types_list(s),
        "categories": await get_categories(s),
        "channels": channels_list(s),
        "q": [
            "category",
            "channel",
            "config",
            "media-endpoint",
            "post-types",
            "source",
            "syndicate-to",
        ],
    }


# --- source ------------------------------------------------------------------
def _like(text: str) -> str:
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _cursor(published: str, uid: str) -> str:
    return base64.urlsafe_b64encode(f"{published}|{uid}".encode()).decode()


def _decode_cursor(value: str) -> tuple[str, str]:
    try:
        published, uid = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
    except Exception as exc:
        raise MicropubError("invalid_request", "Invalid paging cursor", 400) from exc
    return published, uid


def _select_properties(mf2: dict[str, Any], wanted: list[str] | None) -> dict[str, Any]:
    if not wanted:
        return mf2
    return {"properties": {k: v for k, v in mf2["properties"].items() if k in wanted}}


def source_one(s: AppState, params: dict[str, Any]) -> dict[str, Any]:
    wanted = params.get("properties[]") or params.get("properties")
    wanted = [wanted] if isinstance(wanted, str) else wanted
    if params.get("url"):
        url = str(params["url"])
        rows = s.db.query(
            "SELECT properties FROM posts WHERE deleted IS NULL AND url IN (?, ?)",
            (url, url.rstrip("/") + "/"),
        )
        if not rows:
            raise MicropubError("invalid_request", f"No post found at {url}", 400)
    else:
        rows = s.db.query(
            "SELECT properties FROM posts WHERE deleted IS NULL AND uid = ?", (str(params["uid"]),)
        )
        if not rows:
            raise MicropubError("not_found", "No post found with that uid", 404)
    return _select_properties(jf2_to_mf2(json.loads(rows[0][0])), wanted)


def source_list(s: AppState, params: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(params.get("limit") or DEFAULT_LIMIT), MAX_LIMIT)
    where = ["deleted IS NULL"]
    args: list[Any] = []
    if params.get("post-type"):
        where.append("post_type = ?")
        args.append(params["post-type"])
    if params.get("post-status"):
        where.append("post_status = ?")
        args.append(params["post-status"])
    if params.get("category"):
        where.append("EXISTS (SELECT 1 FROM json_each(properties, '$.category') WHERE value = ?)")
        args.append(params["category"])
    if params.get("filter"):
        where.append(
            "(lower(json_extract(properties, '$.name')) LIKE ? ESCAPE '\\'"
            " OR lower(json_extract(properties, '$.content.text')) LIKE ? ESCAPE '\\')"
        )
        args += [_like(str(params["filter"]))] * 2

    backwards = bool(params.get("before"))
    cursor = params.get("before") or params.get("after")
    if cursor:
        published, uid = _decode_cursor(str(cursor))
        op = ">" if backwards else "<"
        where.append(f"(published, uid) {op} (?, ?)")
        args += [published, uid]
    order = "ASC" if backwards else "DESC"
    rows = s.db.query(
        f"SELECT published, uid, properties FROM posts WHERE {' AND '.join(where)}"
        f" ORDER BY published {order}, uid {order} LIMIT ?",
        (*args, limit + 1),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    if backwards:
        rows = list(reversed(rows))

    wanted = params.get("properties[]") or params.get("properties")
    wanted = [wanted] if isinstance(wanted, str) else wanted
    items = [_select_properties(jf2_to_mf2(json.loads(r["properties"])), wanted) for r in rows]
    paging: dict[str, str] = {}
    if rows:
        has_after = more if not backwards else bool(cursor)
        has_before = bool(cursor) if not backwards else more
        if has_after:
            paging["after"] = _cursor(rows[-1]["published"], rows[-1]["uid"])
        if has_before:
            paging["before"] = _cursor(rows[0]["published"], rows[0]["uid"])
    return {"items": items, "paging": paging}
