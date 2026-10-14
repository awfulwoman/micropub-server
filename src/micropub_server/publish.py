"""Create posts: normalise, route, render, commit, record (spec 03)."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from uuid_utils import uuid7

from .app import AppState
from .posts import discover_post_type, normalise, render_template
from .stores import Put, StoreError
from .templates import render_post


class MicropubError(Exception):
    def __init__(self, error: str, description: str, status: int = 400, **extra: Any):
        super().__init__(description)
        self.error, self.description, self.status, self.extra = error, description, status, extra


@dataclass(frozen=True)
class Created:
    url: str
    path: str
    uid: str
    post_type: str


def absolute_url(me: str, url_path: str) -> str:
    url = urljoin(me, url_path)
    last = url.rstrip("/").rsplit("/", 1)[-1]
    return url if url.endswith("/") or "." in last else url + "/"


def _suffix(path: str, url_path: str, k: int) -> tuple[str, str]:
    """Make a colliding path and URL unique by suffixing the filename (or bundle directory)."""
    directory, filename = posixpath.split(path)
    if filename == "index.md":
        parent, bundle = posixpath.split(directory)
        new_path = posixpath.join(parent, f"{bundle}-{k}", filename)
    else:
        stem, ext = posixpath.splitext(filename)
        new_path = posixpath.join(directory, f"{stem}-{k}{ext}")
    trailing = "/" if url_path.endswith("/") else ""
    return new_path, f"{url_path.rstrip('/')}-{k}{trailing}"


def _count_same_day(s: AppState, post_type: str, published: str) -> int:
    row = s.db.query(
        "SELECT COUNT(*) FROM posts WHERE post_type = ? AND substr(published, 1, 10) = ?",
        (post_type, published[:10]),
    )
    return row[0][0]


async def _taken(s: AppState, path: str, url: str) -> bool:
    if s.db.query("SELECT 1 FROM posts WHERE path = ? OR url = ?", (path, url)):
        return True
    return await s.store.exists(path)


async def create_post(s: AppState, jf2: dict[str, Any], *, draft: bool) -> Created:
    jf2.pop("uid", None)  # never trust a client-chosen uid
    if "mp-syndicate-to" in jf2 and not s.config.syndication:
        del jf2["mp-syndicate-to"]

    app = s.config.application
    props = normalise(
        jf2,
        tz=app.time_zone,
        me=s.me,
        slug_separator=s.config.publication.slug_separator,
        channels=list(s.config.channels),
        now=s.clock(),
    )

    post_type = discover_post_type(s.config.post_types, props)
    cfg = s.config.post_types.get(post_type)
    if cfg is None:
        raise MicropubError(
            "not_implemented", f"No post type is configured for {post_type} posts", 501
        )

    sep = s.config.publication.slug_separator
    n = _count_same_day(s, post_type, props["published"]) + 1
    path = render_template(cfg.post.path, props, tz=app.time_zone, n=n, separator=sep)
    url_path = render_template(
        cfg.post.url or cfg.post.path, props, tz=app.time_zone, n=n, separator=sep
    )

    candidate_path, candidate_url = path, url_path
    for k in range(2, 100):
        if not await _taken(s, candidate_path, absolute_url(s.me, candidate_url)):
            break
        candidate_path, candidate_url = _suffix(path, url_path, k)
    else:
        raise MicropubError("invalid_request", "Could not find a free path for this post", 409)
    path, url = candidate_path, absolute_url(s.me, candidate_url)

    uid = str(uuid7())
    props["url"] = url
    props["uid"] = uid
    props["post-type"] = post_type
    props["post-status"] = "draft" if draft else props.get("post-status", "published")

    content = render_post(props, s.config.template)
    message = s.config.publication.commit_message.format(
        action="create", post_type=post_type, file_type="post"
    )
    try:
        await s.store.commit([Put(path, content.encode())], message)
    except StoreError as exc:
        raise MicropubError("store_error", str(exc), 502) from exc

    s.db.execute(
        "INSERT INTO posts (uid, url, path, post_type, post_status, published, properties)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, url, path, post_type, props["post-status"], props["published"], json.dumps(props)),
    )
    return Created(url=url, path=path, uid=uid, post_type=post_type)
