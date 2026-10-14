"""The /media endpoint (spec 04)."""

from __future__ import annotations

import base64
from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .http import oauth_error, state_of
from .media import MediaError, process_upload
from .media_service import media_properties, prepare_media, record_media
from .publish import MicropubError
from .resource import authenticate
from .stores import Delete, Put, StoreError

DEFAULT_LIMIT = 40
MAX_LIMIT = 100


def _error(exc: MicropubError) -> JSONResponse:
    return oauth_error(exc.error, exc.description, exc.status, **exc.extra)


def _need(info, *scopes: str) -> None:
    if not set(scopes) & info.scopes:
        raise MicropubError(
            "insufficient_scope", f"The {scopes[0]} scope is required", 403, scope=scopes[0]
        )


async def media_post(request: Request) -> Response:
    s = state_of(request)
    try:
        form = await request.form()
        info = authenticate(request, form.get("access_token"))
        action = request.query_params.get("action") or form.get("action") or "media"
        if action == "delete":
            return await _delete(s, info, request, form)
        if action != "media":
            raise MicropubError("invalid_request", f"Unknown action: {action}")
        _need(info, "media", "create")

        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise MicropubError("invalid_request", "A file is required in the file field")
        try:
            processed = process_upload(
                await upload.read(), upload.filename or "file", s.config.media
            )
        except MediaError as exc:
            raise MicropubError("invalid_request", str(exc), exc.status) from exc

        prepared = await prepare_media(s, processed)
        if not prepared.existing:
            message = s.config.publication.commit_message.format(
                action="upload", post_type=processed.media_type, file_type="file"
            )
            try:
                await s.store.commit([Put(prepared.path, processed.content)], message)
            except StoreError as exc:
                raise MicropubError("store_error", str(exc), 502) from exc
        record_media(s, prepared)
    except MicropubError as exc:
        return _error(exc)
    return JSONResponse(
        {"success": "create", "success_description": f"Media uploaded to {prepared.url}"},
        status_code=201,
        headers={"Location": prepared.url},
    )


async def _delete(s, info, request: Request, form) -> Response:
    _need(info, "delete")
    url = request.query_params.get("url") or form.get("url")
    if not url:
        raise MicropubError("invalid_request", "The url parameter is required")
    rows = s.db.query("SELECT uid, path FROM media WHERE url = ?", (str(url),))
    if not rows:
        raise MicropubError("invalid_request", f"No media found at {url}")
    message = s.config.publication.commit_message.format(
        action="delete", post_type="media", file_type="file"
    )
    try:
        await s.store.commit([Delete(rows[0]["path"])], message)
    except StoreError as exc:
        raise MicropubError("store_error", str(exc), 502) from exc
    s.db.execute("DELETE FROM media WHERE uid = ?", (rows[0]["uid"],))
    return JSONResponse({"success": "delete", "success_description": f"File deleted from {url}"})


def _cursor(published: str, uid: str) -> str:
    return base64.urlsafe_b64encode(f"{published}|{uid}".encode()).decode()


def _decode(value: str) -> tuple[str, str]:
    try:
        published, uid = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
    except Exception as exc:
        raise MicropubError("invalid_request", "Invalid paging cursor") from exc
    return published, uid


async def media_get(request: Request) -> Response:
    s = state_of(request)
    p: dict[str, Any] = dict(request.query_params)
    try:
        authenticate(request, p.pop("access_token", None))
        q = p.get("q")
        if not q:
            raise MicropubError("invalid_request", "The q parameter is required")
        if q == "last":
            rows = s.db.query("SELECT * FROM media ORDER BY published DESC, uid DESC LIMIT 1")
            if not rows:
                raise MicropubError("not_found", "No media has been uploaded", 404)
            return JSONResponse(media_properties(rows[0]))
        if q != "source":
            raise MicropubError("invalid_request", f"Unsupported query: q={q}")
        if p.get("url") or p.get("uid"):
            col, val = ("url", p["url"]) if p.get("url") else ("uid", p["uid"])
            rows = s.db.query(f"SELECT * FROM media WHERE {col} = ?", (val,))
            if not rows:
                if p.get("url"):
                    raise MicropubError("invalid_request", f"No media found at {val}")
                raise MicropubError("not_found", "No media found with that uid", 404)
            return JSONResponse(media_properties(rows[0]))

        limit = min(int(p.get("limit") or DEFAULT_LIMIT), MAX_LIMIT)
        where, args = ["1=1"], []
        if p.get("filter"):
            where.append("instr(lower(url), ?) > 0")
            args.append(p["filter"].lower())
        backwards = bool(p.get("before"))
        cursor = p.get("before") or p.get("after")
        if cursor:
            published, uid = _decode(cursor)
            where.append(f"(published, uid) {'>' if backwards else '<'} (?, ?)")
            args += [published, uid]
        order = "ASC" if backwards else "DESC"
        rows = s.db.query(
            f"SELECT * FROM media WHERE {' AND '.join(where)}"
            f" ORDER BY published {order}, uid {order} LIMIT ?",
            (*args, limit + 1),
        )
        more = len(rows) > limit
        rows = rows[:limit]
        if backwards:
            rows.reverse()
        paging: dict[str, str] = {}
        if rows:
            if more if not backwards else bool(cursor):
                paging["after"] = _cursor(rows[-1]["published"], rows[-1]["uid"])
            if bool(cursor) if not backwards else more:
                paging["before"] = _cursor(rows[0]["published"], rows[0]["uid"])
        return JSONResponse({"items": [media_properties(r) for r in rows], "paging": paging})
    except MicropubError as exc:
        return _error(exc)


def routes() -> list[Route]:
    return [
        Route("/media", media_post, methods=["POST"]),
        Route("/media", media_get, methods=["GET"]),
    ]
