"""More of the web UI: sharing, files, apps, status and the link preview helper (spec 13)."""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import ui
from .auth.client import FetchError, assert_fetchable, guarded_client
from .http import state_of
from .media import MediaError, process_upload
from .media_service import delete_media, prepare_media, record_media
from .publish import MicropubError
from .routes_ui import _form_page, _title
from .stores import Put, StoreError
from .target_meta import MAX_PAGE_CHARS, extract_page
from .ui import page, signed_in

_URL = re.compile(r"https?://[^\s<>\"]+")


# --- share (bookmarklet and the phone's share sheet) ----------------------------------------
@signed_in
async def share(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    cfg = s.post_types.get("bookmark")
    if cfg is None:
        return Response("This site has no bookmark post type", status_code=404)
    q = request.query_params
    url, title, text = (
        q.get("url", "").strip(),
        q.get("title", "").strip(),
        q.get("text", "").strip(),
    )
    if not url and (found := _URL.search(text)):  # Android often puts the address in the text
        url = found.group(0).rstrip(".,)")
        text = text.replace(found.group(0), "").strip()
    quoted = "\n".join(f"> {line}" for line in text.splitlines()) if text else ""
    prefill = {"bookmark-of": url, "name": title, "content": quoted}
    return await _form_page(request, "bookmark", cfg, prefill=prefill)


# --- files ------------------------------------------------------------------------------------------
@signed_in
async def files_list(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    rows = s.db.query(
        "SELECT m.*, p.properties AS post_props, p.deleted_properties AS post_deleted"
        " FROM media m LEFT JOIN posts p ON p.uid = m.post_uid"
        " ORDER BY m.published DESC, m.uid DESC LIMIT 120"
    )
    items = []
    for r in rows:
        title = None
        if r["post_uid"] and (r["post_props"] or r["post_deleted"]):
            title = _title(json.loads(r["post_deleted"] or r["post_props"]), "a post")
        items.append(
            {"uid": r["uid"], "url": r["url"], "kind": r["media_type"], "published": r["published"],
             "post_uid": r["post_uid"], "post_title": title}
        )  # fmt: skip
    return page(request, "ui/files.html", items=items, notice=request.query_params.get("notice"))


@signed_in
async def upload_form(request: Request, session: dict[str, Any]) -> Response:
    return page(request, "ui/upload.html")


@signed_in
async def upload_submit(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    uploads = [f for f in form.getlist("file") if isinstance(f, UploadFile) and f.filename]
    if not uploads:
        return page(request, "ui/upload.html", 422, error="Choose a file first.")
    urls = []
    try:
        for upload in uploads:
            processed = process_upload(await upload.read(), upload.filename, s.config.media)
            prepared = await prepare_media(s, processed)
            if not prepared.existing:
                message = s.config.publication.commit_message.format(
                    action="upload", post_type=processed.media_type, file_type="file"
                )
                await s.store.commit([Put(prepared.path, processed.content)], message)
            record_media(s, prepared)
            urls.append(prepared.url)
    except MediaError as exc:
        return page(request, "ui/upload.html", 422, error=f"{upload.filename}: {exc}")
    except StoreError as exc:
        return page(request, "ui/upload.html", 502, error=str(exc))
    notice = "Uploaded: " + ", ".join(urls)
    return RedirectResponse(f"/files?{urlencode({'notice': notice})}", status_code=303)


async def _media_row(request: Request):
    rows = state_of(request).db.query(
        "SELECT m.*, p.properties AS post_props FROM media m LEFT JOIN posts p ON p.uid = m.post_uid WHERE m.uid = ?",
        (request.path_params["uid"],),
    )
    return rows[0] if rows else None


@signed_in
async def file_delete_form(request: Request, session: dict[str, Any]) -> Response:
    row = await _media_row(request)
    if row is None:
        return Response("No such file", status_code=404)
    note = None
    if row["post_uid"] and row["post_props"]:
        title = _title(json.loads(row["post_props"]), "a post")
        note = f"This file is used by “{title}”. That post will be left pointing at a missing file."
    return page(
        request,
        "ui/confirm.html",
        heading="Delete this file?",
        question="It is removed from the site's repository.",
        title=posixpath.basename(row["path"]),
        subject=row["url"],
        note=note,
        button="Delete",
        danger=True,
        action=f"/files/{row['uid']}/delete",
        cancel_url="/files",
    )


@signed_in
async def file_delete_submit(request: Request, session: dict[str, Any]) -> Response:
    row = await _media_row(request)
    if row is None:
        return Response("No such file", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    try:
        await delete_media(state_of(request), row)
    except MicropubError as exc:
        return RedirectResponse(f"/files?{urlencode({'notice': exc.description})}", status_code=303)
    return RedirectResponse(f"/files?{urlencode({'notice': 'File deleted.'})}", status_code=303)


# --- apps with access -------------------------------------------------------------------------------
@signed_in
async def tokens_list(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    now = s.clock().isoformat(timespec="seconds")
    tokens = []
    for t in s.auth.list_tokens():
        state = "revoked" if t["revoked_at"] else "expired" if t["expires_at"] <= now else "active"
        tokens.append({**t, "id": t["token_hash"], "state": state})
    return page(request, "ui/tokens.html", tokens=tokens, notice=request.query_params.get("notice"))


@signed_in
async def token_revoke(request: Request, session: dict[str, Any]) -> Response:
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    done = state_of(request).auth.revoke_id(request.path_params["id"])
    notice = "Access revoked." if done else "That app no longer has access."
    return RedirectResponse(f"/tokens?{urlencode({'notice': notice})}", status_code=303)


# --- status ---------------------------------------------------------------------------------------------
@signed_in
async def status_page(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    problem = await s.store.check()

    def count(sql: str) -> int:
        return s.db.query(sql)[0][0]

    counts = [
        (
            "Published posts",
            count("SELECT COUNT(*) FROM posts WHERE deleted IS NULL AND post_status='published'"),
        ),
        (
            "Drafts",
            count("SELECT COUNT(*) FROM posts WHERE deleted IS NULL AND post_status='draft'"),
        ),
        ("Deleted posts", count("SELECT COUNT(*) FROM posts WHERE deleted IS NOT NULL")),
        (
            "Waiting to be shared",
            count(
                "SELECT COUNT(*) FROM posts WHERE deleted IS NULL AND"
                " json_array_length(json_extract(properties, '$.\"mp-syndicate-to\"')) > 0"
            ),
        ),
        ("Imported from the site", count("SELECT COUNT(*) FROM posts WHERE source='import'")),
        ("Files", count("SELECT COUNT(*) FROM media")),
        ("Apps with access", count("SELECT COUNT(*) FROM tokens WHERE revoked_at IS NULL")),
    ]
    return page(
        request,
        "ui/status.html",
        version=s.settings.app_version,
        store_name=s.store.name,
        store_uid=s.store.uid,
        store_problem=problem,
        site_config=s.site_config_status.replace("fallback", "not found, using the local copy"),
        targets=[t.info for t in s.targets],
        counts=counts,
    )


# --- link preview for the bookmark form ------------------------------------------------------------------
async def _fetch_preview(s, url: str) -> dict[str, str]:
    headers = {"Accept": "text/html", "User-Agent": "micropub-server link preview"}
    async with guarded_client(s.resolver, timeout=6, follow_redirects=False) as client:
        for _ in range(4):  # every hop is checked, so a redirect cannot lead inside
            assert_fetchable(url, resolver=s.resolver)
            response = await client.get(url, headers=headers)
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            break
        else:
            return {}
    if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
        return {}
    meta = extract_page(response.text[:MAX_PAGE_CHARS], url)
    result = {"name": meta.title, "site": meta.site, "summary": meta.description}
    return {k: v for k, v in result.items() if v}


async def preview(request: Request) -> Response:
    if ui.current_session(request) is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    s = state_of(request)
    url = request.query_params.get("url", "").strip()
    key = f"preview:{url}"
    now = s.clock().isoformat(timespec="seconds")
    cached = s.db.query("SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, now))
    if cached:
        return JSONResponse(json.loads(cached[0][0]))
    try:
        result = await _fetch_preview(s, url)
    except (FetchError, httpx.HTTPError, ValueError):
        return JSONResponse({})
    from datetime import timedelta

    s.db.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, json.dumps(result), (s.clock() + timedelta(days=1)).isoformat(timespec="seconds")),
    )
    return JSONResponse(result)


def routes() -> list[Route]:
    return [
        Route("/share", share),
        Route("/files", files_list),
        Route("/files/upload", upload_form, methods=["GET"]),
        Route("/files/upload", upload_submit, methods=["POST"]),
        Route("/files/{uid}/delete", file_delete_form, methods=["GET"]),
        Route("/files/{uid}/delete", file_delete_submit, methods=["POST"]),
        Route("/tokens", tokens_list),
        Route("/tokens/{id}/revoke", token_revoke, methods=["POST"]),
        Route("/status", status_page),
        Route("/api/preview", preview),
    ]
