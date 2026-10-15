from ruamel.yaml import YAML

from .conftest import jpeg_bytes


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1]), text.split("---\n", 2)[2]


def create(client, bearer, **kw):
    r = client.post("/micropub", headers=bearer, **kw)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def test_like(client, bearer, site):
    url = create(client, bearer, data={"like-of": "https://example.com/post"})
    assert url == "https://awfulwoman.com/likes/2026/10/14/1910/"
    front, body = fm(site, "content/likes/2026/10/14/1910.md")
    assert front["like_of"] == "https://example.com/post" and body.strip() == ""


def test_reply(client, bearer, site):
    create(
        client, bearer, data={"in-reply-to": "https://example.com/post", "content": "Loving this!"}
    )
    front, body = fm(site, "content/replies/2026/10/14/1910.md")
    assert front["in_reply_to"] == "https://example.com/post" and "Loving this!" in body


def test_repost_with_commentary(client, bearer, site):
    create(
        client, bearer, data={"repost-of": "https://example.com/post", "content": "Worth reading"}
    )
    front, body = fm(site, "content/reposts/2026/10/14/1910.md")
    assert front["repost_of"] == "https://example.com/post" and "Worth reading" in body


def test_quote(client, bearer, site):
    create(
        client,
        bearer,
        data={"quotation-of": "https://example.com/post", "content": "A fine sentence."},
    )
    front, body = fm(site, "content/quotes/2026/10/14/1910.md")
    assert front["quote_of"] == "https://example.com/post" and "A fine sentence." in body


def test_page_goes_to_the_site_root(client, bearer, site):
    url = create(
        client,
        bearer,
        data={"name": "Uses", "content": "My kit.", "mp-channel": "pages"},
    )
    assert url == "https://awfulwoman.com/uses/"
    front, body = fm(site, "content/uses.md")
    assert front["title"] == "Uses" and "My kit." in body


def test_a_titled_post_without_the_pages_channel_is_an_article_not_a_page(client, bearer, site):
    url = create(client, bearer, data={"name": "Uses", "content": "My kit."})
    assert url == "https://awfulwoman.com/posts/uses/"
    assert not (site / "content/uses.md").exists()


CHECKIN = {
    "type": ["h-entry"],
    "properties": {
        "published": ["2026-10-14T18:30:00+02:00"],
        "content": ["Back in Berlin!"],
        "syndication": ["https://www.swarmapp.com/c/abc"],
        "checkin": [
            {
                "type": ["h-card"],
                "properties": {
                    "name": ["Gesundbrunnen (Nordkreuz), Berlin"],
                    "url": ["https://foursquare.com/v/xyz"],
                    "latitude": ["52.548"],
                    "longitude": ["13.388"],
                },
            }
        ],
    },
}


def test_checkin_from_ownyourswarm(client, bearer, site):
    url = create(client, bearer, json=CHECKIN)
    assert url == "https://awfulwoman.com/checkins/2026/10/14/1830/"
    front, body = fm(site, "content/checkins/2026/10/14/1830/index.md")
    assert front["title"] == "Checked in at Gesundbrunnen (Nordkreuz), Berlin"
    assert front["place_name"] == "Gesundbrunnen (Nordkreuz), Berlin"
    assert front["place_geo"] == {"lat": 52.548, "lng": 13.388}
    assert front["syndication"] == ["https://www.swarmapp.com/c/abc"]
    assert "Back in Berlin!" in body


def test_checkin_with_a_photo_stays_a_checkin(client, bearer, site):
    body = {
        **CHECKIN,
        "properties": {**CHECKIN["properties"], "photo": ["https://cdn.example/photo.jpg"]},
    }
    create(client, bearer, json=body)
    front, _ = fm(site, "content/checkins/2026/10/14/1830/index.md")
    assert front["images"] == [{"file": "photo.jpg"}]


def test_checkin_with_uploaded_photo_goes_in_its_bundle(client, bearer, site):
    r = client.post(
        "/micropub",
        data={"content": "x", "checkin": "https://example.com/place"},
        files={"photo": ("pic.jpg", jpeg_bytes(), "image/jpeg")},
        headers=bearer,
    )
    assert r.status_code == 202
    assert any((site / "content/checkins/2026/10/14/1910").glob("pic.jpg"))


def test_photo_post_json_with_alt(client, bearer, site):
    body = {
        "type": ["h-entry"],
        "properties": {
            "photo": [{"value": "https://cdn.example/a.jpg", "alt": "Boats"}],
            "content": ["Boats!"],
        },
    }
    create(client, bearer, json=body)
    front, _ = fm(site, "content/photos/2026/10/14/1910/index.md")
    assert front["images"] == [{"file": "a.jpg", "alt": "Boats"}]


def test_geo_location_on_a_note(client, bearer, site):
    create(client, bearer, data={"content": "here", "location": "geo:52.5,13.4"})
    front, _ = fm(site, "content/notes/2026/10/14/1910.md")
    assert front["place_geo"] == {"lat": 52.5, "lng": 13.4}
