"""The user's profile for the IndieAuth `profile` scope: configured values over the site's h-card."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import mf2py

from ..app import AppState
from .client import FetchError, assert_fetchable

CACHE_FOR = timedelta(days=1)


def _first(values: list[Any] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    if isinstance(value, dict):
        value = value.get("value") or value.get("url")
    return str(value) if value else None


async def _discover(s: AppState) -> dict[str, str]:
    try:
        assert_fetchable(s.me, resolver=s.resolver)
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            response = await client.get(s.me, headers={"Accept": "text/html"})
        if response.status_code != 200:
            return {}
    except (FetchError, httpx.HTTPError):
        return {}
    cards = [
        i
        for i in mf2py.parse(doc=response.text, url=s.me)["items"]
        if "h-card" in i.get("type", [])
    ]
    if not cards:
        return {}
    mine = s.me.rstrip("/")
    card = next(
        (
            c
            for c in cards
            if any(
                u.rstrip("/") == mine for u in c["properties"].get("url", []) if isinstance(u, str)
            )
        ),
        cards[0],
    )
    props = card["properties"]
    found = {
        "name": _first(props.get("name")),
        "url": _first(props.get("url")) or s.me,
        "photo": _first(props.get("photo")),
    }
    return {k: v for k, v in found.items() if v}


async def get_profile(s: AppState) -> dict[str, str]:
    key = f"profile:{s.me}"
    now = s.clock().isoformat(timespec="seconds")
    cached = s.db.query("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, now))
    if cached:
        discovered = json.loads(cached[0][0])
    else:
        discovered = await _discover(s)
        s.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(discovered), (s.clock() + CACHE_FOR).isoformat(timespec="seconds")),
        )
    return {**discovered, **s.config.publication.profile}
