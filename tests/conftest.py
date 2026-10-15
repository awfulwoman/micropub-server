import base64
import hashlib
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from starlette.testclient import TestClient

from micropub_server.app import AppState, create_app
from micropub_server.auth.passwords import hash_password
from micropub_server.auth.service import AuthService
from micropub_server.config import Config, Settings
from micropub_server.db import Database
from micropub_server.stores import FileSystemStore

ME = "https://awfulwoman.com/"
BASE = "https://indie.awfulwoman.com/"
PASSWORD = "correct horse battery"
CLIENT = "https://app.example/"
REDIRECT = "https://app.example/cb"
VERIFIER = "v" * 43

CONFIG = {
    "application": {"time_zone": "Europe/Berlin"},
    "store": {"type": "filesystem", "directory": "SITE"},
    "channels": {"main": {"name": "Main"}, "pages": {"name": "Pages"}},
    "post_types": {
        "note": {
            "name": "Note",
            "post": {
                "path": "content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "notes/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
            "properties": ["content", "category", "location"],
            "required_properties": ["content"],
        },
        "article": {
            "name": "Post",
            "import_slug": "title",
            "post": {"path": "content/posts/{yyyy}/{slug}/index.md", "url": "posts/{slug}/"},
            "properties": ["name", "content", "summary", "category"],
            "required_properties": ["name", "content"],
        },
        "photo": {
            "name": "Photo",
            "post": {
                "path": "content/photos/{yyyy}/{MM}/{dd}/{HH}{mm}/index.md",
                "url": "photos/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
            "properties": ["photo", "name", "content"],
            "required_properties": ["photo"],
        },
        "like": {
            "name": "Like",
            "post": {
                "path": "content/likes/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "likes/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
        },
        "reply": {
            "name": "Reply",
            "post": {
                "path": "content/replies/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "replies/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
        },
        "repost": {
            "name": "Repost",
            "post": {
                "path": "content/reposts/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "reposts/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
        },
        "quote": {
            "name": "Quote",
            "discovery": "quotation-of",
            "post": {
                "path": "content/quotes/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "quotes/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
        },
        "checkin": {
            "name": "Checkin",
            "discovery": "checkin",
            "discovery_first": True,
            "post": {
                "path": "content/checkins/{yyyy}/{MM}/{dd}/{HH}{mm}/index.md",
                "url": "checkins/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
        },
        "page": {
            "name": "Page",
            "channel": "pages",
            "post": {"path": "content/{slug}.md", "url": "{slug}/"},
            "properties": ["name", "content"],
            "required_properties": ["name", "content"],
        },
        "bookmark": {
            "name": "Bookmark",
            "discovery": "bookmark-of",
            "post": {
                "path": "content/bookmarks/{yyyy}/{MM}/{dd}/{HH}{mm}.md",
                "url": "bookmarks/{yyyy}/{MM}/{dd}/{HH}{mm}/",
            },
            "properties": ["bookmark-of", "name", "content"],
            "required_properties": ["bookmark-of"],
        },
    },
}


def public_resolver(host):
    return ["93.184.216.34"]


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    return d


@pytest.fixture
def state(tmp_path, site):
    cfg_data = {**CONFIG, "store": {"type": "filesystem", "directory": str(site)}}
    config = Config.model_validate(cfg_data)
    settings = Settings(
        base_url=BASE,
        publication_url=ME,
        secret="s" * 32,
        password_hash=hash_password(PASSWORD),
        data_dir=str(tmp_path / "data"),
        app_version="test",
    )
    db = Database(tmp_path / "data" / "micropub.db")
    return AppState(
        clock=lambda: datetime(2026, 10, 14, 17, 10, 47, tzinfo=UTC),
        config=config,
        settings=settings,
        db=db,
        auth=AuthService(db, me=ME, token_ttl_days=90),
        store=FileSystemStore(site),
        resolver=public_resolver,
    )


@pytest.fixture
def client(state, no_network):
    with TestClient(
        create_app(state), base_url="https://indie.awfulwoman.com", follow_redirects=False
    ) as c:
        yield c


def pkce_challenge(verifier=VERIFIER):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def sign_in(client, scope="create update media", password=PASSWORD):
    """Run the full IndieAuth flow and return the token response JSON."""
    params = {
        "response_type": "code",
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "state": "xyz",
        "scope": scope,
        "me": ME,
        "code_challenge": pkce_challenge(),
        "code_challenge_method": "S256",
    }
    r = client.get("/auth", params=params)
    assert r.status_code == 302, r.text
    consent = r.headers["location"]
    r = client.post(
        urlsplit(consent).path + "?" + urlsplit(consent).query,
        data={"password": password, "scope": scope.split()},
    )
    assert r.status_code == 302, r.text
    q = parse_qs(urlsplit(r.headers["location"]).query)
    r = client.post(
        "/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def token(client):
    return sign_in(client)["access_token"]


@pytest.fixture
def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def no_network():
    """Every outbound request must be mocked. app.example is a plain client by default."""
    with respx.mock(assert_all_called=False) as router:
        router.get("https://app.example/").respond(
            200,
            headers={"content-type": "text/html"},
            text='<div class="h-app"><a class="u-url p-name" href="https://app.example/">Test app</a></div>',
        )
        yield router


def jpeg_bytes(size=(64, 48), color=(200, 30, 30)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()
