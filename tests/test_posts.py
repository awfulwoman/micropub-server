from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from micropub_server.config import PostType
from micropub_server.posts import (
    discover_post_type,
    get_slug,
    normalise,
    parse_geo,
    render_template,
)

BERLIN = "Europe/Berlin"


def pt(**kw):
    kw.setdefault("name", "X")
    kw.setdefault("post", {"path": "content/x/{slug}.md"})
    return PostType(**kw)


TYPES = {
    "note": pt(),
    "article": pt(),
    "bookmark": pt(discovery="bookmark-of"),
    "checkin": pt(discovery="checkin", discovery_first=True),
    "page": pt(channel="pages"),
    "quote": pt(discovery="quotation-of"),
    "photo": pt(discovery_without=["content"], discovery_without_type="note"),
}


# --- discovery ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("props", "expected"),
    [
        ({"content": "hi"}, "note"),
        ({"name": "T", "content": "hi"}, "article"),
        ({"name": "T", "summary": "s"}, "article"),
        ({"name": "T"}, "note"),
        ({"bookmark-of": "https://x"}, "bookmark"),
        ({"like-of": "https://x"}, "like"),
        ({"in-reply-to": "https://x"}, "reply"),
        ({"repost-of": "https://x"}, "repost"),
        ({"photo": [{"url": "/a.jpg"}]}, "photo"),
        # built-in photo wins over configured bookmark discovery (decision 2026-10-14)
        ({"bookmark-of": "https://x", "photo": [{"url": "/a.jpg"}]}, "photo"),
        ({"quotation-of": "https://x", "content": "q"}, "quote"),
        # discovery_first: a checkin with a photo stays a checkin
        ({"checkin": {"type": "card"}, "photo": [{"url": "/a.jpg"}]}, "checkin"),
        ({"type": "event"}, "event"),
        # discovery_without(_type): a photo with words is a note, titled or not
        ({"photo": [{"url": "/a.jpg"}], "content": "hi"}, "note"),
        ({"photo": [{"url": "/a.jpg"}], "name": "T", "content": "hi"}, "note"),
        ({"photo": [{"url": "/a.jpg"}], "name": "T"}, "photo"),
        # a later property rule still wins over discovery_without_type
        ({"photo": [{"url": "/a.jpg"}], "content": "hi", "bookmark-of": "https://x"}, "bookmark"),
    ],
)
def test_discover(props, expected):
    assert discover_post_type(TYPES, props) == expected


def test_discover_channel_binding():
    assert discover_post_type(TYPES, {"name": "Uses", "channel": ["pages"]}) == "page"


# --- slugs -------------------------------------------------------------------
def test_slug_prefers_mp_slug():
    assert get_slug({"mp-slug": "My Slug", "name": "Other"}, "-") == "my-slug"


def test_slug_from_name_first_five_words():
    assert get_slug({"name": "One two three four five six seven"}, "-") == "one-two-three-four-five"


def test_slug_falls_back_to_random_id():
    # Not derived from anything on the post, so a URL cannot be guessed from its date.
    props = {"published": "2026-10-14T19:10:00+02:00"}
    slug = get_slug(props, "-")
    assert len(slug) == 8 and slug.isalnum() and slug == slug.lower()
    assert slug != get_slug(props, "-")


def test_slug_separator():
    assert get_slug({"name": "a b"}, "_") == "a_b"


# --- geo ---------------------------------------------------------------------
def test_parse_geo():
    assert parse_geo("geo:52.5,13.4") == {"type": "geo", "latitude": "52.5", "longitude": "13.4"}
    assert parse_geo("geo:52.5,13.4,30")["altitude"] == "30"
    assert parse_geo("not geo") == "not geo"


# --- normalise ---------------------------------------------------------------
def n(props, **kw):
    kw.setdefault("tz", BERLIN)
    kw.setdefault("me", "https://awfulwoman.com/")
    kw.setdefault("slug_separator", "-")
    kw.setdefault("channels", [])
    kw.setdefault("now", datetime(2026, 10, 14, 17, 10, 47, tzinfo=ZoneInfo("UTC")))
    return normalise(props, **kw)


def test_normalise_published_default_now_in_zone():
    assert n({"content": "x"})["published"] == "2026-10-14T19:10:47+02:00"


def test_normalise_published_converted_to_zone():
    out = n({"published": "2026-10-14T10:00:00Z"})
    assert out["published"] == "2026-10-14T12:00:00+02:00"


def test_normalise_published_client_zone_kept():
    out = n({"published": "2026-10-14T10:00:00-05:00"}, tz="client")
    assert out["published"] == "2026-10-14T10:00:00-05:00"


def test_normalise_content_text_to_html():
    out = n({"content": "Hello *world*"})
    assert out["content"] == {"text": "Hello *world*", "html": "<p>Hello <em>world</em></p>\n"}


def test_normalise_content_html_only_gets_text():
    out = n({"content": {"html": "<p>Hi <b>there</b></p>"}})
    assert out["content"]["text"].strip() == "Hi **there**"
    assert out["content"]["html"] == "<p>Hi <b>there</b></p>"


def test_normalise_categories():
    out = n({"category": [" Foo ", "foo", "", "Bar"]})
    assert out["category"] == ["Foo", "Bar"]


def test_normalise_empty_categories_removed():
    assert "category" not in n({"category": ["", " "]})


def test_normalise_category_string_to_list():
    assert n({"category": "solo"})["category"] == ["solo"]


def test_normalise_name_trimmed_and_empty_removed():
    assert n({"name": "  T  "})["name"] == "T"
    assert "name" not in n({"name": "  "})


def test_normalise_photo_alt_and_relative():
    out = n(
        {
            "photo": ["https://awfulwoman.com/media/a.jpg", "https://cdn.example/b.jpg"],
            "mp-photo-alt": ["First"],  # shorter than photo: must not crash
        }
    )
    assert out["photo"] == [
        {"url": "/media/a.jpg", "alt": "First"},
        {"url": "https://cdn.example/b.jpg"},
    ]
    assert "mp-photo-alt" not in out


def test_normalise_location_geo_uri():
    assert n({"location": "geo:1,2"})["location"]["type"] == "geo"


def test_normalise_syndicate_to_shapes():
    assert n({"mp-syndicate-to": "a"})["mp-syndicate-to"] == ["a"]
    assert "mp-syndicate-to" not in n({"mp-syndicate-to": {}})  # empty object from mf2 conversion
    assert "mp-syndicate-to" not in n({"mp-syndicate-to": []})


def test_normalise_channel_default_and_selected():
    ch = ["main", "pages"]
    assert n({}, channels=ch)["channel"] == ["main"]
    assert n({"mp-channel": "pages"}, channels=ch)["channel"] == ["pages"]
    assert n({"mp-channel": "nope"}, channels=ch)["channel"] == ["main"]
    assert "mp-channel" not in n({"mp-channel": "pages"}, channels=ch)


def test_normalise_slug_and_status():
    out = n({"name": "Hello there world"})
    assert out["slug"] == "hello-there-world"
    assert out["post-status"] == "published"
    assert n({"post-status": "draft"})["post-status"] == "draft"


# --- path templates ----------------------------------------------------------
def test_render_template_tokens():
    props = {
        "published": "2026-10-14T19:10:47+02:00",
        "slug": "hi",
        "uid": "u-1",
        "channel": ["a", "b"],
    }
    out = render_template(
        "c/{yyyy}/{MM}/{dd}/{HH}{mm}/{slug}-{n}-{uuid}-{channel}.md",
        props,
        tz=BERLIN,
        n=3,
        separator="-",
    )
    assert out == "c/2026/10/14/1910/hi-3-u-1-a-b.md"


def test_render_template_uses_zone_not_utc():
    # 23:30 UTC is already the next day in Berlin
    props = {"published": "2026-10-14T23:30:00+00:00"}
    assert render_template("{dd}-{HH}", props, tz=BERLIN) == "15-01"


def test_render_template_unknown_token_left_alone():
    assert (
        render_template("a/{nope}.md", {"published": "2026-10-14T19:10:47+02:00"}, tz=BERLIN)
        == "a/{nope}.md"
    )


def test_normalise_updated_is_converted_like_published():
    out = n({"updated": "2021-02-24T08:00:00.000Z"})
    assert out["updated"] == "2021-02-24T09:00:00+01:00"  # Berlin is on CET in February
    assert "updated" not in n({})
