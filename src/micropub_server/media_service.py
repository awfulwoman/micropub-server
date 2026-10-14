"""Prepare, record and look up uploaded media (spec 04)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any

from uuid_utils import uuid7

from .app import AppState
from .media import Processed
from .posts import render_template
from .publish import absolute_url


@dataclass
class Prepared:
    uid: str
    path: str
    url: str
    processed: Processed
    published: str
    existing: bool  # already stored: nothing to write
    recorded: bool  # already has a database row


def _props(s: AppState, p: Processed, published: str) -> dict[str, Any]:
    return {"published": published, "filename": p.filename, "ext": p.ext, "md5": p.md5}


def _suffix(path: str, url: str, k: int) -> tuple[str, str]:
    def add(value: str) -> str:
        stem, ext = posixpath.splitext(value)
        return f"{stem}-{k}{ext}"

    return add(path), add(url)


async def prepare_media(
    s: AppState, processed: Processed, *, path: str | None = None, url_path: str | None = None
) -> Prepared:
    """Work out where a file goes, reusing an identical file already stored there."""
    tz = s.config.application.time_zone
    published = s.clock().astimezone().isoformat(timespec="seconds")
    if tz != "client":
        from zoneinfo import ZoneInfo

        published = s.clock().astimezone(ZoneInfo(tz)).isoformat(timespec="seconds")
    props = _props(s, processed, published)
    media = s.config.media
    base_path = path or render_template(media.upload_path, props, tz=tz)
    base_url = url_path or render_template(media.url_template, props, tz=tz)

    content_named = "{md5}" in (media.upload_path if path is None else "")
    candidate_path, candidate_url = base_path, base_url
    for k in range(2, 100):
        url = absolute_url(s.me, candidate_url)
        row = s.db.query("SELECT uid, md5 FROM media WHERE url = ?", (url,))
        if row and row[0]["md5"] == processed.md5:
            return Prepared(row[0]["uid"], candidate_path, url, processed, published, True, True)
        on_disk = await s.store.exists(candidate_path)
        if not row and not on_disk:
            return Prepared(str(uuid7()), candidate_path, url, processed, published, False, False)
        if not row and on_disk and content_named:
            # the name contains the content hash, so this is the same file; just record it
            return Prepared(str(uuid7()), candidate_path, url, processed, published, True, False)
        candidate_path, candidate_url = _suffix(base_path, base_url, k)
    raise RuntimeError("could not find a free path for this file")


def record_media(s: AppState, prepared: Prepared, post_uid: str | None = None) -> None:
    if prepared.recorded:
        return
    p = prepared.processed
    s.db.execute(
        "INSERT INTO media (uid, url, path, media_type, content_type, md5, published, post_uid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            prepared.uid,
            prepared.url,
            prepared.path,
            p.media_type,
            p.content_type,
            p.md5,
            prepared.published,
            post_uid,
        ),
    )


def media_properties(row) -> dict[str, Any]:
    return {
        "uid": row["uid"],
        "url": row["url"],
        "content-type": row["content_type"],
        "media-type": row["media_type"],
        "published": row["published"],
    }


async def delete_media(s: AppState, row) -> None:
    """Remove a file from the store and forget it. Raises MicropubError if the store fails."""
    from .publish import MicropubError
    from .stores import Delete, StoreError

    message = s.config.publication.commit_message.format(
        action="delete", post_type=row["media_type"], file_type="file"
    )
    try:
        if await s.store.exists(row["path"]):
            await s.store.commit([Delete(row["path"])], message)
    except StoreError as exc:
        raise MicropubError("store_error", str(exc), 502) from exc
    s.db.execute("DELETE FROM media WHERE uid = ?", (row["uid"],))
