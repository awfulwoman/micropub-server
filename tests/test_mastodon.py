import json

import httpx
import pytest
import respx

from micropub_server.syndication.base import NotApplicable
from micropub_server.syndication.mastodon import MastodonTarget, build_status

API = "https://indieweb.social"
PERMALINK = "https://awfulwoman.com/notes/2026/10/14/1910/"


def target(**kw):
    return MastodonTarget(url=API, user="awfulwoman", token="tok", **kw)


async def no_media(entry):
    raise AssertionError("no media expected")


# --- the text of a status -------------------------------------------------------------------
def test_plain_note_gets_a_permalink():
    props = {
        "url": PERMALINK,
        "content": {"text": "Hello *world*", "html": "<p>Hello <em>world</em></p>\n"},
    }
    assert build_status(props, limit=500) == f"Hello world\n\n{PERMALINK}"


def test_permalink_is_optional():
    props = {"url": PERMALINK, "content": {"text": "Hi", "html": "<p>Hi</p>"}}
    assert build_status(props, limit=500, include_permalink=False) == "Hi"


def test_links_are_kept_with_their_text():
    props = {
        "url": PERMALINK,
        "content": {
            "text": "x",
            "html": '<p>See <a href="https://example.com/a">this post</a>.</p>',
        },
    }
    assert "this post https://example.com/a" in build_status(props, limit=500)


def test_article_uses_its_title_and_the_permalink():
    props = {
        "url": PERMALINK,
        "name": "Planning Security",
        "content": {"text": "long body", "html": "<p>long body</p>"},
    }
    assert build_status(props, limit=500) == f"Planning Security\n\n{PERMALINK}"


def test_long_text_is_cut_at_a_word_to_leave_room_for_the_link():
    text = " ".join(["word"] * 200)
    out = build_status(
        {"url": PERMALINK, "content": {"text": text, "html": f"<p>{text}</p>"}}, limit=500
    )
    body, link = out.rsplit("\n\n", 1)
    assert link == PERMALINK and body.endswith("…")
    assert len(body) + 2 + 23 <= 500  # Mastodon counts every URL as 23 characters
    assert not body.removesuffix("…").endswith("wor")  # not mid-word


def test_hashtags_are_camel_cased_deduplicated_and_only_added_if_they_fit():
    props = {
        "url": PERMALINK,
        "content": {"text": "x", "html": "<p>x</p>"},
        "category": ["self hosting", "ZFS", "zfs", "open-source"],
    }
    out = build_status(props, limit=500)
    assert out.endswith("#SelfHosting #ZFS #OpenSource")
    tight = {
        "url": PERMALINK,
        "content": {"text": "x" * 470, "html": "<p>" + "x" * 470 + "</p>"},
        "category": ["alpha", "beta"],
    }
    assert "#" not in build_status(tight, limit=500)


def test_repost_with_commentary_includes_the_original_link():
    props = {
        "url": PERMALINK,
        "repost-of": "https://example.com/p",
        "content": {"text": "Worth reading", "html": "<p>Worth reading</p>"},
    }
    assert build_status(props, limit=500) == f"Worth reading https://example.com/p\n\n{PERMALINK}"


def test_photo_only_post_is_just_the_permalink():
    assert build_status({"url": PERMALINK, "photo": [{"url": "a.jpg"}]}, limit=500) == PERMALINK


# --- the API calls ----------------------------------------------------------------------------
@respx.mock
async def test_posts_a_status_with_an_idempotency_key():
    route = respx.post(f"{API}/api/v1/statuses").respond(
        200, json={"url": f"{API}/@awfulwoman/111"}
    )
    out = await target().syndicate(
        {"uid": "u-1", "url": PERMALINK, "content": {"text": "Hi", "html": "<p>Hi</p>"}}, no_media
    )
    assert out == f"{API}/@awfulwoman/111"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["idempotency-key"] == "u-1"
    body = json.loads(req.content)
    assert body["status"] == f"Hi\n\n{PERMALINK}" and body["visibility"] == "public"


@respx.mock
async def test_visibility_follows_the_post_or_the_config():
    route = respx.post(f"{API}/api/v1/statuses").respond(200, json={"url": "u"})
    await target().syndicate(
        {"uid": "1", "url": PERMALINK, "content": "x", "visibility": "unlisted"}, no_media
    )
    assert json.loads(route.calls[0].request.content)["visibility"] == "unlisted"
    await target(visibility="private").syndicate(
        {"uid": "2", "url": PERMALINK, "content": "x"}, no_media
    )
    assert json.loads(route.calls[1].request.content)["visibility"] == "private"


@respx.mock
async def test_like_of_a_status_on_the_same_instance_favourites_it():
    route = respx.post(f"{API}/api/v1/statuses/12345/favourite").respond(200, json={})
    out = await target().syndicate(
        {"uid": "1", "url": PERMALINK, "like-of": f"{API}/@someone/12345"}, no_media
    )
    assert out == f"{API}/@someone/12345" and route.called


@respx.mock
async def test_repost_without_commentary_boosts():
    route = respx.post(f"{API}/api/v1/statuses/777/reblog").respond(200, json={})
    out = await target().syndicate(
        {"uid": "1", "url": PERMALINK, "repost-of": f"{API}/@a/777"}, no_media
    )
    assert out == f"{API}/@a/777" and route.called


@respx.mock
async def test_reply_to_a_status_on_the_same_instance():
    route = respx.post(f"{API}/api/v1/statuses").respond(200, json={"url": f"{API}/@awfulwoman/9"})
    await target().syndicate(
        {"uid": "1", "url": PERMALINK, "in-reply-to": f"{API}/@a/555", "content": "yes!"}, no_media
    )
    assert json.loads(route.calls[0].request.content)["in_reply_to_id"] == "555"


@pytest.mark.parametrize(
    "props",
    [
        {"like-of": "https://elsewhere.example/post"},
        {"in-reply-to": "https://elsewhere.example/post", "content": "x"},
        {"repost-of": "https://elsewhere.example/post"},
    ],
)
async def test_things_that_cannot_be_mirrored_are_not_applicable(props):
    with pytest.raises(NotApplicable):
        await target().syndicate({"uid": "1", "url": PERMALINK, **props}, no_media)


@respx.mock
async def test_photos_are_uploaded_with_alt_text_and_attached():
    upload = respx.post(f"{API}/api/v2/media").respond(200, json={"id": "m1"})
    status = respx.post(f"{API}/api/v1/statuses").respond(200, json={"url": "u"})

    async def fetch(entry):
        return b"JPEGBYTES", "boat.jpg"

    props = {
        "uid": "1",
        "url": PERMALINK,
        "content": "Boats",
        "photo": [{"url": "boat.jpg", "alt": "A boat"}] * 6,
    }
    await target().syndicate(props, fetch)
    assert upload.call_count == 4  # Mastodon allows four
    assert b"A boat" in upload.calls[0].request.content
    assert json.loads(status.calls[0].request.content)["media_ids"] == ["m1"] * 4


@respx.mock
async def test_api_errors_are_raised_with_the_status_and_body():
    respx.post(f"{API}/api/v1/statuses").respond(422, json={"error": "Validation failed"})
    with pytest.raises(Exception, match="422.*Validation failed"):
        await target().syndicate({"uid": "1", "url": PERMALINK, "content": "x"}, no_media)


@respx.mock
async def test_network_errors_are_raised():
    respx.post(f"{API}/api/v1/statuses").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(Exception, match="down"):
        await target().syndicate({"uid": "1", "url": PERMALINK, "content": "x"}, no_media)


def test_target_description_for_micropub_clients():
    info = target(checked=True).info
    assert info["uid"] == f"{API}/@awfulwoman" and info["checked"] is True
    assert info["name"] == "@awfulwoman on indieweb.social"
    assert info["service"]["name"] == "Mastodon" and info["user"]["url"] == f"{API}/@awfulwoman"
