import pytest

from .conftest import PASSWORD


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/auth",
        "/.well-known/oauth-authorization-server",
        "/login",
        "/static/app.css",
        "/nope",
    ],
)
def test_every_response_has_the_basic_security_headers(client, path):
    h = client.get(path).headers
    assert h["strict-transport-security"] == "max-age=31536000"
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "same-origin"


def test_hsts_is_not_sent_over_plain_http(state, tmp_path):
    from starlette.testclient import TestClient

    from micropub_server.app import create_app

    state.settings.base_url = "http://localhost:8000/"
    with TestClient(create_app(state), base_url="http://localhost:8000") as c:
        assert "strict-transport-security" not in c.get("/health").headers


# --- body size limits ---------------------------------------------------------------------------
def test_forms_and_auth_endpoints_have_a_small_limit(client):
    big = "x" * 70_000
    for path in ("/auth/consent?request_uri=x", "/login", "/auth/token"):
        r = client.post(path, data={"password": big})
        assert r.status_code == 413, path


def test_small_bodies_are_fine(client):
    assert client.post("/login", data={"password": "short"}).status_code == 422


def test_uploads_have_a_large_limit_and_it_is_configurable(client, state, bearer):
    state.config.application.max_body_bytes = 5_000
    r = client.post("/micropub", data={"content": "x" * 6_000}, headers=bearer)
    assert r.status_code == 413 and r.json()["error"] == "invalid_request"
    r = client.post("/media", files={"file": ("a.jpg", b"x" * 6_000, "image/jpeg")}, headers=bearer)
    assert r.status_code == 413
    state.config.application.max_body_bytes = 25_000_000
    assert (
        client.post("/micropub", data={"content": "x" * 6_000}, headers=bearer).status_code == 202
    )


def test_a_streamed_body_cannot_dodge_the_limit(client, state):
    def chunks():
        for _ in range(100):
            yield b"a" * 1_000

    r = client.post(
        "/login", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 413


def test_the_ui_forms_can_carry_photos(client, state, site):
    # the large limit applies to the pages that take uploads
    assert client.post("/login", data={"password": PASSWORD}).status_code == 303
    r = client.post(
        "/files/upload",
        data={"csrf": "x"},
        files=[("file", ("a.jpg", b"x" * 100_000, "image/jpeg"))],
    )
    assert r.status_code != 413


# --- framing ----------------------------------------------------------------------------------------
def test_no_page_can_be_framed(client):
    params = {
        "response_type": "code",
        "client_id": "https://app.example/",
        "redirect_uri": "https://app.example/cb",
        "state": "s",
    }
    consent = client.get(client.get("/auth", params=params).headers["location"])
    assert consent.status_code == 200
    for r in (consent, client.get("/auth"), client.get("/login"), client.get("/")):
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_the_consent_page_can_still_redirect_to_the_client(client):
    # a form-action CSP would block the redirect to the app's redirect_uri; it must not be set here
    r = client.get("/auth")
    assert "form-action" not in r.headers["content-security-policy"]
