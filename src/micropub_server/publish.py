"""Create posts: normalise, route, render, commit, record (spec 03)."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from uuid_utils import uuid7

from .app import AppState
from .media import Processed
from .posts import discover_post_type, normalise, render_template
from .stores import Delete, Move, Put, StoreError
from .templates import render_post
from .update import UpdateError, apply_update
from .util import base_domain, to_list

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


def _count_same_day(s: AppState, post_type: str, published: str, exclude_uid: str = "") -> int:
    row = s.db.query(
        "SELECT COUNT(*) FROM posts WHERE post_type = ? AND substr(published, 1, 10) = ? AND uid != ?",
        (post_type, published[:10], exclude_uid),
    )
    return row[0][0]


async def _taken(s: AppState, path: str, url: str, exclude_uid: str = "") -> bool:
    if s.db.query(
        "SELECT 1 FROM posts WHERE (path = ? OR url = ?) AND uid != ?", (path, url, exclude_uid)
    ):
        return True
    return await s.store.exists(path)


async def _choose_path(
    s: AppState, props: dict[str, Any], post_type: str, cfg, exclude_uid: str = ""
) -> tuple[str, str]:
    """Render a post's path and absolute URL, adding -2, -3 ... if they are taken."""
    tz = s.config.application.time_zone
    sep = s.config.publication.slug_separator
    n = _count_same_day(s, post_type, props["published"], exclude_uid) + 1
    path = render_template(cfg.post.path, props, tz=tz, n=n, separator=sep)
    url_path = render_template(cfg.post.url or cfg.post.path, props, tz=tz, n=n, separator=sep)
    candidate_path, candidate_url = path, url_path
    for k in range(2, 100):
        if not await _taken(s, candidate_path, absolute_url(s.me, candidate_url), exclude_uid):
            return candidate_path, absolute_url(s.me, candidate_url)
        candidate_path, candidate_url = _suffix(path, url_path, k)
    raise MicropubError("invalid_request", "Could not find a free path for this post", 409)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def resolve_targets(s: AppState, requested: Any) -> list[str]:
    """The uids of configured targets that a client asked for, by uid or by origin."""
    wanted = (
        [r for r in to_list(requested) if isinstance(r, str)]
        if not isinstance(requested, dict)
        else []
    )
    return [
        t.uid for t in s.targets if any(r == t.uid or _origin(r) == _origin(t.uid) for r in wanted)
    ]


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
    jf2.pop("uid", None)  # never trust a client-chosen uid or slug
    jf2.pop("slug", None)
    if "mp-syndicate-to" in jf2:
        known = resolve_targets(s, jf2["mp-syndicate-to"])
        if known:
            jf2["mp-syndicate-to"] = known
        else:
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

    post_type = discover_post_type(s.post_types, props)
    cfg = s.post_types.get(post_type)
    if cfg is None:
        raise MicropubError(
            "not_implemented", f"No post type is configured for {post_type} posts", 501
        )
    if props.get("bookmark-of"):
        props["basedomain"] = base_domain(
            str(props["bookmark-of"])
        )  # the layout shows "Original link on ..."

    uid = str(uuid7())
    props["uid"] = uid  # so a {uuid} token in a path template works
    path, url = await _choose_path(s, props, post_type, cfg)

    props["url"] = url
    props["uid"] = uid
    props["post-type"] = post_type
    props["post-status"] = "draft" if draft else props.get("post-status", "published")

    media_changes, media_actions = await _place_media(s, props, path, url, uploads, uid)

    content = render_post(props, s.template)
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
                json.dumps(props, default=str),
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


# --- update, delete, undelete ---------------------------------------------------
@dataclass(frozen=True)
class Updated:
    url: str
    changed: bool
    moved: bool


def _find_post(s: AppState, url: str):
    rows = s.db.query("SELECT * FROM posts WHERE url IN (?, ?)", (url, url.rstrip("/") + "/"))
    if not rows:
        raise MicropubError("invalid_request", f"No post found at {url}")
    return rows[0]


def _message(s: AppState, action: str, post_type: str) -> str:
    return s.config.publication.commit_message.format(
        action=action, post_type=post_type, file_type="post"
    )


async def _commit(s: AppState, changes: list, message: str) -> None:
    try:
        await s.store.commit(changes, message)
    except StoreError as exc:
        raise MicropubError("store_error", str(exc), 502) from exc


def _comparable_all(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if k not in {"updated", "url", "post-type"}}


def _comparable(props: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in props.items()
        if k not in {"updated", "url", "post-type"} and not k.startswith("mp-")
    }


async def update_post(
    s: AppState,
    url: str,
    *,
    add: Any = None,
    replace: Any = None,
    delete: Any = None,
    draft_mode: bool = False,
) -> Updated:
    row = _find_post(s, url)
    if row["deleted"]:
        raise MicropubError("invalid_request", f"The post at {url} has been deleted")
    old = json.loads(row["properties"])
    if draft_mode and old.get("post-status") != "draft":
        raise MicropubError(
            "insufficient_scope", "The update scope is required", 403, scope="update"
        )

    try:
        edited = apply_update(old, add=add, replace=replace, delete=delete)
    except UpdateError as exc:
        raise MicropubError("invalid_request", str(exc)) from exc
    if draft_mode:
        edited["post-status"] = "draft"

    app = s.config.application
    props = normalise(
        edited,
        tz=app.time_zone,
        me=s.me,
        slug_separator=s.config.publication.slug_separator,
        channels=list(s.config.channels),
        now=s.clock(),
    )
    # An edit only changes a post's type if it changes what discovery says about it. Otherwise a
    # note with a title (which discovery calls an article) would jump sections on its first edit.
    discovered = discover_post_type(s.post_types, props)
    post_type = (
        discovered if discovered != discover_post_type(s.post_types, old) else row["post_type"]
    )
    cfg = s.post_types.get(post_type)
    if cfg is None:
        raise MicropubError(
            "not_implemented", f"No post type is configured for {post_type} posts", 501
        )
    props["uid"], props["post-type"] = row["uid"], post_type
    if props.get("bookmark-of") != old.get("bookmark-of") and (
        "basedomain" in props or "basedomain" in props.get("front-matter", {})
    ):
        props.get("front-matter", {}).pop("basedomain", None)
        if props.get("bookmark-of"):
            props["basedomain"] = base_domain(str(props["bookmark-of"]))
        else:
            props.pop("basedomain", None)

    old_path, old_url = row["path"], row["url"]
    shape_changed = (
        post_type != row["post_type"]
        or props["slug"] != old.get("slug")
        or props["published"] != old.get("published")
    )
    same_file = render_post(_comparable(props) | {"url": ""}, s.template) == render_post(
        _comparable(old) | {"url": ""}, s.template
    )
    if same_file and not shape_changed:
        if _comparable_all(props) != _comparable_all(old):
            # only server-side commands changed (mp-syndicate-to ...): save them, nothing to commit
            s.db.execute(
                "UPDATE posts SET properties = ? WHERE uid = ?",
                (json.dumps({**props, "url": old_url}, default=str), row["uid"]),
            )
        return Updated(url=row["url"], changed=False, moved=False)

    if shape_changed:
        new_path, new_url = await _choose_path(s, props, post_type, cfg, exclude_uid=row["uid"])
    else:
        new_path, new_url = old_path, old_url
    moved = new_path != old_path

    props["url"] = new_url
    props["updated"] = (
        s.clock()
        .astimezone(ZoneInfo(app.time_zone) if app.time_zone != "client" else UTC)
        .isoformat(timespec="seconds")
    )
    content = render_post(props, s.template).encode()

    changes: list = [Move(old_path, new_path, content) if moved else Put(old_path, content)]
    media_rows = []
    if moved and old_path.endswith("/index.md") and new_path.endswith("/index.md"):
        new_dir = posixpath.dirname(new_path)
        for m in s.db.query("SELECT uid, path FROM media WHERE post_uid = ?", (row["uid"],)):
            name = posixpath.basename(m["path"])
            changes.append(Move(m["path"], f"{new_dir}/{name}"))
            media_rows.append((f"{new_dir}/{name}", new_url + name, m["uid"]))
    await _commit(s, changes, _message(s, "update", post_type))

    with s.db.transaction():
        s.db.execute(
            "UPDATE posts SET url = ?, path = ?, post_type = ?, post_status = ?, published = ?,"
            " updated = ?, properties = ? WHERE uid = ?",
            (
                new_url,
                new_path,
                post_type,
                props["post-status"],
                props["published"],
                props["updated"],
                json.dumps(props, default=str),
                row["uid"],
            ),
        )
        for path, media_url, media_uid in media_rows:
            s.db.execute(
                "UPDATE media SET path = ?, url = ? WHERE uid = ?", (path, media_url, media_uid)
            )
    return Updated(url=new_url, changed=True, moved=moved or new_url != old_url)


async def delete_post(s: AppState, url: str) -> str:
    row = _find_post(s, url)
    if row["deleted"]:
        return row["url"]  # already gone: nothing to do
    props = json.loads(row["properties"])
    if await s.store.exists(row["path"]):
        await _commit(s, [Delete(row["path"])], _message(s, "delete", row["post_type"]))
    kept = {
        k: props[k] for k in ("post-type", "published", "slug", "type", "uid", "url") if k in props
    }
    now = s.clock().astimezone(UTC).isoformat(timespec="seconds")
    s.db.execute(
        "UPDATE posts SET deleted = ?, deleted_properties = ?, properties = ? WHERE uid = ?",
        (now, row["properties"], json.dumps({**kept, "deleted": now}), row["uid"]),
    )
    return row["url"]


async def undelete_post(s: AppState, url: str, *, draft_mode: bool = False) -> str:
    row = _find_post(s, url)
    if not row["deleted"] or not row["deleted_properties"]:
        raise MicropubError("invalid_request", f"The post at {url} has not been deleted")
    props = json.loads(row["deleted_properties"])
    if draft_mode:
        props["post-status"] = "draft"
    content = render_post(props, s.template).encode()
    await _commit(s, [Put(row["path"], content)], _message(s, "undelete", row["post_type"]))
    s.db.execute(
        "UPDATE posts SET deleted = NULL, deleted_properties = NULL, post_status = ?, properties = ?"
        " WHERE uid = ?",
        (props.get("post-status", "published"), json.dumps(props, default=str), row["uid"]),
    )
    return row["url"]
