"""Share posts to Bluesky (spec 08)."""

from __future__ import annotations

import io
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from atproto import AsyncClient, client_utils, models
from atproto_client.exceptions import AtProtocolError
from PIL import Image

from .base import MediaFetcher, NotApplicable, SyndicationError
from .text import _URL, hashtags, plain_text, truncate

LIMIT = 300  # graphemes; URLs count in full
MAX_IMAGES = 4
MAX_BLOB = 950_000  # Bluesky refuses blobs over about 1 MB
_BSKY_POST = re.compile(r"^/profile/(?P<who>[^/]+)/post/(?P<rkey>[^/]+)/?$")

Segment = tuple[str, str, str | None]  # (kind, shown text, uri or tag)


def compose(
    props: dict[str, Any], *, limit: int = LIMIT, include_permalink: bool = True
) -> list[Segment]:
    """The post as text, link and hashtag segments."""
    permalink = props["url"]
    reserve = len(permalink) + 2 if include_permalink else 0
    commentary = plain_text(props.get("content"))
    body = str(props["name"]) if props.get("name") else commentary
    body = truncate(body, limit - reserve, None) if body else ""

    segments: list[Segment] = []
    pos = 0
    for match in _URL.finditer(body):
        if match.start() > pos:
            segments.append(("text", body[pos : match.start()], None))
        segments.append(("link", match.group(0), match.group(0)))
        pos = match.end()
    if pos < len(body):
        segments.append(("text", body[pos:], None))

    if include_permalink:
        if segments:
            segments.append(("text", "\n\n", None))
        segments.append(("link", permalink, permalink))
    used = sum(len(text) for _, text, _ in segments)
    tags = hashtags(props.get("category"), limit - used - 2)
    if tags:
        segments.append(("text", "\n\n", None))
        for i, tag in enumerate(tags.split(" ")):
            if i:
                segments.append(("text", " ", None))
            segments.append(("tag", tag, tag.removeprefix("#")))
    return segments


def _builder(segments: list[Segment]) -> client_utils.TextBuilder:
    builder = client_utils.TextBuilder()
    for kind, text, extra in segments:
        if kind == "link":
            builder.link(text, extra)
        elif kind == "tag":
            builder.tag(text, extra)
        else:
            builder.text(text)
    return builder


def _bsky_post(url: Any) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    match = _BSKY_POST.match(parts.path) if (parts.hostname or "") == "bsky.app" else None
    return (match["who"], match["rkey"]) if match else None


def fit_blob(data: bytes) -> bytes:
    """Re-encode a photo until it fits Bluesky's size limit."""
    if len(data) <= MAX_BLOB:
        return data
    image = Image.open(io.BytesIO(data)).convert("RGB")
    for scale, quality in [
        (1.0, 80),
        (0.9, 75),
        (0.8, 72),
        (0.7, 70),
        (0.6, 65),
        (0.5, 60),
        (0.4, 55),
        (0.3, 50),
    ]:
        resized = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        )
        out = io.BytesIO()
        resized.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= MAX_BLOB:
            return out.getvalue()
    return out.getvalue()


class BlueskyTarget:
    def __init__(
        self,
        *,
        handle: str,
        password: str,
        service: str = "https://bsky.social",
        checked: bool = False,
        include_permalink: bool = True,
    ):
        self.handle = handle
        self.service = service.rstrip("/")
        self.uid = f"https://bsky.app/profile/{handle}"
        self.checked = checked
        self.include_permalink = include_permalink
        self._password = password

    @property
    def info(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "name": f"@{self.handle} on Bluesky",
            "checked": self.checked,
            "service": {
                "name": "Bluesky",
                "url": "https://bsky.app/",
                "photo": "https://bsky.app/static/favicon.png",
            },
            "user": {"name": self.handle, "url": self.uid},
        }

    async def _record(self, client: AsyncClient, ref: tuple[str, str]):
        who, rkey = ref
        post = await client.get_post(rkey, who)
        return post, models.ComAtprotoRepoStrongRef.Main(uri=post.uri, cid=post.cid)

    async def syndicate(self, props: dict[str, Any], fetch_media: MediaFetcher) -> str:
        like, repost, reply = props.get("like-of"), props.get("repost-of"), props.get("in-reply-to")
        refs = {k: _bsky_post(props.get(k)) for k in ("like-of", "repost-of", "in-reply-to")}
        if like and not refs["like-of"]:
            raise NotApplicable("a like of a post outside Bluesky cannot be mirrored")
        if repost and not refs["repost-of"]:
            raise NotApplicable("a repost of a post outside Bluesky cannot be mirrored")
        if reply and not refs["in-reply-to"]:
            raise NotApplicable("a reply to a post outside Bluesky cannot be mirrored")

        client = AsyncClient(self.service)
        try:
            await client.login(self.handle, self._password)
            if like:
                _, ref = await self._record(client, refs["like-of"])
                await client.like(ref.uri, ref.cid)
                return str(like)
            if repost and not props.get("content"):
                _, ref = await self._record(client, refs["repost-of"])
                await client.repost(ref.uri, ref.cid)
                return str(repost)

            reply_ref = None
            if reply:
                parent, parent_ref = await self._record(client, refs["in-reply-to"])
                thread = getattr(parent.value, "reply", None)
                root = thread.root if thread else parent_ref
                reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root)

            media = await self._images(client, props, fetch_media)
            embed: Any = media
            if repost:  # a repost with commentary is a quote post
                _, quoted = await self._record(client, refs["repost-of"])
                record = models.AppBskyEmbedRecord.Main(record=quoted)
                embed = (
                    models.AppBskyEmbedRecordWithMedia.Main(record=record, media=media)
                    if media
                    else record
                )
            elif not media and props.get("name"):
                embed = self._card(props)

            segments = compose(props, include_permalink=self.include_permalink)
            created = await client.send_post(_builder(segments), reply_to=reply_ref, embed=embed)
        except (AtProtocolError, httpx.HTTPError) as exc:
            raise SyndicationError(f"Bluesky: {exc}") from exc
        return f"https://bsky.app/profile/{self.handle}/post/{created.uri.rsplit('/', 1)[-1]}"

    async def _images(self, client: AsyncClient, props: dict[str, Any], fetch_media: MediaFetcher):
        images = []
        for entry in (props.get("photo") or [])[:MAX_IMAGES]:
            data, _ = await fetch_media(entry)
            blob = await client.upload_blob(fit_blob(data))
            images.append(
                models.AppBskyEmbedImages.Image(alt=entry.get("alt", ""), image=blob.blob)
            )
        return models.AppBskyEmbedImages.Main(images=images) if images else None

    def _card(self, props: dict[str, Any]):
        description = props.get("summary") or truncate(plain_text(props.get("content")), 200, None)
        return models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                uri=props["url"], title=str(props["name"]), description=description or ""
            )
        )
