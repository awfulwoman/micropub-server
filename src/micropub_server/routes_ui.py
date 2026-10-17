"""The web UI: sign in, and writing and managing posts (spec 13)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import nh3
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import ui
from .auth.passwords import verify_password
from .deploys import build_label
from .forms import LABELS, LINKS, PHOTO_SLOTS, build_post, type_fields, type_required
from .http import state_of
from .jf2 import jf2_to_mf2
from .publish import (
    MicropubError,
    create_post,
    delete_post,
    render_file,
    undelete_post,
    update_post,
)
from .queries import _cursor, _decode_cursor, get_categories
from .ui import page, safe_next, signed_in

PAGE_SIZE = 40
STATUSES = [
    ("", "Published and drafts"),
    ("published", "Published"),
    ("draft", "Drafts"),
    ("waiting", "Waiting to be shared"),
    ("deleted", "Deleted"),
]


# --- sign in -----------------------------------------------------------------------
async def home(request: Request) -> Response:
    if ui.current_session(request):
        return RedirectResponse("/posts", status_code=303)
    return page(request, "ui/home.html")


async def login_get(request: Request) -> Response:
    if ui.current_session(request):
        return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
    return page(request, "ui/login.html", next=safe_next(request.query_params.get("next"), ""))


async def login_post(request: Request) -> Response:
    s = state_of(request)
    form = await request.form()
    nxt = request.query_params.get("next")
    ip = request.client.host if request.client else "unknown"
    if not s.auth.login_allowed(ip):
        return page(
            request, "ui/login.html", 429, error="Too many attempts. Wait a minute.", next=nxt
        )
    if not verify_password(
        str(form.get("password", "")),
        password_hash=s.settings.password_hash,
        password_secret=s.settings.password_secret,
    ):
        s.auth.record_login_failure(ip)
        return page(request, "ui/login.html", 422, error="Wrong password.", next=nxt)
    response = RedirectResponse(safe_next(nxt), status_code=303)
    ui.start_session(request, response)
    return response


@signed_in
async def logout(request: Request, session: dict[str, Any]) -> Response:
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    ui.end_all_sessions(request)
    response = RedirectResponse("/login", status_code=303)
    ui.end_session(response)
    return response


# --- the posts list -------------------------------------------------------------------
def _title(props: dict[str, Any], fallback: str) -> str:
    if props.get("name"):
        return str(props["name"])
    content = props.get("content")
    text = content.get("text") if isinstance(content, dict) else content
    text = " ".join(str(text or "").split())
    if text:
        return text if len(text) <= 80 else text[:79].rstrip() + "…"
    for key in ("bookmark-of", "like-of", "repost-of", "in-reply-to", "quotation-of"):
        if props.get(key):
            return str(props[key])
    return fallback


def list_posts(s, filters: dict[str, str], after: str | None, before: str | None):
    where: list[str] = []
    args: list[Any] = []
    status = filters.get("status", "")
    if status == "deleted":
        where.append("deleted IS NOT NULL")
    else:
        where.append("deleted IS NULL")
        if status in {"published", "draft"}:
            where.append("post_status = ?")
            args.append(status)
        elif status == "waiting":
            where.append("json_array_length(json_extract(properties, '$.\"mp-syndicate-to\"')) > 0")
    if filters.get("type"):
        where.append("post_type = ?")
        args.append(filters["type"])
    if filters.get("tag"):
        where.append("EXISTS (SELECT 1 FROM json_each(properties, '$.category') WHERE value = ?)")
        args.append(filters["tag"])
    if filters.get("q"):
        needle = (
            "%"
            + filters["q"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        where.append(
            "(lower(json_extract(coalesce(deleted_properties, properties), '$.name')) LIKE ? ESCAPE '\\'"
            " OR lower(json_extract(coalesce(deleted_properties, properties), '$.content.text')) LIKE ? ESCAPE '\\')"
        )
        args += [needle, needle]
    backwards = bool(before)
    cursor = before or after
    if cursor:
        published, uid = _decode_cursor(cursor)
        where.append(f"(published, uid) {'>' if backwards else '<'} (?, ?)")
        args += [published, uid]
    order = "ASC" if backwards else "DESC"
    rows = s.db.query(
        "SELECT uid, url, post_type, post_status, published, deleted, source,"
        " coalesce(deleted_properties, properties) AS props,"
        " json_array_length(json_extract(properties, '$.\"mp-syndicate-to\"')) AS waiting"
        f" FROM posts WHERE {' AND '.join(where)} ORDER BY published {order}, uid {order} LIMIT ?",
        (*args, PAGE_SIZE + 1),
    )
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    if backwards:
        rows.reverse()
    items = []
    for r in rows:
        props = json.loads(r["props"])
        cfg = s.post_types.get(r["post_type"])
        items.append(
            {
                "uid": r["uid"],
                "url": r["url"],
                "title": _title(props, props.get("slug") or r["url"]),
                "type_label": cfg.name if cfg else r["post_type"],
                "published": r["published"],
                "status": r["post_status"],
                "deleted": bool(r["deleted"]),
                "waiting": bool(r["waiting"]),
                "imported": r["source"] == "import",
                "build": build_label(s, r["uid"]),
            }
        )
    has_after = more if not backwards else bool(cursor)
    has_before = bool(cursor) if not backwards else more
    newer = _cursor(rows[0]["published"], rows[0]["uid"]) if rows and has_before else None
    older = _cursor(rows[-1]["published"], rows[-1]["uid"]) if rows and has_after else None
    return items, newer, older


@signed_in
async def posts_list(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    q = request.query_params
    filters = {k: q.get(k, "").strip() for k in ("q", "type", "status", "tag")}
    try:
        items, newer, older = list_posts(s, filters, q.get("after"), q.get("before"))
    except Exception:
        return Response("Bad paging cursor", status_code=400)
    keep = {k: v for k, v in filters.items() if v}

    def link(**extra: str) -> str:
        return "/posts?" + urlencode({**keep, **extra})

    return page(
        request,
        "ui/posts.html",
        rows=items,
        filters=filters,
        types=[(k, c.name) for k, c in s.post_types.items()],
        statuses=STATUSES,
        prev_url=link(before=newer) if newer else None,
        next_url=link(after=older) if older else None,
        notice=q.get("notice"),
    )


# --- creating posts -------------------------------------------------------------------------------
def _bookmarklet(s) -> str:
    return (
        f"javascript:location.href='{s.base}share?url='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title)+'&text='+encodeURIComponent(getSelection())"
    )


@signed_in
async def new_post(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    types = [(k, c.name) for k, c in s.post_types.items()]
    return page(request, "ui/new.html", types=types, bookmarklet=_bookmarklet(s))


NEW_BUTTONS = [("publish", "Publish", True), ("draft", "Save as draft", False)]


async def _form_page(
    request,
    key,
    cfg,
    *,
    status=200,
    error=None,
    errors=None,
    values=None,
    prefill=None,
    edit_row=None,
):
    s = state_of(request)
    editing = edit_row is not None
    if editing:
        draft = edit_row["post_status"] == "draft"
        buttons = [("save", "Save changes", not draft)]
        buttons += (
            [("publish", "Publish", True)] if draft else [("draft", "Revert to draft", False)]
        )
        heading = f"Edit {cfg.name.lower()}"
        action = f"/posts/{edit_row['uid']}/edit"
    else:
        buttons, heading, action = NEW_BUTTONS, f"New {cfg.name.lower()}", f"/posts/new/{key}"
    return page(
        request,
        "ui/form.html",
        status,
        type_key=key,
        type_name=cfg.name,
        heading=heading,
        form_action=action,
        buttons=buttons,
        button_name="action" if editing else "status",
        editing=editing,
        existing_photos=_photos_of(edit_row) if editing else [],
        fields=type_fields(key, cfg),
        required=set(type_required(key, cfg)),
        links=set(LINKS),
        labels=LABELS,
        slots=PHOTO_SLOTS,
        targets=[t.info for t in s.targets],
        tags=await get_categories(s),
        error=error,
        errors=errors or {},
        values=values or {},
        prefill=prefill or {},
        example_path=edit_row["path"] if editing else cfg.post.path,
        preview_path=f"/posts/new/{key}/preview",
    )


def _photos_of(row) -> list[dict[str, str]]:
    props = json.loads(row["properties"])
    return [
        {
            "src": urljoin(row["url"], p["url"]),
            "alt": p.get("alt", ""),
            "name": p["url"].rsplit("/", 1)[-1],
        }
        for p in props.get("photo", []) or []
        if isinstance(p, dict) and p.get("url")
    ]


@signed_in
async def new_form(request: Request, session: dict[str, Any]) -> Response:
    key = request.path_params["type"]
    cfg = state_of(request).post_types.get(key)
    if cfg is None:
        return Response("No such post type", status_code=404)
    return await _form_page(request, key, cfg)


@signed_in
async def new_submit(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    key = request.path_params["type"]
    cfg = s.post_types.get(key)
    if cfg is None:
        return Response("No such post type", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    built = await build_post(key, cfg, form, s.config.media, cfg.channel)
    if built.errors:
        return await _form_page(
            request,
            key,
            cfg,
            status=422,
            error="Some fields need attention.",
            errors=built.errors,
            values=built.values,
        )
    try:
        created = await create_post(
            s,
            built.jf2,
            draft=form.get("status") == "draft",
            uploads=built.uploads,
            expect_type=key,
        )
    except MicropubError as exc:
        return await _form_page(
            request,
            key,
            cfg,
            status=502 if exc.error == "store_error" else 422,
            error=exc.description,
            values=built.values,
        )
    return RedirectResponse(f"/posts/{created.uid}?created=1", status_code=303)


# --- one post ---------------------------------------------------------------------------------------------
def _clean_preview(html: str, base: str) -> str:
    """Sanitised post HTML, with relative image links made absolute against the post's own URL."""

    def absolute(tag: str, attr: str, value: str) -> str:
        return urljoin(base, value) if (tag, attr) == ("img", "src") else value

    return nh3.clean(html, attribute_filter=absolute)


@signed_in
async def post_page(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    rows = s.db.query("SELECT * FROM posts WHERE uid = ?", (request.path_params["uid"],))
    if not rows:
        return Response("No such post", status_code=404)
    row = rows[0]
    deleted = bool(row["deleted"])
    props = json.loads(
        row["deleted_properties"] if deleted and row["deleted_properties"] else row["properties"]
    )
    cfg = s.post_types.get(row["post_type"])
    content = props.get("content")
    html = content.get("html") if isinstance(content, dict) else None
    photos = [
        {"src": urljoin(row["url"], p["url"]), "alt": p.get("alt", "")}
        for p in props.get("photo", []) or []
        if isinstance(p, dict) and p.get("url")
    ]
    details = [
        ("Status", "deleted" if deleted else row["post_status"]),
        ("Published", row["published"]),
        ("Updated", row["updated"] or "never"),
        ("Source", "imported from the site" if row["source"] == "import" else "created here"),
        ("Tags", ", ".join(map(str, props.get("category") or [])) or "none"),
        ("Waiting to share to", ", ".join(props.get("mp-syndicate-to") or []) or "nothing"),
        ("Id", row["uid"]),
    ]
    copies = [u for u in (props.get("syndication") or []) if isinstance(u, str)]
    notice = (
        f"Committed. It will be live at {row['url']} once the site has built."
        if request.query_params.get("created")
        else request.query_params.get("notice")
    )
    return page(
        request,
        "ui/post.html",
        row=row,
        title=_title(props, props.get("slug") or row["url"]),
        type_label=cfg.name if cfg else row["post_type"],
        deleted=deleted,
        preview_html=_clean_preview(html, row["url"]) if html else "",
        photos=photos,
        details=details,
        copies=copies,
        notice=notice,
        waiting=bool(props.get("mp-syndicate-to")) and not deleted and bool(s.targets),
        build=build_label(s, row["uid"]),
        file_text=render_file(s, props) if not deleted else "",
    )


@signed_in
async def post_build(request: Request, session: dict[str, Any]) -> Response:
    """The post's build state as JSON, so the page can update the badge while a build runs."""
    label = build_label(state_of(request), request.path_params["uid"])
    return JSONResponse(label or {}, headers={"Cache-Control": "no-store"})


# --- editing ------------------------------------------------------------------------------------------------
def _local_time(s, iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    tz = s.config.application.time_zone
    return (dt.astimezone(ZoneInfo(tz)) if tz != "client" else dt).strftime("%Y-%m-%dT%H:%M")


def _prefill(s, props: dict[str, Any]) -> dict[str, Any]:
    content = props.get("content")
    out: dict[str, Any] = {
        "name": props.get("name", ""),
        "summary": props.get("summary", ""),
        "content": (content.get("text") if isinstance(content, dict) else content) or "",
        "category": ", ".join(map(str, props.get("category") or [])),
        "published": _local_time(s, props["published"]),
        "mp-slug": props.get("slug", ""),
        "syndicate": props.get("mp-syndicate-to") or [],
    }
    for link in LINKS:
        out[link] = props.get(link, "")
    geo = props.get("location")
    if isinstance(geo, dict):
        out["location_lat"], out["location_lng"] = geo.get("latitude", ""), geo.get("longitude", "")
    card = props.get("checkin")
    if isinstance(card, dict):
        out["checkin_name"] = card.get("name", "")
        out["checkin_lat"], out["checkin_lng"] = card.get("latitude", ""), card.get("longitude", "")
    return out


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _diff(
    s, fields: list[str], props: dict[str, Any], new: dict[str, Any], prefill: dict[str, Any]
):
    """The Micropub update operations that turn the stored post into what the form now says."""
    replace: dict[str, list[Any]] = {}
    delete: list[str] = []

    def set_(key: str, value: Any, changed: bool, present: bool) -> None:
        if changed and present:
            replace[key] = [value]
        elif changed:
            delete.append(key)

    for prop in fields:
        if prop in LINKS or prop in {"name", "summary"}:
            old, value = props.get(prop), new.get(prop)
            set_(prop, value, (old or None) != (value or None), bool(value))
        elif prop == "content":
            old = props.get("content")
            old = (old.get("text") if isinstance(old, dict) else old) or ""
            value = new.get("content", "")
            set_("content", value, old.strip() != value.strip(), bool(value))
        elif prop == "category":
            old, value = list(props.get("category") or []), list(new.get("category") or [])
            if old != value:
                if value:
                    replace["category"] = value
                else:
                    delete.append("category")
        elif prop == "location":
            old, value = props.get("location"), new.get("location")
            was = (
                (_number(old.get("latitude")), _number(old.get("longitude")))
                if isinstance(old, dict)
                else None
            )
            now = tuple(float(x) for x in value.removeprefix("geo:").split(",")) if value else None
            set_("location", value, was != now, bool(value))
        elif prop == "checkin":
            old, card = props.get("checkin") or {}, new.get("checkin")
            was = (old.get("name"), _number(old.get("latitude")), _number(old.get("longitude")))
            now = (
                (card.get("name"), _number(card.get("latitude")), _number(card.get("longitude")))
                if card
                else None
            )
            set_(
                "checkin",
                jf2_to_mf2(card) if card else None,
                (was if old else None) != now,
                bool(card),
            )

    if new.get("published") and new["published"] != prefill["published"]:
        replace["published"] = [new["published"]]
    if new.get("mp-slug") and new["mp-slug"] != prefill["mp-slug"]:
        replace["mp-slug"] = [new["mp-slug"]]
    return replace, delete


async def _load(request) -> Any:
    s = state_of(request)
    rows = s.db.query("SELECT * FROM posts WHERE uid = ?", (request.path_params["uid"],))
    return rows[0] if rows else None


def _back(uid: str, notice: str) -> RedirectResponse:
    return RedirectResponse(f"/posts/{uid}?{urlencode({'notice': notice})}", status_code=303)


@signed_in
async def edit_form(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    row = await _load(request)
    if row is None:
        return Response("No such post", status_code=404)
    if row["deleted"]:
        return _back(row["uid"], "This post is deleted. Restore it before editing.")
    cfg = s.post_types.get(row["post_type"])
    if cfg is None:
        return Response("This post's type is not configured", status_code=409)
    return await _form_page(
        request,
        row["post_type"],
        cfg,
        prefill=_prefill(s, json.loads(row["properties"])),
        edit_row=row,
    )


@signed_in
async def edit_submit(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    row = await _load(request)
    if row is None:
        return Response("No such post", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    cfg = s.post_types.get(row["post_type"])
    if row["deleted"] or cfg is None:
        return _back(row["uid"], "This post cannot be edited.")
    key, props = row["post_type"], json.loads(row["properties"])
    prefill = _prefill(s, props)
    built = await build_post(key, cfg, form, s.config.media, None, editing=True)
    if built.errors:
        return await _form_page(
            request, key, cfg, status=422, error="Some fields need attention.", errors=built.errors,
            values=built.values, prefill=prefill, edit_row=row,
        )  # fmt: skip

    replace, delete = _diff(s, type_fields(key, cfg), props, built.jf2, prefill)
    chosen = [str(v) for v in form.getlist("syndicate") if v]
    if sorted(chosen) != sorted(prefill["syndicate"]) and s.targets:
        if chosen:
            replace["mp-syndicate-to"] = chosen
        else:
            delete.append("mp-syndicate-to")
    action = form.get("action")
    if action == "publish" and row["post_status"] != "published":
        replace["post-status"] = ["published"]
    elif action == "draft" and row["post_status"] != "draft":
        replace["post-status"] = ["draft"]
    if not replace and not delete:
        return _back(row["uid"], "Nothing changed.")
    try:
        done = await update_post(s, row["url"], replace=replace or None, delete=delete or None)
    except MicropubError as exc:
        return await _form_page(
            request, key, cfg, status=502 if exc.error == "store_error" else 422, error=exc.description,
            values=built.values, prefill=prefill, edit_row=row,
        )  # fmt: skip
    if done.moved:
        return _back(row["uid"], f"Saved. The post moved to {done.url}")
    return _back(row["uid"], "Saved." if done.changed else "Nothing changed.")


async def _confirm(
    request, session, *, action: str, heading: str, question: str, button: str, danger: bool
):
    row = await _load(request)
    if row is None:
        return None, Response("No such post", status_code=404)
    props = json.loads(
        row["deleted_properties"]
        if row["deleted"] and row["deleted_properties"]
        else row["properties"]
    )
    return row, page(
        request,
        "ui/confirm.html",
        row=row,
        title=_title(props, props.get("slug") or row["url"]),
        subject=row["url"],
        cancel_url=f"/posts/{row['uid']}",
        heading=heading,
        question=question,
        button=button,
        danger=danger,
        action=f"/posts/{row['uid']}/{action}",
    )


@signed_in
async def delete_form(request: Request, session: dict[str, Any]) -> Response:
    row, response = await _confirm(
        request, session, action="delete", heading="Delete this post?", button="Delete", danger=True,
        question="The file is removed from the site's repository. You can restore it afterwards.",
    )  # fmt: skip
    return response


@signed_in
async def delete_submit(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    row = await _load(request)
    if row is None:
        return Response("No such post", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    try:
        await delete_post(s, row["url"])
    except MicropubError as exc:
        return _back(row["uid"], exc.description)
    return RedirectResponse(
        f"/posts?{urlencode({'notice': 'Deleted. You can restore it from the Deleted filter.'})}",
        status_code=303,
    )


@signed_in
async def undelete_form(request: Request, session: dict[str, Any]) -> Response:
    row, response = await _confirm(
        request, session, action="undelete", heading="Restore this post?", button="Restore", danger=False,
        question="The file comes back at its old address.",
    )  # fmt: skip
    return response


@signed_in
async def undelete_submit(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    row = await _load(request)
    if row is None:
        return Response("No such post", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    try:
        await undelete_post(s, row["url"])
    except MicropubError as exc:
        return _back(row["uid"], exc.description)
    return _back(row["uid"], "Restored.")


@signed_in
async def share_now(request: Request, session: dict[str, Any]) -> Response:
    s = state_of(request)
    row = await _load(request)
    if row is None:
        return Response("No such post", status_code=404)
    form = await request.form()
    if not ui.csrf_ok(session, form.get("csrf")):
        return Response("Invalid form token", status_code=403)
    from .syndication.service import run_syndication

    try:
        result = await run_syndication(s, row["url"])
    except MicropubError as exc:
        return _back(row["uid"], exc.description)
    note = "Shared." if result.syndicated else result.message
    if result.failed:
        note = f"Could not share to: {', '.join(result.failed)}. It will be tried again."
    return _back(row["uid"], note)


def routes() -> list:
    return [
        Route("/", home),
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/posts", posts_list),
        Route("/posts/new", new_post),
        Route("/posts/new/{type}", new_form, methods=["GET"]),
        Route("/posts/new/{type}", new_submit, methods=["POST"]),
        Route("/posts/{uid}", post_page),
        Route("/posts/{uid}/build", post_build),
        Route("/posts/{uid}/edit", edit_form, methods=["GET"]),
        Route("/posts/{uid}/edit", edit_submit, methods=["POST"]),
        Route("/posts/{uid}/delete", delete_form, methods=["GET"]),
        Route("/posts/{uid}/delete", delete_submit, methods=["POST"]),
        Route("/posts/{uid}/undelete", undelete_form, methods=["GET"]),
        Route("/posts/{uid}/undelete", undelete_submit, methods=["POST"]),
        Route("/posts/{uid}/syndicate", share_now, methods=["POST"]),
        Mount("/static", StaticFiles(packages=[("micropub_server", "static")]), name="static"),
    ]
