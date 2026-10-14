import httpx
import pytest
import respx

from micropub_server.auth.client import (
    FetchError,
    assert_fetchable,
    fetch_client_info,
    validate_redirect,
)
from micropub_server.auth.passwords import hash_password, verify_password

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


# --- passwords ---------------------------------------------------------------
def test_argon2_roundtrip():
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$")
    assert verify_password("correct horse", password_hash=h)
    assert not verify_password("wrong", password_hash=h)


def test_bcrypt_accepted_during_cutover():
    import bcrypt

    h = bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode()
    assert verify_password("pw", password_secret=h)
    assert not verify_password("nope", password_secret=h)


def test_no_hash_configured_rejects_everything():
    assert not verify_password("anything")


# --- SSRF guard ----------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest",
        "http://100.80.1.1/",  # tailscale CGNAT
        "http://[::1]/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "https:///nohost",
    ],
)
def test_assert_fetchable_blocks(url):
    with pytest.raises(FetchError):
        assert_fetchable(
            url, resolver=lambda host: ["127.0.0.1"] if host == "localhost" else [host]
        )


def test_assert_fetchable_blocks_hostname_resolving_privately():
    with pytest.raises(FetchError):
        assert_fetchable("https://sneaky.example/", resolver=lambda h: ["10.1.2.3"])


def test_assert_fetchable_allows_public():
    assert_fetchable("https://app.example/", resolver=PUBLIC)


# --- client info ---------------------------------------------------------------
@respx.mock
async def test_client_info_from_h_app():
    respx.get("https://app.example/").respond(
        200,
        headers={"content-type": "text/html"},
        text='<div class="h-app"><a class="u-url p-name" href="https://app.example/">Quill</a>'
        '<img class="u-logo" src="/logo.png"></div>',
    )
    info = await fetch_client_info("https://app.example/", resolver=PUBLIC)
    assert info["name"] == "Quill"
    assert info["logo"] == "https://app.example/logo.png"


@respx.mock
async def test_client_info_from_json_metadata():
    respx.get("https://app.example/id").respond(
        200,
        headers={"content-type": "application/json"},
        json={
            "client_id": "https://app.example/id",
            "client_name": "Cool",
            "logo_uri": "https://app.example/l.png",
        },
    )
    info = await fetch_client_info("https://app.example/id", resolver=PUBLIC)
    assert info == {"name": "Cool", "url": None, "logo": "https://app.example/l.png"}


@respx.mock
async def test_client_info_fallback_on_failure():
    respx.get("https://app.example/").respond(500)
    assert await fetch_client_info("https://app.example/", resolver=PUBLIC) is None


async def test_client_info_blocked_host_returns_none():
    assert await fetch_client_info("http://127.0.0.1/", resolver=PUBLIC) is None


# --- redirect validation ---------------------------------------------------------
async def test_same_origin_redirect_ok():
    assert await validate_redirect(
        "https://app.example/cb", "https://app.example/", resolver=PUBLIC
    )


async def test_different_scheme_not_same_origin():
    # http redirect for an https client_id must fall through to declared-URI check
    with respx.mock:
        respx.get("https://app.example/").respond(
            200, text="<html></html>", headers={"content-type": "text/html"}
        )
        assert not await validate_redirect(
            "http://app.example/cb", "https://app.example/", resolver=PUBLIC
        )


@respx.mock
async def test_declared_redirect_in_html():
    respx.get("https://app.example/").respond(
        200,
        headers={"content-type": "text/html"},
        text='<link rel="redirect_uri" href="https://other.example/cb">',
    )
    assert await validate_redirect(
        "https://other.example/cb", "https://app.example/", resolver=PUBLIC
    )
    assert not await validate_redirect(
        "https://other.example/x", "https://app.example/", resolver=PUBLIC
    )


@respx.mock
async def test_declared_redirect_in_link_header():
    respx.get("https://app.example/").respond(
        200,
        headers={
            "content-type": "text/html",
            "link": '<https://other.example/cb>; rel="redirect_uri"',
        },
        text="",
    )
    assert await validate_redirect(
        "https://other.example/cb", "https://app.example/", resolver=PUBLIC
    )


@respx.mock
async def test_redirect_validation_fetch_failure_is_false():
    respx.get("https://app.example/").mock(side_effect=httpx.ConnectError("x"))
    assert not await validate_redirect(
        "https://evil.example/cb", "https://app.example/", resolver=PUBLIC
    )
