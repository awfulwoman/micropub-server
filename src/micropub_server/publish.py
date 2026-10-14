"""Create posts: normalise, route, render, commit, record (spec 03)."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from uuid_utils import uuid7

from .app import AppState
from .media import Processed
from .posts import discover_post_type, normalise, render_template
from .stores import Move, Put, StoreError
from .templates import render_post
from .util import to_list

UPLOAD = "upload:"  # placeholder URL for a file sent with the post: upload:<property>:<index>
MEDIA_PROPERTIES = ("photo", "video", "audio")


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


def _unique_name(name: str, used: set[str]) -> str:
    candidate, k = name, 2
    stem, ext = posixpath.splitext(name)
    while candidate in used:
        candidate = f"{stem}-{k}{ext}"
        k += 1
    used.add(candidate)
    return candidate


async def _place_media(
    s: AppState,
    props: dict[str, Any],
    post_path: str,
    page_url: str,
    uploads: dict[str, list[Processed]],
    post_uid: str,
) -> tuple[list, list]:
    """Decide where each photo, video and audio file lives.

    Returns (store changes, database actions to apply after the commit).
    Files for a page-bundle post go next to its index.md; others go to static media.
    """
    from .media_service import Prepared, prepare_media

    changes: list = []
    actions: list = []
    bundle_dir = posixpath.dirname(post_path) if post_path.endswith("/index.md") else None
    used: set[str] = set()

    for prop in MEDIA_PROPERTIES:
        for entry in props.get(prop) or []:
            url = entry["url"]
            if url.startswith(UPLOAD):
                _, source, index = url.split(":")
                processed = uploads[source][int(index)]
                if bundle_dir:
                    name = _unique_name(processed.filename, used)
                    path = f"{bundle_dir}/{name}"
                    changes.append(Put(path, processed.content))
                    prepared = Prepared(
                        str(uuid7()),
                        path,
                        page_url + name,
                        processed,
                        props["published"],
                        False,
                        False,
                    )
                    actions.append(("insert", prepared, post_uid))
                    entry["url"] = name
                else:
                    prepared = await prepare_media(s, processed)
                    if not prepared.existing:
                        changes.append(Put(prepared.path, processed.content))
                    actions.append(
                        ("insert", prepared, post_uid if not prepared.recorded else None)
                    )
                    entry["url"] = prepared.url.removeprefix(s.me.rstrip("/"))
                continue

            row = s.db.query(
                "SELECT uid, path, post_uid FROM media WHERE url = ?", (urljoin(s.me, url),)
            )
            if bundle_dir and row and row[0]["post_uid"] is None:
                name = _unique_name(posixpath.basename(row[0]["path"]), used)
                new_path = f"{bundle_dir}/{name}"
                changes.append(Move(row[0]["path"], new_path))
                actions.append(("move", row[0]["uid"], new_path, page_url + name, post_uid))
                entry["url"] = name
    return changes, actions


async def create_post(
    s: AppState,
    jf2: dict[str, Any],
    *,
    draft: bool,
    uploads: dict[str, list[Processed]] | None = None,
) -> Created:
    jf2.pop("uid", None)  # never trust a client-chosen uid
    if "mp-syndicate-to" in jf2 and not s.config.syndication:
        del jf2["mp-syndicate-to"]

    uploads = uploads or {}
    for prop, files in uploads.items():
        jf2[prop] = to_list(jf2.get(prop)) + [f"{UPLOAD}{prop}:{i}" for i in range(len(files))]

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

    media_changes, media_actions = await _place_media(s, props, path, url, uploads, uid)

    content = render_post(props, s.config.template)
    message = s.config.publication.commit_message.format(
        action="create", post_type=post_type, file_type="post"
    )
    try:
        await s.store.commit([Put(path, content.encode()), *media_changes], message)
    except StoreError as exc:
        raise MicropubError("store_error", str(exc), 502) from exc

    from .media_service import record_media

    with s.db.transaction():
        s.db.execute(
            "INSERT INTO posts (uid, url, path, post_type, post_status, published, properties)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                url,
                path,
                post_type,
                props["post-status"],
                props["published"],
                json.dumps(props),
            ),
        )
        for action in media_actions:
            if action[0] == "insert":
                record_media(s, action[1], action[2])
            else:
                _, media_uid, new_path, new_url, post_uid = action
                s.db.execute(
                    "UPDATE media SET path = ?, url = ?, post_uid = ? WHERE uid = ?",
                    (new_path, new_url, post_uid, media_uid),
                )
    return Created(url=url, path=path, uid=uid, post_type=post_type)
