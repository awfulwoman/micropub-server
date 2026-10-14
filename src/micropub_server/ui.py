"""Browser sessions, CSRF and page rendering for the web UI (spec 13)."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlsplit

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .http import state_of

COOKIE = "ms_session"
MAX_AGE = 30 * 24 * 3600
CSP = "default-src 'self'; img-src 'self' https: data:; frame-ancestors 'none'; base-uri 'self'"

Handler = Callable[[Request], Awaitable[Response]]


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(state_of(request).settings.secret, salt="micropub-ui-session")


_EPOCH_KEY = "session-epoch"


def _epoch(request: Request) -> int:
    rows = state_of(request).db.query("SELECT value FROM cache WHERE key = ?", (_EPOCH_KEY,))
    return int(rows[0][0]) if rows else 0


def end_all_sessions(request: Request) -> None:
    """Make every cookie issued so far worthless. There is one user, so this is all signing out needs."""
    state_of(request).db.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, '9999-12-31T00:00:00')",
        (_EPOCH_KEY, str(_epoch(request) + 1)),
    )


def current_session(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _serializer(request).loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return None
    if (
        not isinstance(data, dict)
        or not data.get("csrf")
        or data.get("epoch", 0) != _epoch(request)
    ):
        return None
    return data


def start_session(request: Request, response: Response) -> None:
    secure = state_of(request).base.startswith("https://")
    response.set_cookie(
        COOKIE,
        _serializer(request).dumps({"csrf": secrets.token_urlsafe(24), "epoch": _epoch(request)}),
        max_age=MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def csrf_ok(session: dict[str, Any], token: Any) -> bool:
    return isinstance(token, str) and hmac.compare_digest(token, session["csrf"])


def safe_next(value: str | None, default: str = "/posts") -> str:
    """Only ever redirect to a path on this site."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return default
    parts = urlsplit(value)
    return default if parts.scheme or parts.netloc else value


def secure_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def page(request: Request, template: str, status: int = 200, **context: Any) -> HTMLResponse:
    s = state_of(request)
    session = current_session(request)
    html = s.jinja.get_template(template).render(
        me=s.me, base=s.base, csrf=session["csrf"] if session else "", **context
    )
    return secure_headers(HTMLResponse(html, status_code=status))  # type: ignore[return-value]


def signed_in(handler: Callable[[Request, dict[str, Any]], Awaitable[Response]]) -> Handler:
    """Run a handler only for a signed-in browser; everyone else goes to /login."""

    async def wrapper(request: Request) -> Response:
        session = current_session(request)
        if session is None:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
        return await handler(request, session)

    wrapper.__name__ = handler.__name__
    return wrapper
