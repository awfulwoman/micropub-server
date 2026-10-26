"""The image on a bookmark's link card (spec 16): fetched once, shrunk and saved as card.webp.

The server keeps its own copy and never links to the other site's image. Everything here fails
soft: an image that cannot be fetched, read or used gives None and the card has no image.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from urllib.parse import urljoin

import filetype
import httpx
from PIL import Image, ImageOps

from .auth.client import FetchError, Resolver, assert_fetchable, guarded_client, system_resolver
from .config import MediaCard
from .media import MediaError, _open_image
from .target_meta import HOP_LIMIT, TOTAL_TIMEOUT, USER_AGENT

# no "cover", "feature" or "thumbnail": the site would use it as the bookmark's share card
CARD_FILENAME = "card.webp"
MIN_WIDTH, MIN_HEIGHT = 200, 100  # smaller images are mostly site logos and tracking pixels
_ACCEPTED = {"image/jpeg", "image/png", "image/webp", "image/gif"}  # never SVG


@dataclass(frozen=True)
class CardImage:
    content: bytes
    width: int
    height: int
    filename: str = CARD_FILENAME


def process_card(data: bytes, card: MediaCard) -> CardImage | None:
    """A WebP of at most card.max_width, with no metadata, or None if the image is not usable."""
    kind = filetype.guess(data)
    if kind is None or kind.mime not in _ACCEPTED:
        return None
    try:
        im = _open_image(data)  # refuses too many pixels before decoding; a GIF gives frame 1
        im = ImageOps.exif_transpose(im)  # apply the camera's rotation before it is discarded
        if im.width < MIN_WIDTH or im.height < MIN_HEIGHT:
            return None
        if im.width > card.max_width:  # never upscale
            height = max(1, round(im.height * card.max_width / im.width))
            im = im.resize((card.max_width, height), Image.Resampling.LANCZOS)
        mode = "RGBA" if im.has_transparency_data else "RGB"
        # A fresh image object guarantees no metadata rides along; nothing from im.info is saved.
        clean = Image.new(mode, im.size)
        clean.paste(im.convert(mode))
        out = io.BytesIO()
        clean.save(out, "WEBP", quality=card.quality)
    except (MediaError, OSError, ValueError, Image.DecompressionBombError):
        return None
    return CardImage(out.getvalue(), clean.width, clean.height)


async def _download(url: str, resolver: Resolver, max_bytes: int) -> bytes | None:
    """The body of an image, checking every redirect hop and stopping at max_bytes."""
    async with guarded_client(resolver, timeout=6, follow_redirects=False) as client:
        for _ in range(HOP_LIMIT):
            assert_fetchable(url, resolver=resolver)
            headers = {"Accept": "image/*", "User-Agent": USER_AGENT}
            async with client.stream("GET", url, headers=headers) as response:
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code != 200:
                    return None
                body = bytearray()
                async for chunk in response.aiter_bytes():  # Content-Length is not trusted
                    body += chunk
                    if len(body) > max_bytes:
                        return None
                return bytes(body)
    return None  # too many redirects


async def fetch_card(
    url: str,
    resolver: Resolver = system_resolver,
    card: MediaCard | None = None,
    timeout: float = TOTAL_TIMEOUT,
) -> CardImage | None:
    """Never raises: None means there is no image for the card."""
    card = card or MediaCard()
    try:
        async with asyncio.timeout(timeout):
            data = await _download(url, resolver, card.max_bytes)
        return process_card(data, card) if data else None
    except (FetchError, httpx.HTTPError, ValueError, TimeoutError):
        return None
