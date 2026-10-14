import re

from .conftest import PASSWORD


def csrf_of(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client, password=PASSWORD, next_="/posts"):
    return client.post(f"/login?next={next_}", data={"password": password})


def test_ui_pages_redirect_to_login_when_signed_out(client):
    r = client.get("/posts")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fposts"


def test_next_keeps_the_query_string(client):
    r = client.get("/posts?type=note&status=draft")
    assert r.headers["location"] == "/login?next=%2Fposts%3Ftype%3Dnote%26status%3Ddraft"


def test_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200 and 'type="password"' in r.text and 'rel="stylesheet"' in r.text


def test_login_sets_a_hardened_cookie_and_redirects(client):
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/posts"
    cookie = r.headers["set-cookie"].lower()
    assert (
        "httponly" in cookie
        and "secure" in cookie
        and "samesite=lax" in cookie
        and "max-age=2592000" in cookie
    )
    assert client.get("/posts").status_code == 200


def test_wrong_password_is_422_and_sets_no_session(client):
    r = login(client, "nope")
    assert r.status_code == 422 and "set-cookie" not in r.headers
    assert client.get("/posts").status_code == 303


def test_login_is_rate_limited(client):
    codes = [login(client, "bad").status_code for _ in range(7)]
    assert codes[:5] == [422] * 5 and codes[5] == 429
    assert login(client, PASSWORD).status_code == 429


def test_next_must_be_a_local_path(client):
    for evil in (
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "javascript:alert(1)",
    ):
        r = client.post("/login", params={"next": evil}, data={"password": PASSWORD})
        assert r.status_code == 303 and r.headers["location"] == "/posts", evil


def test_logout_needs_the_csrf_token_and_clears_the_session(client):
    login(client)
    page = client.get("/posts").text
    assert client.post("/logout", data={"csrf": "wrong"}).status_code == 403
    assert client.get("/posts").status_code == 200
    r = client.post("/logout", data={"csrf": csrf_of(page)})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/posts").status_code == 303


def test_a_tampered_cookie_is_not_a_session(client):
    login(client)
    value = client.cookies.get("ms_session")
    client.cookies.set("ms_session", value[:-3] + "xyz")
    assert client.get("/posts").status_code == 303


def test_a_session_signed_with_another_secret_is_rejected(client, state):
    login(client)
    value = client.cookies.get("ms_session")
    state.settings.secret = "z" * 32
    assert client.get("/posts").status_code == 303
    state.settings.secret = "s" * 32
    assert client.cookies.get("ms_session") == value


def test_bearer_tokens_are_not_sessions_and_sessions_are_not_tokens(client, token):
    assert client.get("/posts", headers={"Authorization": f"Bearer {token}"}).status_code == 303
    login(client)
    client.headers.pop("Authorization", None)
    assert (
        client.get("/micropub", params={"q": "config"}).status_code == 401
    )  # cookie alone is no API access


def test_security_headers_on_ui_pages(client):
    login(client)
    r = client.get("/posts")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"]


def test_static_assets_are_served(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]
    assert client.get("/static/../app.py").status_code in (404, 400)


def test_home_redirects_by_state(client):
    assert client.get("/").status_code == 200  # a small public page with the endpoints
    login(client)
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/posts"
