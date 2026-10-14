import io
import json

from PIL import Image

from .conftest import jpeg_bytes, sign_in


def upload(client, headers, data=None, name="holiday.jpg", **kw):
    data = data or jpeg_bytes()
    return client.post("/media", files={"file": (name, data, "image/jpeg")}, headers=headers, **kw)


def media_files(site):
    return sorted(p for p in (site / "static" / "media").rglob("*") if p.is_file())


def test_upload_stores_cleaned_file_and_returns_location(client, bearer, site):
    r = upload(client, bearer)
    assert r.status_code == 201
    loc = r.headers["location"]
    assert loc.startswith("https://awfulwoman.com/media/2026/10/") and loc.endswith(".jpg")
    assert r.json()["success"] == "create"
    [stored] = media_files(site)
    assert stored.name == loc.rsplit("/", 1)[1]
    assert stored.parent == site / "static" / "media" / "2026" / "10"
    Image.open(io.BytesIO(stored.read_bytes())).verify()


def test_upload_is_recorded(client, bearer, state):
    upload(client, bearer)
    [row] = state.db.query("SELECT * FROM media")
    assert row["media_type"] == "photo" and row["content_type"] == "image/jpeg"
    assert row["path"].startswith("static/media/2026/10/") and row["post_uid"] is None


def test_same_file_twice_is_deduplicated(client, bearer, site, state):
    a = upload(client, bearer)
    b = upload(client, bearer, name="again.jpg")
    assert a.headers["location"] == b.headers["location"]
    assert len(media_files(site)) == 1
    assert len(state.db.query("SELECT * FROM media")) == 1


def test_different_files_get_different_urls(client, bearer, site):
    a = upload(client, bearer, jpeg_bytes(color=(1, 2, 3)))
    b = upload(client, bearer, jpeg_bytes(color=(9, 9, 9)))
    assert a.headers["location"] != b.headers["location"]
    assert len(media_files(site)) == 2


def test_media_scope_or_create_scope_allowed(client):
    for scope in ("media", "create"):
        t = sign_in(client, scope=scope)["access_token"]
        assert upload(client, {"Authorization": f"Bearer {t}"}).status_code == 201


def test_other_scope_is_403(client):
    t = sign_in(client, scope="update")["access_token"]
    r = upload(client, {"Authorization": f"Bearer {t}"})
    assert r.status_code == 403 and r.json()["error"] == "insufficient_scope"


def test_needs_a_token(client):
    assert upload(client, {}).status_code == 401


def test_missing_file_is_400(client, bearer):
    r = client.post("/media", data={"x": "y"}, headers=bearer)
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_unsupported_type_is_415(client, bearer, site):
    r = upload(client, bearer, b"%PDF-1.4 not an image", name="doc.pdf")
    assert r.status_code == 415
    assert not (site / "static").exists()


def test_too_large_is_413(client, bearer, state):
    state.config.media.max_bytes = 50
    assert upload(client, bearer).status_code == 413


def test_store_failure_is_502_and_leaves_no_record(client, bearer, state, monkeypatch):
    from micropub_server.stores import StoreError

    async def boom(changes, message):
        raise StoreError("nope", 403)

    monkeypatch.setattr(state.store, "commit", boom)
    assert upload(client, bearer).status_code == 502
    assert state.db.query("SELECT * FROM media") == []


def test_commit_message(client, bearer, state, monkeypatch):
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    upload(client, bearer)
    assert seen == ["upload photo file"]


# --- queries ----------------------------------------------------------------
def test_q_source_single_and_list(client, bearer):
    url = upload(client, bearer, jpeg_bytes(color=(1, 1, 1))).headers["location"]
    upload(client, bearer, jpeg_bytes(color=(2, 2, 2)))
    one = client.get("/media", params={"q": "source", "url": url}, headers=bearer).json()
    assert (
        one["url"] == url and one["media-type"] == "photo" and one["content-type"] == "image/jpeg"
    )
    assert one["published"] == "2026-10-14T19:10:47+02:00" and one["uid"]
    everything = client.get("/media", params={"q": "source"}, headers=bearer).json()
    assert len(everything["items"]) == 2
    paged = client.get("/media", params={"q": "source", "limit": "1"}, headers=bearer).json()
    assert len(paged["items"]) == 1 and "after" in paged["paging"]


def test_q_source_unknown(client, bearer):
    assert (
        client.get(
            "/media", params={"q": "source", "url": "https://x/y.jpg"}, headers=bearer
        ).status_code
        == 400
    )
    assert (
        client.get("/media", params={"q": "source", "uid": "nope"}, headers=bearer).status_code
        == 404
    )


def test_q_last(client, bearer):
    assert client.get("/media", params={"q": "last"}, headers=bearer).status_code == 404
    url = upload(client, bearer).headers["location"]
    assert client.get("/media", params={"q": "last"}, headers=bearer).json()["url"] == url


def test_q_required(client, bearer):
    assert client.get("/media", headers=bearer).status_code == 400


# --- delete -----------------------------------------------------------------
def test_delete_media(client, bearer, site, state):
    url = upload(client, bearer).headers["location"]
    t = sign_in(client, scope="delete")["access_token"]
    r = client.post(
        "/media", data={"action": "delete", "url": url}, headers={"Authorization": f"Bearer {t}"}
    )
    assert r.status_code == 200 and r.json()["success"] == "delete"
    assert media_files(site) == [] if (site / "static" / "media").exists() else True
    assert state.db.query("SELECT * FROM media") == []


def test_delete_needs_delete_scope(client, bearer):
    url = upload(client, bearer).headers["location"]
    r = client.post("/media", data={"action": "delete", "url": url}, headers=bearer)
    assert r.status_code == 403


def test_delete_unknown_is_400(client):
    t = sign_in(client, scope="delete")["access_token"]
    r = client.post(
        "/media",
        data={"action": "delete", "url": "https://x/nope.jpg"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 400


def test_config_advertises_media_endpoint(client, bearer):
    assert (
        client.get("/micropub", params={"q": "config"}, headers=bearer)
        .json()["media-endpoint"]
        .endswith("/media")
    )
    json.dumps(client.get("/micropub", params={"q": "config"}, headers=bearer).json())
