import pytest

API_PATHS = [
    "/micropub",
    "/media",
    "/auth/token",
    "/auth/introspect",
    "/auth/revoke",
    "/auth/userinfo",
    "/syndicate",
]


@pytest.mark.parametrize("path", API_PATHS)
def test_preflight_is_answered_for_api_endpoints(client, path):
    r = client.options(
        path,
        headers={
            "Origin": "https://webapp.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "POST" in r.headers["access-control-allow-methods"]
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "content-type" in allowed
    assert int(r.headers["access-control-max-age"]) > 0


def test_actual_responses_are_readable_cross_origin_including_the_location(client, bearer):
    r = client.post(
        "/micropub", data={"content": "x"}, headers={**bearer, "Origin": "https://webapp.example"}
    )
    assert r.status_code == 202
    assert r.headers["access-control-allow-origin"] == "*"
    assert "location" in r.headers["access-control-expose-headers"].lower()


def test_error_responses_are_readable_too(client):
    r = client.get(
        "/micropub", params={"q": "config"}, headers={"Origin": "https://webapp.example"}
    )
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == "*"


def test_the_metadata_document_is_readable_cross_origin(client):
    r = client.get(
        "/.well-known/oauth-authorization-server", headers={"Origin": "https://webapp.example"}
    )
    assert r.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize(
    "path",
    ["/posts", "/login", "/files", "/status", "/", "/health", "/auth/consent", "/static/app.css"],
)
def test_cookie_based_pages_are_never_opened_up(client, path):
    r = client.get(path, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_preflight_for_a_ui_page_is_not_approved(client):
    r = client.options(
        "/posts/new/note",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers


def test_credentials_are_never_allowed(client):
    r = client.options(
        "/micropub",
        headers={"Origin": "https://webapp.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-credentials" not in r.headers
