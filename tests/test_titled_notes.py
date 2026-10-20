"""Notes may have titles on this site: a title alone must never turn a note into an article, and a
move must never lose the post's old URL or front matter added outside the server."""

import json

import pytest
from ruamel.yaml import YAML

from micropub_server.publish import create_post

from .conftest import sign_in

NOTE = "content/notes/2026/10/14/1910.md"


def fm(site, path):
    return YAML(typ="safe").load((site / path).read_text().split("---\n")[1])


@pytest.fixture
def full(client):
    token = sign_in(client, scope="create update delete media")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make(client, headers, **data):
    r = client.post("/micropub", data=data, headers=headers)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def update(client, headers, url, **ops):
    return client.post("/micropub", json={"action": "update", "url": url, **ops}, headers=headers)


def test_giving_a_note_a_title_keeps_it_a_note(client, full, site, state):
    url = make(client, full, content="A note")
    r = update(client, full, url, add={"name": ["Short Title"]})
    assert r.status_code == 200  # not moved
    assert fm(site, NOTE)["title"] == "Short Title"
    assert state.db.query("SELECT post_type FROM posts")[0][0] == "note"


def test_taking_an_articles_title_away_keeps_it_an_article(client, full, site, state):
    url = make(client, full, name="Hello there", content="Body")
    update(client, full, url, add={"summary": ["s"]})
    assert state.db.query("SELECT post_type FROM posts")[0][0] == "article"


def test_other_type_changes_still_happen(client, full, site):
    url = make(client, full, content="A note", name="Titled")
    r = update(client, full, url, add={"photo": ["https://cdn.example/a.jpg"]})
    assert r.status_code == 201
    assert r.headers["location"].startswith("https://awfulwoman.com/photos/")


async def test_a_titled_post_asked_for_as_a_note_is_a_note(state, site):
    created = await create_post(
        state, {"name": "Short Title", "content": "Body"}, draft=False, expect_type="note"
    )
    assert created.url == "https://awfulwoman.com/notes/2026/10/14/1910/"
    assert fm(site, NOTE)["title"] == "Short Title"


async def test_a_titled_post_without_a_type_is_still_an_article(state, site):
    created = await create_post(state, {"name": "Hello", "content": "Body"}, draft=False)
    assert "/posts/" in created.url


def test_a_moved_post_keeps_its_old_url_as_an_alias(client, full, site):
    url = make(client, full, name="Hello there", content="Body")
    update(client, full, url, replace={"mp-slug": ["greetings"]})
    assert "/posts/hello-there/" in fm(site, "content/posts/2026/greetings/index.md")["aliases"]


def test_front_matter_added_to_the_file_outside_the_server_survives_an_edit(
    client, full, site, state
):
    url = make(client, full, content="A note")
    path = site / NOTE
    path.write_text(
        path.read_text().replace(
            "---\n\n",
            "aliases:\n  - /notes/old/\nguid: https://awfulwoman.com/notes/old/\n---\n\n",
            1,
        )
    )
    assert update(client, full, url, replace={"content": ["Changed"]}).status_code == 200
    front = fm(site, NOTE)
    assert front["guid"] == "https://awfulwoman.com/notes/old/"
    assert front["aliases"] == ["/notes/old/"]
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert props["front-matter"]["guid"] == "https://awfulwoman.com/notes/old/"


def test_a_title_added_to_the_file_outside_the_server_survives_an_edit(client, full, site):
    url = make(client, full, content="A note")
    path = site / NOTE
    path.write_text(path.read_text().replace("---\n", "---\ntitle: Local Title\n", 1))
    r = update(client, full, url, replace={"content": ["Changed"]})
    assert r.status_code == 200  # still a note, not moved
    assert fm(site, NOTE)["title"] == "Local Title"


def test_a_client_can_say_it_is_posting_a_note(client, full, site):
    body = {
        "type": ["h-entry"],
        "properties": {"name": ["Short Title"], "content": ["Body"], "mp-post-type": ["note"]},
    }
    r = client.post("/micropub", json=body, headers=full)
    assert r.status_code == 202, r.text
    assert r.headers["location"] == "https://awfulwoman.com/notes/2026/10/14/1910/"
    front = fm(site, NOTE)
    assert front["title"] == "Short Title" and "mp_post_type" not in front


def test_a_wrong_post_type_hint_is_refused(client, full):
    body = {
        "type": ["h-entry"],
        "properties": {"like-of": ["https://x.example/"], "mp-post-type": ["note"]},
    }
    r = client.post("/micropub", json=body, headers=full)
    assert r.status_code == 422
