"""Request and response helpers shared by route modules."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

if TYPE_CHECKING:
    from .app import AppState


def state_of(request: Request) -> AppState:
    return request.app.state.s


def render(request: Request, template: str, status: int = 200, **context: Any) -> HTMLResponse:
    s = state_of(request)
    html = s.jinja.get_template(template).render(me=s.me, base=s.base, **context)
    return HTMLResponse(html, status_code=status)


def error_page(request: Request, message: str, status: int = 400) -> HTMLResponse:
    return render(request, "error.html", status, message=message)


def oauth_error(error: str, description: str = "", status: int = 400, **extra: Any) -> JSONResponse:
    body = {"error": error, **({"error_description": description} if description else {}), **extra}
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


async def read_params(request: Request) -> dict[str, Any]:
    """Merge query parameters with a form or JSON body (body wins)."""
    params: dict[str, Any] = dict(request.query_params)
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        if isinstance(body, dict):
            params.update(body)
    elif "form" in ctype:
        form = await request.form()
        for key in form:
            values = form.getlist(key)
            params[key] = values if len(values) > 1 else values[0]
    return params


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def wants_form(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/x-www-form-urlencoded" in accept and "json" not in accept
