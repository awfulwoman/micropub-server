"""Create posts: normalise, route, render, commit, record (spec 03)."""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from uuid_utils import uuid7

from .app import AppState
from .card_image import CARD_FILENAME
from .deploys import record as record_deploy
from .media import Processed
from .posts import discover_post_type, normalise, render_template
from .stores import Delete, Move, Put, StoreError
from .target_enrich import (
    CARD_KINDS,
    Card,
    card_image,
    enrich_props,
    image_key,
    refetch_card,
    refresh_props,
)
from .templates import render_post
from .topics import MAX_TOPICS, topics_for_tags
from .update import UpdateError, apply_update
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


def render_file(s: AppState, props: dict[str, Any]) -> str:
    """Render a post's file, including the post type's configured aliases."""
    cfg = s.config.post_types.get(props.get("post-type", ""))
    aliases = []
    if cfg and cfg.post.aliases and props.get("published"):
        tz = s.config.application.time_zone
        sep = s.config.publication.slug_separator
        aliases = [render_template(a, props, tz=tz, separator=sep) for a in cfg.post.aliases]
    return render_post(props, s.template, aliases)


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


def _set_topics(
    s: AppState, props: dict[str, Any], explicit: list[Any] | None, derive: bool
) -> None:
    """Spec 15: topics a client sent (mp-topic), else from the tags if `derive`, else unchanged."""
    extras = props.get("front-matter")
    if isinstance(extras, dict) and "topics" in extras:  # imported: the file's topics
        props.setdefault("topic", [str(t) for t in to_list(extras.pop("topics"))])
    if not s.topic_map:
        return
    if explicit is not None:
        if len(explicit) > MAX_TOPICS:
            raise MicropubError("invalid_request", f"A post can have at most {MAX_TOPICS} topics")
        wanted = [str(t).strip().lower() for t in explicit]
        topics = list(dict.fromkeys(t for t in wanted if t in s.topic_map))
    elif derive:
        topics = topics_for_tags([str(t) for t in to_list(props.get("category"))], s.topic_map)
    else:
        return
    if topics:
        props["topic"] = topics
    else:
        props.pop("topic", None)


def _keys(op: Any) -> set[str]:
    if isinstance(op, dict):
        return set(op)
    return {k for k in op if isinstance(k, str)} if isinstance(op, list) else set()


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


def _card_media(s: AppState, card: Card, post_path: str, page_url: str, post_uid: str):
    """The card image as a store change plus the media row to record once it is committed."""
    from .media_service import Prepared

    image = card.image
    processed = Processed(
        image.content,
        image.filename,
        ".webp",
        "photo",
        "image/webp",
        hashlib.md5(image.content).hexdigest(),
    )
    published = s.clock().isoformat(timespec="seconds")
    path = posixpath.join(posixpath.dirname(post_path), CARD_FILENAME)
    prepared = Prepared(
        str(uuid7()), path, page_url + CARD_FILENAME, processed, published, False, False
    )
    return Put(path, image.content), ("insert", prepared, post_uid)


async def create_post(
    s: AppState,
    jf2: dict[str, Any],
    *,
    draft: bool,
    uploads: dict[str, list[Processed]] | None = None,
    expect_type: str | None = None,
) -> Created:
    jf2.pop("uid", None)  # never trust a client-chosen uid or slug
    jf2.pop("slug", None)
    # A client may say which type it means (mp-post-type: note), so a titled note stays a note.
    hint = to_list(jf2.pop("mp-post-type", None))
    expect_type = expect_type or (str(hint[0]) if hint else None)
    if "mp-syndicate-to" in jf2:
        known = resolve_targets(s, jf2["mp-syndicate-to"])
        if known:
            jf2["mp-syndicate-to"] = known
        else:
            del jf2["mp-syndicate-to"]

    explicit_topics = to_list(jf2.pop("mp-topic")) if "mp-topic" in jf2 else None

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
    _set_topics(s, props, explicit_topics, derive=True)

    post_type = discover_post_type(s.post_types, props)
    cfg = s.post_types.get(post_type)
    if cfg is None:
        raise MicropubError(
            "not_implemented", f"No post type is configured for {post_type} posts", 501
        )
    if (
        expect_type
        and post_type != expect_type
        and discover_post_type(s.post_types, _untitled(props)) == expect_type
    ):
        post_type, cfg = expect_type, s.post_types.get(expect_type)  # a note with a title
    if expect_type and post_type != expect_type:
        raise MicropubError(
            "invalid_request",
            f"This would be saved as a {post_type} post, not a {expect_type}. Check the fields.",
            422,
        )
    card = await enrich_props(s, props)

    uid = str(uuid7())
    props["uid"] = uid  # so a {uuid} token in a path template works
    path, url = await _choose_path(s, props, post_type, cfg)

    props["url"] = url
    props["uid"] = uid
    props["post-type"] = post_type
    props["post-status"] = "draft" if draft else props.get("post-status", "published")

    media_changes, media_actions = await _place_media(s, props, path, url, uploads, uid)
    if card and path.endswith("/index.md"):  # the image needs a bundle; otherwise text only
        props[image_key(card.kind)] = card.as_prop()
        change, action = _card_media(s, card, path, url, uid)
        media_changes.append(change)
        media_actions.append(action)

    content = render_file(s, props)
    message = s.config.publication.commit_message.format(
        action="create", post_type=post_type, file_type="post"
    )
    try:
        sha = await s.store.commit([Put(path, content.encode()), *media_changes], message)
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
        record_deploy(s, sha, uid, "create")
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


async def _commit(s: AppState, changes: list, message: str) -> str | None:
    try:
        return await s.store.commit(changes, message)
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


def _untitled(props: dict[str, Any]) -> dict[str, Any]:
    """Props without the title: on this site a title alone never makes a note an article."""
    return {k: v for k, v in props.items() if k != "name"}


async def _with_file_front_matter(s: AppState, row, old: dict[str, Any]) -> dict[str, Any]:
    """The post as the database has it, plus anything added to its file outside the server.

    A hand edit or a script (a URL migration, a backfill) can add a title, aliases or a guid to the
    file without the database knowing. The next save renders from the database, so without this
    those keys would be lost. The database wins where both have a value; aliases are combined.
    """
    from .importer import _front_matter_to_jf2, _split

    cfg = s.post_types.get(row["post_type"])
    try:
        if cfg is None or not await s.store.exists(row["path"]):
            return old
        front, body = _split((await s.store.read(row["path"])).decode())
        from_file, extras = _front_matter_to_jf2(front, body, row["post_type"], cfg, row["path"])
    except Exception:  # an unreadable file: carry on with what the database has
        return old
    merged = {**from_file, **old}
    ours = old.get("front-matter") if isinstance(old.get("front-matter"), dict) else {}
    kept = {**extras, **ours}
    aliases = [*to_list(extras.get("aliases")), *to_list(ours.get("aliases"))]
    if aliases:
        kept["aliases"] = list(dict.fromkeys(aliases))
    if kept:
        merged["front-matter"] = kept
    return merged


def _add_alias(props: dict[str, Any], url: str) -> None:
    extras = props.get("front-matter")
    if not isinstance(extras, dict):
        extras = props["front-matter"] = {}
    path = urlsplit(url).path
    extras["aliases"] = list(dict.fromkeys([*to_list(extras.get("aliases")), path]))


async def update_post(
    s: AppState,
    url: str,
    *,
    add: Any = None,
    replace: Any = None,
    delete: Any = None,
    draft_mode: bool = False,
    refetch_card_of: str | None = None,
    card: Card | None = None,
) -> Updated:
    row = _find_post(s, url)
    if row["deleted"]:
        raise MicropubError("invalid_request", f"The post at {url} has been deleted")
    old = json.loads(row["properties"])
    if draft_mode and old.get("post-status") != "draft":
        raise MicropubError(
            "insufficient_scope", "The update scope is required", 403, scope="update"
        )

    old = await _with_file_front_matter(s, row, old)
    try:
        edited = apply_update(old, add=add, replace=replace, delete=delete)
    except UpdateError as exc:
        raise MicropubError("invalid_request", str(exc)) from exc
    if draft_mode:
        edited["post-status"] = "draft"
    touched = _keys(add) | _keys(replace) | _keys(delete)
    if "mp-topic" in edited:
        explicit_topics: list[Any] | None = to_list(edited.pop("mp-topic"))
    elif "topic" in touched:
        explicit_topics = to_list(edited.get("topic"))
    else:
        explicit_topics = None

    app = s.config.application
    props = normalise(
        edited,
        tz=app.time_zone,
        me=s.me,
        slug_separator=s.config.publication.slug_separator,
        channels=list(s.config.channels),
        now=s.clock(),
    )
    _set_topics(s, props, explicit_topics, derive="category" in touched)
    for kind in CARD_KINDS:
        if image_key(kind) in _keys(delete):  # an image the file had before the server knew of it
            props.get("front-matter", {}).pop(image_key(kind).replace("-", "_"), None)
    # An edit only changes a post's type if it changes what discovery says about it. Otherwise a
    # note with a title (which discovery calls an article) would jump sections on its first edit.
    # A title alone never counts: on this site notes may have titles too.
    discovered = discover_post_type(s.post_types, props)
    changed = discover_post_type(s.post_types, _untitled(props)) != discover_post_type(
        s.post_types, _untitled(old)
    )
    post_type = discovered if changed else row["post_type"]
    cfg = s.post_types.get(post_type)
    if cfg is None:
        raise MicropubError(
            "not_implemented", f"No post type is configured for {post_type} posts", 501
        )
    props["uid"], props["post-type"] = row["uid"], post_type
    props.pop("basedomain", None)  # an old Indiekit key; the layout reads bookmark_of
    props.get("front-matter", {}).pop("basedomain", None)

    card = await refresh_props(s, props, old) or card  # `card`: one the caller already fetched
    if refetch_card_of:  # asked for: a new card for the same link, from the page as it is now
        card = await refetch_card(s, props, refetch_card_of) or card
    old_path, old_url = row["path"], row["url"]
    bundle = old_path.endswith("/index.md")
    # A flat bookmark or like from before spec 16 moves into a bundle, at the same address, so it
    # can hold a card image.
    to_bundle = post_type in CARD_KINDS and not bundle and cfg.post.path.endswith("/index.md")
    had_image = bool(card_image(old))
    if card and (bundle or to_bundle):
        props[image_key(card.kind)] = card.as_prop()
    else:
        card = None

    shape_changed = (
        post_type != row["post_type"]
        or props["slug"] != old.get("slug")
        or props["published"] != old.get("published")
    )
    same_file = render_post(_comparable(props) | {"url": ""}, s.template) == render_post(
        _comparable(old) | {"url": ""}, s.template
    )
    if same_file and not shape_changed and not to_bundle:
        if _comparable_all(props) != _comparable_all(old):
            # only server-side commands changed (mp-syndicate-to ...): save them, nothing to commit
            s.db.execute(
                "UPDATE posts SET properties = ? WHERE uid = ?",
                (json.dumps({**props, "url": old_url}, default=str), row["uid"]),
            )
        return Updated(url=row["url"], changed=False, moved=False)

    if shape_changed:
        new_path, new_url = await _choose_path(s, props, post_type, cfg, exclude_uid=row["uid"])
    elif to_bundle:
        new_path, new_url = old_path.removesuffix(".md") + "/index.md", old_url
    else:
        new_path, new_url = old_path, old_url
    moved = new_path != old_path
    if new_url != old_url and props.get("post-status") != "draft":
        _add_alias(props, old_url)  # links to the old address keep working

    props["url"] = new_url
    props["updated"] = (
        s.clock()
        .astimezone(ZoneInfo(app.time_zone) if app.time_zone != "client" else UTC)
        .isoformat(timespec="seconds")
    )
    content = render_file(s, props).encode()

    changes: list = [Move(old_path, new_path, content) if moved else Put(old_path, content)]
    media_rows = []
    media_rows_gone: list[str] = []
    card_actions: list = []
    # A new card replaces the old; an edit that left no image takes the old one away with it.
    replace_card = bool(card) or (had_image and not card_image(props))
    new_card_path = posixpath.join(posixpath.dirname(new_path), CARD_FILENAME)
    stale = (
        {posixpath.join(posixpath.dirname(old_path), CARD_FILENAME)}
        if had_image and replace_card
        else set()
    )
    keep_moving = moved and bundle and new_path.endswith("/index.md")
    if bundle:
        new_dir = posixpath.dirname(new_path)
        for m in s.db.query("SELECT uid, path FROM media WHERE post_uid = ?", (row["uid"],)):
            name = posixpath.basename(m["path"])
            if name == CARD_FILENAME and replace_card:
                stale.add(m["path"])
                media_rows_gone.append(m["uid"])
            elif keep_moving:
                changes.append(Move(m["path"], f"{new_dir}/{name}"))
                media_rows.append((f"{new_dir}/{name}", new_url + name, m["uid"]))
    if card:
        stale.discard(new_card_path)  # written over in place
        change, action = _card_media(s, card, new_path, new_url, row["uid"])
        changes.append(change)
        card_actions.append(action)
    for path in sorted(stale):
        if await s.store.exists(path):
            changes.append(Delete(path))
    from .media_service import record_media

    sha = await _commit(s, changes, _message(s, "update", post_type))

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
        for media_uid in media_rows_gone:
            s.db.execute("DELETE FROM media WHERE uid = ?", (media_uid,))
        for action in card_actions:
            record_media(s, action[1], action[2])
        record_deploy(s, sha, row["uid"], "update")
    return Updated(url=new_url, changed=True, moved=new_url != old_url)


async def delete_post(s: AppState, url: str) -> str:
    row = _find_post(s, url)
    if row["deleted"]:
        return row["url"]  # already gone: nothing to do
    props = json.loads(row["properties"])
    if await s.store.exists(row["path"]):
        sha = await _commit(s, [Delete(row["path"])], _message(s, "delete", row["post_type"]))
        record_deploy(s, sha, row["uid"], "delete")
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
    content = render_file(s, props).encode()
    sha = await _commit(s, [Put(row["path"], content)], _message(s, "undelete", row["post_type"]))
    record_deploy(s, sha, row["uid"], "undelete")
    s.db.execute(
        "UPDATE posts SET deleted = NULL, deleted_properties = NULL, post_status = ?, properties = ?"
        " WHERE uid = ?",
        (props.get("post-status", "published"), json.dumps(props, default=str), row["uid"]),
    )
    return row["url"]
