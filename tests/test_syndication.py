import json

import httpx
import pytest

from micropub_server.syndication.mastodon import MastodonTarget

from .conftest import jpeg_bytes, sign_in

API = "https://indieweb.social"
UID = f"{API}/@awfulwoman"
NOTE_URL = "https://awfulwoman.com/notes/2026/10/14/1910/"


@pytest.fixture
def mastodon(state):
    state.targets = [MastodonTarget(url=API, user="awfulwoman", token="tok", checked=True)]
    return state


@pytest.fixture
def full(client):
    return {
        "Authorization": f"Bearer {sign_in(client, scope='create update delete media')['access_token']}"
    }


def make(client, headers, **data):
    r = client.post("/micropub", data=data, headers=headers)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def live(no_network, url, status=200):
    no_network.get(url).respond(status, text="<html></html>")


def mastodon_status(no_network, url=f"{API}/@awfulwoman/111"):
    return no_network.post(f"{API}/api/v1/statuses").respond(200, json={"url": url})


def fm(site, path):
    from ruamel.yaml import YAML

    return YAML(typ="safe").load((site / path).read_text().split("---\n")[1])


# --- advertising and recording targets -------------------------------------------------------
def test_q_syndicate_to_lists_targets(client, mastodon, full):
    [t] = client.get("/micropub", params={"q": "syndicate-to"}, headers=full).json()["syndicate-to"]
    assert t["uid"] == UID and t["checked"] is True and t["service"]["name"] == "Mastodon"
    config = client.get("/micropub", params={"q": "config"}, headers=full).json()
    assert config["syndicate-to"][0]["uid"] == UID


def test_requested_targets_are_recorded_but_not_written_to_the_file(
    client, mastodon, full, site, state
):
    make(client, full, content="Hello", **{"mp-syndicate-to[]": [UID]})
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert props["mp-syndicate-to"] == [UID]
    assert "syndicate" not in (site / "content/notes/2026/10/14/1910.md").read_text()


def test_a_target_can_be_named_by_its_origin(client, mastodon, full, state):
    make(client, full, content="Hello", **{"mp-syndicate-to": f"{API}/"})
    assert json.loads(state.db.query("SELECT properties FROM posts")[0][0])["mp-syndicate-to"] == [
        UID
    ]


def test_unknown_targets_and_empty_lists_are_dropped(client, mastodon, full, state):
    make(client, full, content="Hello", **{"mp-syndicate-to": "https://nowhere.example/"})
    assert "mp-syndicate-to" not in json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    r = client.post(
        "/micropub",
        json={"type": ["h-entry"], "properties": {"content": ["x"], "mp-syndicate-to": []}},
        headers=full,
    )
    assert r.status_code == 202


# --- POST /syndicate ------------------------------------------------------------------------------
def syndicate(client, headers, **params):
    return client.post("/syndicate", params=params, headers=headers)


def test_syndicates_a_live_post_and_records_the_copy(
    client, mastodon, full, site, state, no_network
):
    make(client, full, content="Hello world", **{"mp-syndicate-to": UID})
    live(no_network, NOTE_URL)
    status = mastodon_status(no_network)
    r = syndicate(client, full)
    assert r.status_code == 200 and r.json()["success"] == "OK"
    assert fm(site, "content/notes/2026/10/14/1910.md")["syndication"] == [f"{API}/@awfulwoman/111"]
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert "mp-syndicate-to" not in props and props["syndication"] == [f"{API}/@awfulwoman/111"]
    sent = json.loads(status.calls[0].request.content)
    assert sent["status"] == f"Hello world\n\n{NOTE_URL}"
    assert status.calls[0].request.headers["idempotency-key"] == props["uid"]


def test_nothing_waiting(client, mastodon, full, no_network):
    make(client, full, content="No targets requested")
    r = syndicate(client, full)
    assert (
        r.status_code == 200 and "No posts awaiting syndication" in r.json()["success_description"]
    )


def test_a_post_that_is_not_live_yet_is_left_queued(client, mastodon, full, state, no_network):
    make(client, full, content="Hello", **{"mp-syndicate-to": UID})
    live(no_network, NOTE_URL, status=404)
    status = mastodon_status(no_network)
    r = syndicate(client, full)
    assert r.status_code == 200 and "not live" in r.json()["success_description"]
    assert not status.called
    assert json.loads(state.db.query("SELECT properties FROM posts")[0][0])["mp-syndicate-to"] == [
        UID
    ]


def test_a_failing_target_stays_queued_and_is_retried(
    client, mastodon, full, site, state, no_network
):
    make(client, full, content="Hello", **{"mp-syndicate-to": UID})
    live(no_network, NOTE_URL)
    failing = no_network.post(f"{API}/api/v1/statuses").respond(500, text="boom")
    r = syndicate(client, full)
    assert r.status_code == 200 and r.json()["failed"] == [UID]
    assert json.loads(state.db.query("SELECT properties FROM posts")[0][0])["mp-syndicate-to"] == [
        UID
    ]
    assert "syndication" not in fm(site, "content/notes/2026/10/14/1910.md")
    failing.respond(200, json={"url": f"{API}/@awfulwoman/222"})
    r = syndicate(client, full)
    assert r.json().get("failed") in (None, [])
    assert fm(site, "content/notes/2026/10/14/1910.md")["syndication"] == [f"{API}/@awfulwoman/222"]


def test_an_existing_copy_is_not_posted_twice(client, mastodon, full, no_network):
    make(
        client,
        full,
        content="Hello",
        **{"mp-syndicate-to": UID, "syndication": f"{API}/@awfulwoman/1"},
    )
    live(no_network, NOTE_URL)
    status = mastodon_status(no_network)
    syndicate(client, full)
    assert not status.called


def test_things_that_cannot_be_mirrored_are_dropped_from_the_queue(
    client, mastodon, full, state, no_network
):
    make(client, full, **{"like-of": "https://elsewhere.example/post", "mp-syndicate-to": UID})
    live(no_network, "https://awfulwoman.com/likes/2026/10/14/1910/")
    r = syndicate(client, full)
    assert r.status_code == 200
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert "mp-syndicate-to" not in props and "syndication" not in props


def test_oldest_waiting_post_goes_first_and_drafts_never(client, mastodon, full, state, no_network):
    make(
        client,
        full,
        content="newer",
        published="2026-10-14T10:00:00+02:00",
        **{"mp-syndicate-to": UID},
    )
    make(
        client,
        full,
        content="older",
        published="2026-10-13T10:00:00+02:00",
        **{"mp-syndicate-to": UID},
    )
    make(
        client,
        full,
        content="draft",
        published="2026-10-12T10:00:00+02:00",
        **{"mp-syndicate-to": UID, "post-status": "draft"},
    )
    live(no_network, "https://awfulwoman.com/notes/2026/10/13/1000/")
    status = mastodon_status(no_network)
    syndicate(client, full)
    assert "older" in json.loads(status.calls[0].request.content)["status"]


def test_a_specific_post_can_be_named(client, mastodon, full, no_network):
    make(
        client,
        full,
        content="first",
        published="2026-10-13T10:00:00+02:00",
        **{"mp-syndicate-to": UID},
    )
    url = make(
        client,
        full,
        content="second",
        published="2026-10-14T10:00:00+02:00",
        **{"mp-syndicate-to": UID},
    )
    live(no_network, url)
    status = mastodon_status(no_network)
    syndicate(client, full, source_url=url)
    assert "second" in json.loads(status.calls[0].request.content)["status"]


def test_needs_an_update_scope(client, mastodon):
    assert syndicate(client, {}).status_code == 401
    t = sign_in(client, scope="create")["access_token"]
    r = syndicate(client, {"Authorization": f"Bearer {t}"})
    assert r.status_code == 403 and r.json()["scope"] == "update"


def test_photos_are_sent_from_the_store(client, mastodon, full, no_network):
    r = client.post(
        "/micropub",
        data={"content": "Boats", "mp-syndicate-to": UID, "mp-photo-alt": "A boat"},
        files={"photo": ("boat.jpg", jpeg_bytes(), "image/jpeg")},
        headers=full,
    )
    live(no_network, r.headers["location"])
    upload = no_network.post(f"{API}/api/v2/media").respond(200, json={"id": "m9"})
    status = mastodon_status(no_network)
    syndicate(client, full)
    assert upload.called and b"A boat" in upload.calls[0].request.content
    assert json.loads(status.calls[0].request.content)["media_ids"] == ["m9"]


def test_a_network_error_counts_as_a_failure(client, mastodon, full, no_network):
    make(client, full, content="Hello", **{"mp-syndicate-to": UID})
    live(no_network, NOTE_URL)
    no_network.post(f"{API}/api/v1/statuses").mock(side_effect=httpx.ConnectError("down"))
    assert syndicate(client, full).json()["failed"] == [UID]
