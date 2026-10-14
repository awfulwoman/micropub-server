"""What every syndication target provides (spec 08)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

# Given one photo entry from a post, return (bytes, filename).
MediaFetcher = Callable[[dict[str, Any]], Awaitable[tuple[bytes, str]]]


class SyndicationError(Exception):
    """The target failed; the post stays queued for another try."""


class NotApplicable(Exception):  # noqa: N818 - reads better as a condition than as an error
    """This post cannot be mirrored on this target (for example a like of another site's post)."""


class Target(Protocol):
    uid: str

    @property
    def info(self) -> dict[str, Any]:
        """The description clients see in q=syndicate-to."""
        ...

    async def syndicate(self, props: dict[str, Any], fetch_media: MediaFetcher) -> str:
        """Share the post. Return the URL of the copy."""
        ...
