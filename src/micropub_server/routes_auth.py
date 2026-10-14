"""IndieAuth endpoints (spec 02)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .auth.client import fetch_client_info, validate_redirect
from .auth.passwords import verify_password
from .auth.service import AuthError
from .http import bearer_token, error_page, oauth_error, read_params, render, state_of, wants_form

SCOPES = {
    "create": "Create posts and upload files",
    "update": "Edit posts",
    "delete": "Delete posts",
    "draft": "Create and edit draft posts only",
    "media": "Upload media files",
    "profile": "See your name and photo",
}
_DOT_SEGMENTS = re.compile(r"^[^:]+://[^/?#]*[^?#]*/\.{1,2}(/|[?#]|$)")


def _issuer(request: Request) -> str:
    return state_of(request).base


async def metadata(request: Request) -> Response:
    base = _issuer(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}auth",
            "token_endpoint": f"{base}auth/token",
            "introspection_endpoint": f"{base}auth/introspect",
            "introspection_endpoint_auth_methods_supported": ["Bearer"],
            "revocation_endpoint": f"{base}auth/revoke",
            "revocation_endpoint_auth_methods_supported": ["none"],
            "userinfo_endpoint": f"{base}auth/userinfo",
            "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": f"{base}auth",
        }
    )


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return bool(parts.scheme in {"http", "https"} and parts.hostname) and not _DOT_SEGMENTS.match(
        value
    )


async def authorize(request: Request) -> Response:
    """GET: validate the request, then send the user to the consent page."""
    s = state_of(request)
    if not request.query_params:
        return render(request, "docs.html")
    q = dict(request.query_params)

    response_type = q.get("response_type") or (
        "id" if q.get("client_id", "").startswith("https://indieauth.com") else None
    )
    for name, value in (
        ("client_id", q.get("client_id")),
        ("redirect_uri", q.get("redirect_uri")),
        ("response_type", response_type),
        ("state", q.get("state")),
    ):
        if not value:
            return error_page(request, f"Missing required parameter: {name}")
    if response_type not in {"code", "id"}:
        return error_page(request, "Invalid value for response_type")
    for name in ("client_id", "redirect_uri", "me"):
        if name in q and not _valid_url(q[name]):
            return error_page(request, f"Invalid value for {name}")

    challenge, method = q.get("code_challenge"), q.get("code_challenge_method")
    if challenge or method:
        for name, value in (("code_challenge", challenge), ("code_challenge_method", method)):
            if not value:
                return error_page(request, f"Missing required parameter: {name}")
        if method != "S256":
            return error_page(request, "Invalid value for code_challenge_method")

    if not await validate_redirect(q["redirect_uri"], q["client_id"], resolver=s.resolver):
        return error_page(
            request, "Invalid value for redirect_uri: it does not match the client_id"
        )

    client = await fetch_client_info(q["client_id"], resolver=s.resolver)
    params = {
        k: q[k]
        for k in (
            "client_id",
            "redirect_uri",
            "state",
            "scope",
            "me",
            "code_challenge",
            "code_challenge_method",
        )
        if k in q
    }
    request_uri = s.auth.new_request(params, client)
    return RedirectResponse(
        f"{s.base}auth/consent?{urlencode({'request_uri': request_uri})}", status_code=302
    )


def _redirect(params: dict[str, Any], issuer: str, **extra: str) -> RedirectResponse:
    target = {**extra, "state": params["state"], "iss": issuer}
    sep = "&" if urlsplit(params["redirect_uri"]).query else "?"
    return RedirectResponse(f"{params['redirect_uri']}{sep}{urlencode(target)}", status_code=302)


def _consent_context(
    request: Request, request_uri: str, params: dict, client: dict | None, error: str | None = None
):
    requested = params.get("scope", "").split()
    return {
        "request_uri": request_uri,
        "client": client,
        "client_id": params["client_id"],
        "scopes": [(sc, SCOPES.get(sc, sc)) for sc in requested],
        "error": error,
    }


async def consent_get(request: Request) -> Response:
    s = state_of(request)
    request_uri = request.query_params.get("request_uri", "")
    found = s.auth.get_request(request_uri)
    if not found:
        return error_page(
            request, "This sign-in request is unknown or has expired. Start again from the app."
        )
    params, client = found
    return render(request, "consent.html", **_consent_context(request, request_uri, params, client))


async def consent_post(request: Request) -> Response:
    s = state_of(request)
    form = await request.form()
    request_uri = request.query_params.get("request_uri") or form.get("request_uri", "")
    found = s.auth.get_request(request_uri)
    if not found:
        return error_page(
            request, "This sign-in request is unknown or has expired. Start again from the app."
        )
    params, client = found
    issuer = _issuer(request)

    if form.get("deny"):
        s.auth.delete_request(request_uri)
        return _redirect(
            params, issuer, error="access_denied", error_description="The user denied the request"
        )

    ip = request.client.host if request.client else "unknown"
    if not s.auth.login_allowed(ip):
        return render(
            request,
            "consent.html",
            429,
            **_consent_context(
                request,
                request_uri,
                params,
                client,
                "Too many attempts. Wait a minute and try again.",
            ),
        )
    if not verify_password(
        str(form.get("password", "")),
        password_hash=s.settings.password_hash,
        password_secret=s.settings.password_secret,
    ):
        s.auth.record_login_failure(ip)
        return render(
            request,
            "consent.html",
            422,
            **_consent_context(request, request_uri, params, client, "Wrong password."),
        )

    requested = params.get("scope", "").split()
    granted = [sc for sc in requested if sc in set(form.getlist("scope"))] if requested else []
    if requested and not granted:
        s.auth.delete_request(request_uri)
        return _redirect(
            params, issuer, error="access_denied", error_description="No permissions were granted"
        )

    code = s.auth.issue_code(params, " ".join(granted) or None)
    s.auth.delete_request(request_uri)
    extra = {"code": code}
    if params.get("me"):
        extra["me"] = params["me"]
    return _redirect(params, issuer, **extra)


def _profile(request: Request) -> dict[str, str]:
    return dict(state_of(request).config.publication.profile)


async def _redeem(request: Request) -> tuple[Any, dict[str, Any]] | JSONResponse:
    s = state_of(request)
    p = await read_params(request)
    for name in ("code", "client_id", "redirect_uri"):
        if not p.get(name):
            return oauth_error("invalid_request", f"Missing required parameter: {name}")
    try:
        data = s.auth.redeem_code(
            str(p["code"]),
            client_id=str(p["client_id"]),
            redirect_uri=str(p["redirect_uri"]),
            code_verifier=p.get("code_verifier"),
        )
    except AuthError as exc:
        return oauth_error(exc.error, exc.description, exc.status)
    return data, p


def _respond(request: Request, body: dict[str, Any]) -> Response:
    if wants_form(request):
        return Response(
            urlencode(body),
            media_type="application/x-www-form-urlencoded",
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def authorization_post(request: Request) -> Response:
    """Redeem a code for the profile URL only (no token)."""
    result = await _redeem(request)
    if isinstance(result, JSONResponse):
        return result
    data, _ = result
    body: dict[str, Any] = {"me": data.me}
    if "profile" in (data.scope or "").split() and _profile(request):
        body["profile"] = _profile(request)
    return _respond(request, body)


async def token_post(request: Request) -> Response:
    p = await read_params(request)
    if p.get("action") == "revoke" and p.get("token"):
        state_of(request).auth.revoke(str(p["token"]))
        return Response(status_code=200)
    if p.get("grant_type") != "authorization_code":
        return oauth_error(
            "unsupported_grant_type" if p.get("grant_type") else "invalid_request",
            "grant_type must be authorization_code",
        )
    result = await _redeem(request)
    if isinstance(result, JSONResponse):
        return result
    data, _ = result
    try:
        body = state_of(request).auth.issue_token(data)
    except AuthError as exc:
        return oauth_error(exc.error, exc.description, exc.status)
    if "profile" in body["scope"].split() and _profile(request):
        body["profile"] = _profile(request)
    return _respond(request, body)


def _authenticated(request: Request):
    token = bearer_token(request)
    return state_of(request).auth.verify(token) if token else None


async def introspect(request: Request) -> Response:
    if _authenticated(request) is None:
        return oauth_error("unauthorized", "A valid access token is required", 401)
    p = await read_params(request)
    return JSONResponse(state_of(request).auth.introspect(str(p.get("token", ""))))


async def revoke(request: Request) -> Response:
    p = await read_params(request)
    if p.get("token"):
        state_of(request).auth.revoke(str(p["token"]))
    return Response(status_code=200)


async def userinfo(request: Request) -> Response:
    info = _authenticated(request)
    if info is None:
        return oauth_error("unauthorized", "A valid access token is required", 401)
    if "profile" not in info.scopes:
        return oauth_error(
            "insufficient_scope", "The profile scope is required", 403, scope="profile"
        )
    return JSONResponse(_profile(request))


def routes() -> list[Route]:
    return [
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/auth", authorize, methods=["GET"]),
        Route("/auth", authorization_post, methods=["POST"]),
        Route("/auth/consent", consent_get, methods=["GET"]),
        Route("/auth/consent", consent_post, methods=["POST"]),
        Route("/auth/token", token_post, methods=["POST"]),
        Route("/auth/introspect", introspect, methods=["POST"]),
        Route("/auth/revoke", revoke, methods=["POST"]),
        Route("/auth/userinfo", userinfo, methods=["GET"]),
    ]
