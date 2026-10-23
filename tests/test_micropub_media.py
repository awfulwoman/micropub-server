import io

from PIL import Image

from .conftest import jpeg_bytes

BUNDLE = "content/photos/2026/10/14/1910"


def post_photo(client, headers, files=None, data=None):
    files = files if files is not None else [("photo", ("boat.jpg", jpeg_bytes(), "image/jpeg"))]
    return client.post(
        "/micropub", data=data or {"h": "entry", "content": "Boats!"}, files=files, headers=headers
    )


def front_matter(site, path):
    text = (site / path).read_text()
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(text.split("---\n")[1])


def test_uploaded_photo_joins_the_bundle(client, bearer, site):
    r = post_photo(client, bearer, data={"content": "Boats!", "mp-photo-alt": "A boat on a canal"})
    assert r.status_code == 202, r.text
    assert r.headers["location"] == "https://awfulwoman.com/photos/2026/10/14/1910/"
    assert (site / BUNDLE / "boat.jpg").is_file()
    fm = front_matter(site, f"{BUNDLE}/index.md")
    assert fm["images"] == [{"file": "boat.jpg", "alt": "A boat on a canal"}]
    assert not (site / "static").exists()


def test_post_and_files_are_one_commit(client, bearer, state, monkeypatch):
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append((len(changes), message))
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    files = [
        ("photo", ("a.jpg", jpeg_bytes(color=(1, 1, 1)), "image/jpeg")),
        ("photo", ("b.jpg", jpeg_bytes(color=(2, 2, 2)), "image/jpeg")),
    ]
    assert post_photo(client, bearer, files=files).status_code == 202
    assert commits == [(3, "create photo post")]


def test_uploaded_photo_is_cleaned(client, bearer, site):
    import tests.test_media_processing as t

    post_photo(client, bearer, files=[("photo", ("gps.jpg", t.jpeg_with_gps(), "image/jpeg"))])
    stored = (site / BUNDLE / "gps.jpg").read_bytes()
    assert not Image.open(io.BytesIO(stored)).getexif().get_ifd(0x8825)
    assert b"SecretCameraMaker" not in stored


def test_duplicate_filenames_in_one_post_are_made_unique(client, bearer, site):
    files = [
        ("photo", ("same.jpg", jpeg_bytes(color=(1, 1, 1)), "image/jpeg")),
        ("photo", ("same.jpg", jpeg_bytes(color=(2, 2, 2)), "image/jpeg")),
    ]
    post_photo(client, bearer, files=files)
    assert (site / BUNDLE / "same.jpg").is_file() and (site / BUNDLE / "same-2.jpg").is_file()
    assert [i["file"] for i in front_matter(site, f"{BUNDLE}/index.md")["images"]] == [
        "same.jpg",
        "same-2.jpg",
    ]


def test_alt_texts_apply_in_order(client, bearer, site):
    files = [
        ("photo", ("a.jpg", jpeg_bytes(color=(1, 1, 1)), "image/jpeg")),
        ("photo", ("b.jpg", jpeg_bytes(color=(2, 2, 2)), "image/jpeg")),
    ]
    post_photo(
        client, bearer, files=files, data={"content": "x", "mp-photo-alt[]": ["First", "Second"]}
    )
    assert front_matter(site, f"{BUNDLE}/index.md")["images"] == [
        {"file": "a.jpg", "alt": "First"},
        {"file": "b.jpg", "alt": "Second"},
    ]


def test_bracketed_field_name_works(client, bearer, site):
    post_photo(client, bearer, files=[("photo[]", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert (site / BUNDLE / "a.jpg").is_file()


def test_unsupported_upload_fails_cleanly(client, bearer, site, state):
    r = post_photo(client, bearer, files=[("photo", ("x.pdf", b"%PDF-1.4 hi", "application/pdf"))])
    assert r.status_code == 415 and r.json()["error"] == "invalid_request"
    assert state.db.query("SELECT * FROM posts") == []
    assert not any(site.rglob("*.md"))


def test_files_in_unexpected_fields_are_rejected(client, bearer):
    r = post_photo(client, bearer, files=[("evil", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 400


def test_store_failure_records_nothing(client, bearer, state, monkeypatch):
    from micropub_server.stores import StoreError

    async def boom(changes, message):
        raise StoreError("nope", 403)

    monkeypatch.setattr(state.store, "commit", boom)
    assert post_photo(client, bearer).status_code == 502
    assert (
        state.db.query("SELECT * FROM posts") == [] and state.db.query("SELECT * FROM media") == []
    )


def test_media_rows_are_attached_to_the_post(client, bearer, state):
    post_photo(client, bearer)
    [m] = state.db.query("SELECT * FROM media")
    [p] = state.db.query("SELECT uid FROM posts")
    assert m["post_uid"] == p["uid"] and m["path"] == f"{BUNDLE}/boat.jpg"
    assert m["url"] == "https://awfulwoman.com/photos/2026/10/14/1910/boat.jpg"


def test_non_bundle_photo_type_uses_static_media(client, bearer, site, state):
    state.config.post_types["photo"].post.path = "content/photos/{yyyy}/{slug}.md"
    state.config.post_types["photo"].post.url = "photos/{slug}/"
    r = post_photo(client, bearer, data={"name": "Boat", "content": "x"})
    assert r.status_code == 202, r.text
    [stored] = [p for p in (site / "static" / "media").rglob("*.jpg")]
    fm = front_matter(site, "content/photos/2026/boat.md")
    assert fm["images"] == [{"file": stored.name}]


def test_photo_with_words_is_a_note_bundle_when_photo_says_so(client, bearer, site, state):
    state.config.post_types["photo"].discovery_without = ["content"]
    state.config.post_types["photo"].discovery_without_type = "note"
    state.config.post_types["note"].post.path = "content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}/index.md"
    data = {"name": "Boats", "content": "Boats!", "mp-photo-alt": "A boat"}
    r = post_photo(client, bearer, data=data)
    assert r.status_code == 202, r.text
    assert r.headers["location"] == "https://awfulwoman.com/notes/2026/10/14/1910/"
    assert (site / "content/notes/2026/10/14/1910/boat.jpg").is_file()
    fm = front_matter(site, "content/notes/2026/10/14/1910/index.md")
    assert fm["images"] == [{"file": "boat.jpg", "alt": "A boat"}]
    assert (
        post_photo(client, bearer, data={"name": "Bare"})
        .headers["location"]
        .startswith("https://awfulwoman.com/photos/")
    )


# --- media uploaded earlier through /media ---------------------------------------------
def upload(client, bearer, color=(5, 5, 5), name="canal.jpg"):
    r = client.post(
        "/media", files={"file": (name, jpeg_bytes(color=color), "image/jpeg")}, headers=bearer
    )
    assert r.status_code == 201
    return r.headers["location"]


def test_preuploaded_media_moves_into_the_bundle(client, bearer, site, state):
    url = upload(client, bearer)
    old = next((site / "static" / "media").rglob("*.jpg"))
    r = client.post(
        "/micropub",
        json={"type": ["h-entry"], "properties": {"photo": [url], "content": ["Canal"]}},
        headers=bearer,
    )
    assert r.status_code == 202, r.text
    assert not old.exists()
    [moved] = (site / BUNDLE).glob("*.jpg")
    assert front_matter(site, f"{BUNDLE}/index.md")["images"] == [{"file": moved.name}]
    [m] = state.db.query("SELECT * FROM media")
    assert m["path"] == f"{BUNDLE}/{moved.name}" and m["post_uid"] is not None


def test_media_already_used_by_another_post_is_left_in_place(client, bearer, site):
    url = upload(client, bearer)
    body = {"type": ["h-entry"], "properties": {"photo": [url]}}
    assert client.post("/micropub", json=body, headers=bearer).status_code == 202
    second = client.post("/micropub", json=body, headers=bearer)
    assert second.status_code == 202
    # the file belongs to the first post's bundle; the second post just references it by name
    assert len(list((site / "content" / "photos").rglob("*.jpg"))) == 1


def test_external_photo_urls_are_left_alone(client, bearer, site):
    body = {
        "type": ["h-entry"],
        "properties": {"photo": [{"value": "https://cdn.example/x/pic.jpg", "alt": "Elsewhere"}]},
    }
    assert client.post("/micropub", json=body, headers=bearer).status_code == 202
    assert front_matter(site, f"{BUNDLE}/index.md")["images"] == [
        {"file": "pic.jpg", "alt": "Elsewhere"}
    ]
    assert list((site / BUNDLE).iterdir()) == [site / BUNDLE / "index.md"]
