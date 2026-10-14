"""The /micropub endpoint (spec 03)."""

from __future__ import annotations

from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import queries
from .http import oauth_error, state_of
from .jf2 import form_to_jf2, mf2_to_jf2
from .media import MediaError, Processed, process_upload
from .publish import MEDIA_PROPERTIES, MicropubError, create_post
from .resource import authenticate
from .scopes import check_scope

ACTIONS = {"create", "update", "delete", "undelete"}


def _error(exc: MicropubError) -> JSONResponse:
    return oauth_error(exc.error, exc.description, exc.status, **exc.extra)


async def _form_dict(form, media_config) -> tuple[dict[str, Any], dict[str, list[Processed]]]:
    """Split a form into text fields and processed uploads (photo, video, audio)."""
    fields: dict[str, Any] = {}
    uploads: dict[str, list[Processed]] = {}
    for key in form.keys():
        values = form.getlist(key)
        files = [v for v in values if isinstance(v, UploadFile)]
        if not files:
            fields[key] = values if len(values) > 1 or key.endswith("[]") else values[0]
            continue
        prop = key.removesuffix("[]")
        if prop not in MEDIA_PROPERTIES:
            raise MicropubError("invalid_request", f"Files are not accepted in the {prop} field")
        for upload in files:
            try:
                processed = process_upload(
                    await upload.read(), upload.filename or "file", media_config
                )
            except MediaError as exc:
                raise MicropubError("invalid_request", str(exc), exc.status) from exc
            if processed.media_type != prop:
                raise MicropubError(
                    "invalid_request", f"A {processed.media_type} file was sent in the {prop} field"
                )
            uploads.setdefault(prop, []).append(processed)
    return fields, uploads


async def micropub_post(request: Request) -> Response:
    s = state_of(request)
    try:
        ctype = request.headers.get("content-type", "")
        if "json" in ctype:
            try:
                body = await request.json()
            except ValueError as exc:
                raise MicropubError(
                    "invalid_request", "The request body is not valid JSON"
                ) from exc
            if not isinstance(body, dict):
                raise MicropubError("invalid_request", "The request body must be a JSON object")
            form_token, uploads = None, {}
        else:
            body, uploads = await _form_dict(await request.form(), s.config.media)
            form_token = body.get("access_token")

        info = authenticate(request, form_token)

        action = request.query_params.get("action") or body.get("action") or "create"
        if action not in ACTIONS:
            raise MicropubError("invalid_request", f"Unknown action: {action}")
        allowed = check_scope(info.scope, action)
        if not allowed:
            needed = "create" if action == "undelete" else action
            raise MicropubError(
                "insufficient_scope", f"The {needed} scope is required", 403, scope=needed
            )
        url = request.query_params.get("url") or body.get("url")
        if action != "create" and not url:
            raise MicropubError("invalid_request", "The url parameter is required")
        if action != "create":
            raise MicropubError(
                "not_implemented", f"The {action} action is not implemented yet", 501
            )

        jf2 = mf2_to_jf2(body) if "json" in ctype else form_to_jf2(body)
        created = await create_post(s, jf2, draft=allowed == "draft", uploads=uploads)
    except MicropubError as exc:
        return _error(exc)

    return JSONResponse(
        {
            "success": "create_pending",
            "success_description": f"Post will be created at {created.url}",
        },
        status_code=202,
        headers={"Location": created.url},
    )


async def micropub_get(request: Request) -> Response:
    s = state_of(request)
    params: dict[str, Any] = {}
    for key in request.query_params.keys():
        values = request.query_params.getlist(key)
        params[key] = values if key.endswith("[]") else values[-1]
    try:
        authenticate(request, params.pop("access_token", None))
        q = params.get("q")
        if not q:
            raise MicropubError("invalid_request", "The q parameter is required")
        q = {"categories": "category", "channels": "channel"}.get(q, q)

        if q == "config":
            return JSONResponse(await queries.build_config(s))
        if q == "source":
            if params.get("url") or params.get("uid"):
                return JSONResponse(queries.source_one(s, params))
            return JSONResponse(queries.source_list(s, params))
        if q == "category":
            return JSONResponse(
                {"categories": queries.slice_list(await queries.get_categories(s), params)}
            )
        if q == "channel":
            return JSONResponse({"channels": queries.slice_list(queries.channels_list(s), params)})
        if q == "post-types":
            return JSONResponse(
                {"post-types": queries.slice_list(queries.post_types_list(s), params)}
            )
        if q == "syndicate-to":
            return JSONResponse(
                {"syndicate-to": queries.slice_list(queries.syndicate_to_list(s), params)}
            )
        if q == "media-endpoint":
            return JSONResponse({"media-endpoint": f"{s.base}media"})
        raise MicropubError("invalid_request", f"Unsupported query: q={q}")
    except MicropubError as exc:
        return _error(exc)


def routes() -> list[Route]:
    return [
        Route("/micropub", micropub_post, methods=["POST"]),
        Route("/micropub", micropub_get, methods=["GET"]),
    ]
