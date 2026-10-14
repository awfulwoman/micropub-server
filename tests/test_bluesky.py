import base64
import io
import json
import time

import httpx
import pytest
import respx
from PIL import Image

from micropub_server.syndication.base import NotApplicable, SyndicationError
from micropub_server.syndication.bluesky import BlueskyTarget, compose

SERVICE = "https://bsky.social"
PERMALINK = "https://awfulwoman.com/notes/2026/10/14/1910/"
DID = "did:plc:me"


def jwt(payload):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc(payload)}.sig"


def target(**kw):
    return BlueskyTarget(handle="me.bsky.social", password="app-pw", **kw)


async def no_media(entry):
    raise AssertionError("no media expected")


@pytest.fixture
def bsky():
    """A fake Bluesky: login works, and every record created is returned for inspection."""
    now = int(time.time())
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{SERVICE}/xrpc/com.atproto.server.createSession").respond(
            200,
            json={
                "accessJwt": jwt(
                    {"exp": now + 3600, "iat": now, "scope": "com.atproto.appPass", "sub": DID}
                ),
                "refreshJwt": jwt(
                    {"exp": now + 9999, "iat": now, "scope": "com.atproto.refresh", "sub": DID}
                ),
                "handle": "me.bsky.social",
                "did": DID,
                "active": True,
            },
        )
        router.get(url__regex=r".*app\.bsky\.actor\.getProfile.*").respond(
            200, json={"did": DID, "handle": "me.bsky.social"}
        )
        router.created = router.post(f"{SERVICE}/xrpc/com.atproto.repo.createRecord").respond(
            200, json={"uri": f"at://{DID}/app.bsky.feed.post/3kabc", "cid": "bafycid"}
        )
        yield router


def untyped(value):
    """The library tags nested objects with "$type"; the tests care about the data."""
    if isinstance(value, dict):
        return {k: untyped(v) for k, v in value.items() if k != "$type"}
    if isinstance(value, list):
        return [untyped(v) for v in value]
    return value


def record(router, index=0):
    return json.loads(router.created.calls[index].request.content)["record"]


# --- the text of a post --------------------------------------------------------------------------
def segments_text(parts):
    return "".join(text for _, text, _ in parts)


def test_a_note_is_its_text_plus_a_link_to_the_post():
    parts = compose(
        {"url": PERMALINK, "content": {"text": "x", "html": "<p>Hello world</p>"}}, limit=300
    )
    assert segments_text(parts) == f"Hello world\n\n{PERMALINK}"
    assert ("link", PERMALINK, PERMALINK) in parts


def test_links_in_the_text_become_link_facets():
    props = {
        "url": PERMALINK,
        "content": {"text": "x", "html": '<p>See <a href="https://example.com/a">a page</a></p>'},
    }
    assert ("link", "https://example.com/a", "https://example.com/a") in compose(props, limit=300)


def test_hashtags_become_tag_facets():
    props = {
        "url": PERMALINK,
        "content": {"text": "x", "html": "<p>hi</p>"},
        "category": ["self hosting", "ZFS"],
    }
    parts = compose(props, limit=300)
    assert ("tag", "#SelfHosting", "SelfHosting") in parts and ("tag", "#ZFS", "ZFS") in parts


def test_the_limit_is_300_and_a_url_counts_in_full():
    long = " ".join(["word"] * 200)
    parts = compose(
        {"url": PERMALINK, "content": {"text": long, "html": f"<p>{long}</p>"}}, limit=300
    )
    out = segments_text(parts)
    assert len(out) <= 300 and out.endswith(PERMALINK) and "…" in out


def test_articles_use_their_title():
    parts = compose(
        {
            "url": PERMALINK,
            "name": "Planning Security",
            "content": {"text": "b", "html": "<p>b</p>"},
        },
        limit=300,
    )
    assert segments_text(parts) == f"Planning Security\n\n{PERMALINK}"


# --- posting ------------------------------------------------------------------------------------------
async def test_a_post_is_created_with_facets_and_returns_its_web_address(bsky):
    out = await target().syndicate(
        {
            "uid": "u",
            "url": PERMALINK,
            "content": {"text": "Hi", "html": "<p>Hi</p>"},
            "category": ["indieweb"],
        },
        no_media,
    )
    assert out == "https://bsky.app/profile/me.bsky.social/post/3kabc"
    rec = record(bsky)
    assert rec["text"] == f"Hi\n\n{PERMALINK}\n\n#Indieweb"
    kinds = [f["features"][0]["$type"] for f in rec["facets"]]
    assert kinds == ["app.bsky.richtext.facet#link", "app.bsky.richtext.facet#tag"]


async def test_login_failure_is_a_syndication_error():
    with respx.mock:
        respx.post(f"{SERVICE}/xrpc/com.atproto.server.createSession").respond(
            401,
            json={"error": "AuthenticationRequired", "message": "Invalid identifier or password"},
        )
        with pytest.raises(SyndicationError, match="Bluesky"):
            await target().syndicate({"uid": "u", "url": PERMALINK, "content": "x"}, no_media)


async def test_server_errors_are_syndication_errors(bsky):
    bsky.created.respond(500, json={"error": "InternalServerError", "message": "boom"})
    with pytest.raises(SyndicationError, match="Bluesky"):
        await target().syndicate({"uid": "u", "url": PERMALINK, "content": "x"}, no_media)


async def test_network_errors_are_syndication_errors():
    with respx.mock:
        respx.post(f"{SERVICE}/xrpc/com.atproto.server.createSession").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(SyndicationError):
            await target().syndicate({"uid": "u", "url": PERMALINK, "content": "x"}, no_media)


# --- likes, reposts, quotes and replies ---------------------------------------------------------------------
def stub_post(bsky, rkey="3kother", did="did:plc:other", reply=None):
    bsky.get(url__regex=r".*com\.atproto\.identity\.resolveHandle.*").respond(
        200, json={"did": did}
    )
    value = {
        "$type": "app.bsky.feed.post",
        "text": "their post",
        "createdAt": "2026-01-01T00:00:00.000Z",
    }
    if reply:
        value["reply"] = reply
    bsky.get(url__regex=r".*com\.atproto\.repo\.getRecord.*").respond(
        200,
        json={"uri": f"at://{did}/app.bsky.feed.post/{rkey}", "cid": "bafyother", "value": value},
    )


async def test_like_of_a_bluesky_post(bsky):
    stub_post(bsky)
    url = "https://bsky.app/profile/them.bsky.social/post/3kother"
    out = await target().syndicate({"uid": "u", "url": PERMALINK, "like-of": url}, no_media)
    assert out == url
    body = json.loads(bsky.created.calls[0].request.content)
    assert body["collection"] == "app.bsky.feed.like"
    assert untyped(body["record"]["subject"]) == {
        "uri": "at://did:plc:other/app.bsky.feed.post/3kother",
        "cid": "bafyother",
    }


async def test_repost_without_commentary_reposts(bsky):
    stub_post(bsky)
    url = "https://bsky.app/profile/them.bsky.social/post/3kother"
    await target().syndicate({"uid": "u", "url": PERMALINK, "repost-of": url}, no_media)
    assert json.loads(bsky.created.calls[0].request.content)["collection"] == "app.bsky.feed.repost"


async def test_repost_with_commentary_is_a_quote_post(bsky):
    stub_post(bsky)
    url = "https://bsky.app/profile/them.bsky.social/post/3kother"
    await target().syndicate(
        {
            "uid": "u",
            "url": PERMALINK,
            "repost-of": url,
            "content": {"text": "Worth reading", "html": "<p>Worth reading</p>"},
        },
        no_media,
    )
    rec = record(bsky)
    assert rec["embed"]["$type"] == "app.bsky.embed.record"
    assert untyped(rec["embed"]["record"]) == {
        "uri": "at://did:plc:other/app.bsky.feed.post/3kother",
        "cid": "bafyother",
    }
    assert "Worth reading" in rec["text"]


async def test_reply_carries_root_and_parent(bsky):
    root = {"uri": "at://did:plc:root/app.bsky.feed.post/3kroot", "cid": "bafyroot"}
    stub_post(bsky, reply={"root": root, "parent": root})
    url = "https://bsky.app/profile/them.bsky.social/post/3kother"
    await target().syndicate(
        {
            "uid": "u",
            "url": PERMALINK,
            "in-reply-to": url,
            "content": {"text": "agreed", "html": "<p>agreed</p>"},
        },
        no_media,
    )
    reply = untyped(record(bsky)["reply"])
    assert reply["parent"] == {
        "uri": "at://did:plc:other/app.bsky.feed.post/3kother",
        "cid": "bafyother",
    }
    assert reply["root"] == root


@pytest.mark.parametrize(
    "props",
    [
        {"like-of": "https://elsewhere.example/p"},
        {"in-reply-to": "https://elsewhere.example/p", "content": "x"},
        {"repost-of": "https://elsewhere.example/p"},
    ],
)
async def test_things_that_cannot_be_mirrored(props, bsky):
    with pytest.raises(NotApplicable):
        await target().syndicate({"uid": "u", "url": PERMALINK, **props}, no_media)
    assert not bsky.created.called


# --- photos and cards ----------------------------------------------------------------------------------------
def jpeg(size, quality=95):
    import random

    random.seed(1)
    im = Image.frombytes(
        "RGB", size, bytes(random.randrange(256) for _ in range(size[0] * size[1] * 3))
    )
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


async def test_photos_are_uploaded_with_alt_text(bsky):
    upload = bsky.post(f"{SERVICE}/xrpc/com.atproto.repo.uploadBlob").respond(
        200,
        json={
            "blob": {
                "$type": "blob",
                "ref": {"$link": "bafkblob"},
                "mimeType": "image/jpeg",
                "size": 123,
            }
        },
    )

    async def fetch(entry):
        return jpeg((64, 48)), "boat.jpg"

    await target().syndicate(
        {
            "uid": "u",
            "url": PERMALINK,
            "content": "Boats",
            "photo": [{"url": "boat.jpg", "alt": "A boat"}] * 6,
        },
        fetch,
    )
    assert upload.call_count == 4
    images = record(bsky)["embed"]["images"]
    assert (
        len(images) == 4
        and images[0]["alt"] == "A boat"
        and images[0]["image"]["ref"]["$link"] == "bafkblob"
    )


async def test_large_photos_are_shrunk_under_the_one_megabyte_limit(bsky):
    upload = bsky.post(f"{SERVICE}/xrpc/com.atproto.repo.uploadBlob").respond(
        200,
        json={
            "blob": {"$type": "blob", "ref": {"$link": "b"}, "mimeType": "image/jpeg", "size": 1}
        },
    )
    big = jpeg((1800, 1400))
    assert len(big) > 1_000_000

    async def fetch(entry):
        return big, "big.jpg"

    await target().syndicate(
        {"uid": "u", "url": PERMALINK, "content": "x", "photo": [{"url": "big.jpg"}]}, fetch
    )
    assert len(upload.calls[0].request.content) <= 1_000_000


async def test_an_article_gets_a_link_card(bsky):
    await target().syndicate(
        {
            "uid": "u",
            "url": PERMALINK,
            "name": "Planning Security",
            "summary": "Are you secure?",
            "content": {"text": "b", "html": "<p>b</p>"},
        },
        no_media,
    )
    embed = record(bsky)["embed"]
    assert embed["$type"] == "app.bsky.embed.external"
    assert untyped(embed["external"]) == {
        "uri": PERMALINK,
        "title": "Planning Security",
        "description": "Are you secure?",
    }


def test_target_description_for_micropub_clients():
    info = target(checked=False).info
    assert info["uid"] == "https://bsky.app/profile/me.bsky.social"
    assert info["name"] == "@me.bsky.social on Bluesky" and info["checked"] is False
    assert info["service"]["name"] == "Bluesky" and info["user"]["name"] == "me.bsky.social"
