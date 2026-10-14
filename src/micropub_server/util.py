"""Small helpers shared across modules."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, drop default ports, give an empty path a slash."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and _DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def base_domain(url: str) -> str:
    """The host of a URL without a leading www., as the site's bookmark layout shows it."""
    return (urlsplit(url).hostname or "").removeprefix("www.")
