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
from .publish import (
    MEDIA_PROPERTIES,
    MicropubError,
    create_post,
    delete_post,
    undelete_post,
    update_post,
)
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
        if action == "update":
            if "json" not in ctype:
                raise MicropubError("invalid_request", "Updates must be sent as JSON")
            if not any(k in body for k in ("add", "replace", "delete")):
                raise MicropubError("invalid_request", "An update needs add, replace or delete")
            done = await update_post(
                s,
                str(url),
                add=body.get("add"),
                replace=body.get("replace"),
                delete=body.get("delete"),
                draft_mode=allowed == "draft",
            )
            if not done.changed:
                return JSONResponse(
                    {
                        "success": "update",
                        "success_description": f"Post at {done.url} not updated as no properties changed",
                    },
                    headers={"Location": done.url},
                )
            if done.moved:
                return JSONResponse(
                    {
                        "success": "update",
                        "success_description": f"Post updated and moved to {done.url}",
                    },
                    status_code=201,
                    headers={"Location": done.url},
                )
            return JSONResponse(
                {"success": "update", "success_description": f"Post updated at {done.url}"},
                headers={"Location": done.url},
            )
        if action == "delete":
            deleted_url = await delete_post(s, str(url))
            return JSONResponse(
                {"success": "delete", "success_description": f"Post deleted from {deleted_url}"}
            )
        if action == "undelete":
            restored = await undelete_post(s, str(url), draft_mode=allowed == "draft")
            return JSONResponse(
                {
                    "success": "delete_undelete",
                    "success_description": f"Post restored to {restored}",
                },
                headers={"Location": restored},
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
        Route("/syndicate", syndicate_post, methods=["POST"]),
    ]


async def syndicate_post(request: Request) -> Response:
    """Share posts that are waiting for their targets. Ping this after each site deploy."""
    from .syndication.service import run_syndication

    s = state_of(request)
    try:
        info = authenticate(request)
        if "update" not in info.scopes:
            raise MicropubError(
                "insufficient_scope", "The update scope is required", 403, scope="update"
            )
        source_url = request.query_params.get("source_url")
        if not source_url and "json" in request.headers.get("content-type", ""):
            try:
                body = await request.json()
                source_url = (body.get("syndication") or {}).get("source_url") or body.get(
                    "source_url"
                )
            except (ValueError, AttributeError):
                source_url = None
        result = await run_syndication(s, source_url)
    except MicropubError as exc:
        return _error(exc)
    body = {"success": "OK", "success_description": result.message}
    if result.failed:
        body["failed"] = result.failed
    return JSONResponse(body)
