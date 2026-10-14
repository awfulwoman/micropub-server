"""Outbound fetches for IndieAuth: SSRF guard, client info, redirect validation."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import mf2py

FETCH_TIMEOUT = 5.0
MAX_BYTES = 1_000_000
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], list[str]]


class FetchError(Exception):
    pass


def system_resolver(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host}") from exc


def _blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in _TAILSCALE)
    )


def assert_fetchable(url: str, *, resolver: Resolver = system_resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError(f"not a fetchable URL: {url}")
    host = parts.hostname
    try:
        addresses = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise FetchError(f"cannot resolve {host}")
    for address in addresses:
        if _blocked(address):
            raise FetchError(f"refusing to fetch {url}: {address} is not a public address")


async def _fetch(url: str, resolver: Resolver) -> httpx.Response:
    assert_fetchable(url, resolver=resolver)
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers={"Accept": "text/html, application/json"})
        except httpx.HTTPError as exc:
            raise FetchError(str(exc)) from exc
    if len(response.content) > MAX_BYTES:
        raise FetchError("response too large")
    return response


async def fetch_client_info(
    client_id: str, *, resolver: Resolver = system_resolver
) -> dict[str, Any] | None:
    try:
        response = await _fetch(client_id, resolver)
    except FetchError:
        return None
    if response.status_code != 200:
        return None
    if "json" in response.headers.get("content-type", ""):
        try:
            data = response.json()
        except ValueError:
            return None
        if data.get("client_id"):
            return {
                "name": data.get("client_name"),
                "url": data.get("client_uri"),
                "logo": data.get("logo_uri"),
            }
        return None
    parsed = mf2py.parse(doc=response.text, url=client_id)
    for item in parsed["items"]:
        if {"h-app", "h-x-app"} & set(item.get("type", [])):
            props = item["properties"]
            return {
                "name": _first(props.get("name")),
                "url": _first(props.get("url")),
                "logo": _first(props.get("logo")),
            }
    return None


def _first(values: list[Any] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    if isinstance(value, dict):
        return value.get("value") or value.get("url")
    return value


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    default = {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or default


async def validate_redirect(
    redirect_uri: str, client_id: str, *, resolver: Resolver = system_resolver
) -> bool:
    try:
        if _origin(redirect_uri) == _origin(client_id):
            return True
    except ValueError:
        return False
    try:
        response = await _fetch(client_id, resolver)
    except FetchError:
        return False
    declared = [
        link["url"]
        for link in response.links.values()
        if "redirect_uri" in link.get("rel", "").split()
    ]
    if "html" in response.headers.get("content-type", "text/html"):
        declared += mf2py.parse(doc=response.text, url=client_id)["rels"].get("redirect_uri", [])
    return redirect_uri in declared
