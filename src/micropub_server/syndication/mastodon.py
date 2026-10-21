"""Share posts to Mastodon (spec 08)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import MediaFetcher, NotApplicable, SyndicationError
from .text import URL_LENGTH, counted_length, hashtags, plain_text, truncate

MAX_MEDIA = 4
VISIBILITY = {"public", "unlisted", "private"}


def build_status(props: dict[str, Any], *, limit: int = 500, include_permalink: bool = True) -> str:
    permalink = props["url"]
    reserve = (2 + URL_LENGTH) if include_permalink else 0
    commentary = plain_text(props.get("content"))
    room = limit - reserve
    if props.get("name"):
        # A titled post is shared whole if it fits, else as its summary or first
        # paragraph. The title is left out unless there is nothing else.
        if commentary and counted_length(commentary) <= room:
            body = commentary
        else:
            excerpt = plain_text(props.get("summary")) or commentary.split("\n\n", 1)[0]
            body = truncate(excerpt, room) if excerpt else str(props["name"])
    else:
        body = commentary
        if props.get("repost-of") and commentary:
            body = f"{body} {props['repost-of']}"
        body = truncate(body, room) if body else ""

    status = body
    if include_permalink:
        status = f"{body}\n\n{permalink}" if body else permalink
    tags = hashtags(props.get("category"), limit - counted_length(status) - 2)
    return f"{status}\n\n{tags}" if tags else status


def _status_id(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


class MastodonTarget:
    def __init__(
        self,
        *,
        url: str,
        user: str,
        token: str,
        checked: bool = True,
        character_limit: int = 500,
        include_permalink: bool = True,
        visibility: str = "public",
        service_name: str = "Mastodon",
    ):
        self.base = url.rstrip("/")
        self.user = user
        self.host = urlsplit(self.base).hostname or ""
        self.uid = f"{self.base}/@{user}"
        self.checked = checked
        self.limit = character_limit
        self.include_permalink = include_permalink
        self.visibility = visibility
        self.service_name = service_name
        self._token = token

    @property
    def info(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "name": f"@{self.user} on {self.host}",
            "checked": self.checked,
            "service": {
                "name": self.service_name,
                "url": self.base + "/",
                "photo": f"{self.base}/favicon.ico",
            },
            "user": {"name": self.user, "url": self.uid},
        }

    def _same_instance(self, url: Any) -> bool:
        return isinstance(url, str) and (urlsplit(url).hostname or "") == self.host

    async def _post(self, client: httpx.AsyncClient, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
        try:
            response = await client.post(f"{self.base}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise SyndicationError(f"Mastodon request failed: {exc}") from exc
        if response.status_code >= 300:
            raise SyndicationError(f"Mastodon: HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    async def syndicate(self, props: dict[str, Any], fetch_media: MediaFetcher) -> str:
        like, repost, reply = props.get("like-of"), props.get("repost-of"), props.get("in-reply-to")
        async with httpx.AsyncClient(timeout=30) as client:
            if like:
                if not self._same_instance(like):
                    raise NotApplicable("a like of a post on another site cannot be mirrored")
                await self._post(client, f"/api/v1/statuses/{_status_id(like)}/favourite")
                return str(like)
            if repost and not props.get("content"):
                if not self._same_instance(repost):
                    raise NotApplicable("a repost of a post on another site cannot be mirrored")
                await self._post(client, f"/api/v1/statuses/{_status_id(repost)}/reblog")
                return str(repost)
            if reply and not self._same_instance(reply):
                raise NotApplicable("a reply to a post on another site cannot be mirrored")

            visibility = props.get("visibility")
            body: dict[str, Any] = {
                "status": build_status(
                    props, limit=self.limit, include_permalink=self.include_permalink
                ),
                "visibility": visibility if visibility in VISIBILITY else self.visibility,
            }
            if reply:
                body["in_reply_to_id"] = _status_id(reply)
            media_ids = []
            for entry in (props.get("photo") or [])[:MAX_MEDIA]:
                data, filename = await fetch_media(entry)
                uploaded = await self._post(
                    client,
                    "/api/v2/media",
                    files={"file": (filename, data)},
                    data={"description": entry.get("alt", "")},
                )
                media_ids.append(uploaded["id"])
            if media_ids:
                body["media_ids"] = media_ids
            created = await self._post(
                client,
                "/api/v1/statuses",
                json=body,
                headers={"Idempotency-Key": str(props["uid"])},
            )
            return str(created["url"])
