"""Facts about the page a like, quote or bookmark points at (title, author, an embed).

Fetched once, when the post is created or its link changes, and saved in the post's front matter,
so the site never has to fetch anything while it builds. Everything here fails soft: a page that
cannot be fetched or understood gives an empty result and the site falls back to the bare URL.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
import mf2py
import nh3
from bs4 import BeautifulSoup

from .auth.client import FetchError, Resolver, assert_fetchable, guarded_client, system_resolver

MAX_PAGE_CHARS = 500_000
MAX_TEXT_CHARS = 1_500
MAX_TITLE_CHARS = 200
TOTAL_TIMEOUT = 12
HOP_LIMIT = 4
USER_AGENT = "micropub-server link metadata"

# Which front matter prefix each kind of post uses, and which facts it keeps.
KINDS = {
    "like": ("title", "author", "text", "oembed"),
    "quote": ("title", "author", "text", "oembed"),
    "bookmark": ("author",),  # a bookmark already has its own title
}
TARGET_PROPS = {"like": "like-of", "quote": "quotation-of", "bookmark": "bookmark-of"}

_TWITTER_HOSTS = {"twitter.com", "x.com", "mobile.twitter.com"}
_SUFFIX_SEPARATORS = (" | ", " – ", " — ", " - ", " · ", " :: ")
_OEMBED_TAGS = {"blockquote", "p", "a", "br", "iframe", "div", "span", "em", "strong"}
_OEMBED_ATTRS = {
    "a": {"href"},
    "iframe": {"src", "width", "height", "class", "allowfullscreen", "title", "style"},
    "blockquote": {"class", "lang", "dir"},
    "p": {"lang", "dir"},
    "div": {"class"},
    "span": {"class"},
}


@dataclass
class TargetMeta:
    title: str = ""
    author: str = ""
    text: str = ""  # the original words, kept only to back up an embed that fails to load
    oembed: str = ""
    entry_text: str = field(default="", compare=False)  # text the page itself gave, if any

    def as_props(self, kind: str) -> dict[str, str]:
        """Micropub-style properties for this kind of post, empty ones left out."""
        return {
            f"{kind}-{name}": getattr(self, name)
            for name in KINDS.get(kind, ())
            if getattr(self, name)
        }


def _clean(value: Any, limit: int = MAX_TITLE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_NOT_NAMES = {"admin", "administrator", "editor", "author", "user", "root", "webmaster", "staff"}


def clean_author(value: Any) -> str:
    """A person's or organisation's name, or "" for the IDs, URLs and placeholders sites put there."""
    name = re.sub(r"^by\b:?\s*", "", _clean(value), flags=re.IGNORECASE)
    lowered = name.lower()
    if (
        not name
        or name.isdigit()
        or lowered.startswith(("http://", "https://"))
        or lowered in _NOT_NAMES
        or lowered.startswith("admin_")
    ):
        return ""
    return name


def _strip_site(title: str, site: str) -> str:
    for sep in _SUFFIX_SEPARATORS:
        if site and title.endswith(sep + site):
            return title[: -len(sep + site)].strip()
    return title


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _entries(items: list[dict]) -> list[dict]:
    found = []
    for item in items:
        if "h-entry" in item.get("type", []):
            found.append(item)
        found.extend(_entries(item.get("children", [])))
    return found


def _author_name(value: Any) -> str:
    value = _first(value)
    if isinstance(value, dict):
        name = _first(value.get("properties", {}).get("name")) or value.get("value")
        return _clean(name) if name and not str(name).startswith("http") else ""
    if isinstance(value, str) and not value.startswith(("http://", "https://")):
        return _clean(value)
    return ""


def _json_ld_author(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            author = node.get("author") if isinstance(node, dict) else None
            author = _first(author)
            name = author.get("name") if isinstance(author, dict) else author
            if isinstance(name, str) and name.strip():
                return _clean(name)
    return ""


def extract_page(html: str, url: str) -> TargetMeta:
    """Title and author from a page, best source first: its h-entry, then OpenGraph, then HTML."""
    if not html.strip():
        return TargetMeta()
    soup = BeautifulSoup(html, "html.parser")
    meta_tags = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key and tag.get("content"):
            meta_tags.setdefault(key, tag["content"])
    site = _clean(meta_tags.get("og:site_name"))

    title = author = entry_text = ""
    implied = False
    try:
        parsed = mf2py.parse(doc=html, url=url)
    except Exception:  # a hostile page must not break posting
        parsed = {"items": []}
    entries = _entries(parsed["items"])
    if entries:
        props = entries[0]["properties"]
        content = _first(props.get("content"))
        entry_text = (content.get("value") if isinstance(content, dict) else content) or ""
        name = _clean(_first(props.get("name")))
        # a note with no title gets its whole text as an implied name: that is not a title
        implied = bool(entry_text.strip()) and (
            not name or entry_text.strip().startswith(name[:40])
        )
        title = "" if implied else name
        author = _author_name(props.get("author"))

    if not title and not implied:
        title = _clean(meta_tags.get("og:title")) or _clean(
            soup.title.get_text() if soup.title else ""
        )
    # the first source that holds a real name: a placeholder in one must not hide the next
    author = next(
        filter(
            None,
            (
                clean_author(author),
                clean_author(meta_tags.get("author")),
                clean_author(meta_tags.get("article:author")),
                clean_author(_json_ld_author(soup)),
            ),
        ),
        "",
    )
    return TargetMeta(
        title=_strip_site(title, site),
        author=author,
        entry_text=_text_block(entry_text),
    )


def _text_block(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1].rstrip() + "…"


def oembed_endpoint(html: str, url: str) -> str | None:
    """Where to ask for an embed: Twitter's endpoint, or the one the page itself advertises."""
    parts = urlsplit(url)
    if (parts.hostname or "").removeprefix("www.") in _TWITTER_HOSTS:
        return "https://publish.twitter.com/oembed?omit_script=1&dnt=true&url=" + quote(
            url, safe=""
        )
    if html:
        link = BeautifulSoup(html, "html.parser").find(
            "link", rel="alternate", type="application/json+oembed"
        )
        if link and link.get("href"):
            return urljoin(url, link["href"])
    return None


def clean_oembed_html(html: str) -> str:
    """The provider's markup with scripts, handlers and non-https frames removed."""
    cleaned = nh3.clean(
        html or "",
        tags=_OEMBED_TAGS,
        attributes=_OEMBED_ATTRS,
        url_schemes={"https", "http"},
        link_rel=None,
    )
    soup = BeautifulSoup(cleaned, "html.parser")
    for frame in soup.find_all("iframe"):
        if not str(frame.get("src", "")).startswith("https://"):
            frame.decompose()
    return str(soup).strip()


def _embed_text(html: str) -> str:
    """The words of an embedded post: its paragraphs, without the byline after them."""
    soup = BeautifulSoup(html or "", "html.parser")
    paragraphs = soup.find_all("p")
    if not paragraphs:
        return ""
    blocks = []
    for p in paragraphs:
        for br in p.find_all("br"):
            br.replace_with("\n")
        blocks.append(p.get_text())
    return _text_block("\n\n".join(blocks))


def apply_oembed(meta: TargetMeta, data: dict[str, Any]) -> TargetMeta:
    html = clean_oembed_html(str(data.get("html") or ""))
    if not html:
        return meta
    text = meta.text or meta.entry_text or _embed_text(html)
    return TargetMeta(
        title=meta.title,
        author=meta.author or clean_author(data.get("author_name")),
        text=text,
        oembed=html,
        entry_text=meta.entry_text,
    )


async def _get(client: httpx.AsyncClient, url: str, resolver: Resolver, accept: str):
    """GET, checking every redirect hop so none can lead to a private address."""
    for _ in range(HOP_LIMIT):
        assert_fetchable(url, resolver=resolver)
        response = await client.get(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
        if response.is_redirect and response.headers.get("location"):
            url = urljoin(url, response.headers["location"])
            continue
        return response
    raise FetchError("too many redirects")


async def _fetch(url: str, resolver: Resolver) -> TargetMeta:
    meta = TargetMeta()
    html = ""
    on_twitter = (urlsplit(url).hostname or "").removeprefix("www.") in _TWITTER_HOSTS
    async with guarded_client(resolver, timeout=6, follow_redirects=False) as client:
        if not on_twitter:  # the page itself is empty without scripts
            response = await _get(client, url, resolver, "text/html")
            if response.status_code == 200 and "html" in response.headers.get("content-type", ""):
                html = response.text[:MAX_PAGE_CHARS]
                meta = extract_page(html, url)
        endpoint = oembed_endpoint(html, url)
        if endpoint and endpoint.startswith("https://"):
            try:  # a missing embed must not lose the title and author already found
                response = await _get(client, endpoint, resolver, "application/json")
                data = response.json() if response.status_code == 200 else None
            except (FetchError, httpx.HTTPError, ValueError):
                data = None
            if isinstance(data, dict) and len(response.content) < 200_000:
                meta = apply_oembed(meta, data)
    return meta


async def fetch_target_meta(url: str, resolver: Resolver = system_resolver) -> TargetMeta:
    """Never raises: an empty result means nothing could be learned."""
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            return await _fetch(url, resolver)
    except (FetchError, httpx.HTTPError, ValueError, TimeoutError):
        return TargetMeta()
