from ruamel.yaml import YAML

from micropub_server.config import Template
from micropub_server.templates import render_post


def split(text):
    assert text.startswith("---\n")
    front, body = text[4:].split("\n---\n", 1)
    return YAML(typ="safe").load(front) or {}, body


def render(props, **kw):
    return render_post(props, Template(**kw))


def test_note_exact_output():
    out = render(
        {
            "type": "entry",
            "published": "2026-10-14T19:10:00+02:00",
            "category": ["indieweb"],
            "content": {"text": "Hello", "html": "<p>Hello</p>\n"},
            "slug": "x",
            "uid": "u",
            "url": "https://awfulwoman.com/notes/x/",
            "post-type": "note",
            "post-status": "published",
            "channel": ["main"],
        }
    )
    assert out == '---\ndate: "2026-10-14T19:10:00+02:00"\ntags:\n  - indieweb\n---\n\nHello\n'


def test_bookmark_keys_match_site_layouts():
    out = render(
        {
            "published": "2026-10-14T19:12:00+02:00",
            "name": "Internet-Week Clock",
            "bookmark-of": "https://www.hackaday.io/project/1",
            "basedomain": "hackaday.io",
            "content": {"text": "Ridiculous"},
        }
    )
    fm, body = split(out)
    assert fm["title"] == "Internet-Week Clock"
    assert fm["bookmark_of"] == "https://www.hackaday.io/project/1"
    assert fm["basedomain"] == "hackaday.io"
    assert "bookmarkOf" not in fm and "publishDate" not in fm and "uid" not in fm
    assert body.strip() == "Ridiculous"


def test_key_order():
    out = render(
        {
            "published": "2026-10-14T19:12:00+02:00",
            "updated": "2026-10-15T08:00:00+02:00",
            "name": "T",
            "category": ["a"],
            "like-of": "https://x.example/1",
            "post-status": "draft",
            "syndication": ["https://m.example/1"],
            "zzz": 1,
            "aaa": 2,
        }
    )
    keys = list(split(out)[0])
    assert keys == [
        "title",
        "date",
        "lastmod",
        "draft",
        "like_of",
        "tags",
        "syndication",
        "aaa",
        "zzz",
    ]


def test_draft_flag_only_when_draft():
    assert (
        split(render({"published": "2026-10-14T19:12:00+02:00", "post-status": "draft"}))[0][
            "draft"
        ]
        is True
    )
    assert (
        "draft"
        not in split(
            render({"published": "2026-10-14T19:12:00+02:00", "post-status": "published"})
        )[0]
    )


def test_deleted_becomes_expiry_date():
    fm = split(
        render({"published": "2026-10-14T19:12:00+02:00", "deleted": "2026-10-20T00:00:00+02:00"})
    )[0]
    assert fm["expiryDate"] == "2026-10-20T00:00:00+02:00"


def test_checkin_place_keys_and_title():
    out = render(
        {
            "published": "2026-10-14T19:12:00+02:00",
            "checkin": {
                "type": "card",
                "name": "Gesundbrunnen, Berlin",
                "latitude": "52.54",
                "longitude": "13.38",
            },
        }
    )
    fm = split(out)[0]
    assert fm["title"] == "Checked in at Gesundbrunnen, Berlin"
    assert fm["place_name"] == "Gesundbrunnen, Berlin"
    assert fm["place_geo"] == {"lat": 52.54, "lng": 13.38}


def test_checkin_keeps_explicit_name_as_title():
    out = render(
        {
            "published": "2026-10-14T19:12:00+02:00",
            "name": "My title",
            "checkin": {"type": "card", "name": "Cafe"},
        }
    )
    assert split(out)[0]["title"] == "My title"


def test_location_geo_becomes_place_geo():
    fm = split(
        render(
            {
                "published": "2026-10-14T19:12:00+02:00",
                "location": {"type": "geo", "latitude": "1.5", "longitude": "2.5"},
            }
        )
    )[0]
    assert fm["place_geo"] == {"lat": 1.5, "lng": 2.5}


def test_photo_to_images_bundle_relative():
    fm = split(
        render(
            {
                "published": "2026-10-14T19:12:00+02:00",
                "photo": [
                    {"url": "/content/photos/2026/a/boat.jpg", "alt": "A boat"},
                    {"url": "/x/b.jpg"},
                ],
            }
        )
    )[0]
    assert fm["images"] == [{"file": "boat.jpg", "alt": "A boat"}, {"file": "b.jpg"}]


def test_unknown_keys_underscored():
    fm = split(
        render(
            {
                "published": "2026-10-14T19:12:00+02:00",
                "some-key": "v",
                "mp-slug": "no",
                "post-type": "note",
            }
        )
    )[0]
    assert fm["some_key"] == "v"
    assert "mp-slug" not in fm and "mp_slug" not in fm and "post_type" not in fm


def test_output_ends_with_single_newline_and_no_trailing_space():
    out = render({"published": "2026-10-14T19:12:00+02:00", "content": {"text": "Hi  \n"}})
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert all(line == line.rstrip() for line in out.splitlines()[:-1] if line)


def test_body_html_fallback():
    out = render({"published": "2026-10-14T19:12:00+02:00", "content": {"html": "<p>x</p>"}})
    assert split(out)[1].strip() == "<p>x</p>"


def test_custom_mapping():
    out = render(
        {"published": "2026-10-14T19:12:00+02:00", "name": "T"},
        mapping={"name": "headline", "published": "date"},
    )
    assert "headline" in split(out)[0]


def test_photo_extras_such_as_dimensions_survive():
    fm = split(
        render(
            {
                "published": "2026-10-14T19:12:00+02:00",
                "photo": [{"url": "pic.jpg", "alt": "A pic", "width": 4608, "height": 2592}],
            }
        )
    )[0]
    assert fm["images"] == [{"file": "pic.jpg", "alt": "A pic", "width": 4608, "height": 2592}]


def test_the_template_never_invents_a_basedomain():
    out = render(
        {"published": "2026-10-14T19:12:00+02:00", "bookmark-of": "https://www.theatlantic.com/x"}
    )
    assert "basedomain" not in split(out)[0]


def test_front_matter_extras_are_written_back_as_found():
    out = render(
        {
            "published": "2026-10-14T19:12:00+02:00",
            "front-matter": {"basedomain": "www.theatlantic.com", "strapline": "kept"},
        }
    )
    fm = split(out)[0]
    assert fm["basedomain"] == "www.theatlantic.com" and fm["strapline"] == "kept"
