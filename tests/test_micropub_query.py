from urllib.parse import quote


def make(client, bearer, **data):
    r = client.post("/micropub", data=data, headers=bearer)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def get(client, bearer, **params):
    return client.get("/micropub", params=params, headers=bearer)


def test_queries_need_a_token(client):
    assert client.get("/micropub", params={"q": "config"}).status_code == 401


def test_q_required_and_unknown(client, bearer):
    r = client.get("/micropub", headers=bearer)
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    r = get(client, bearer, q="nonsense")
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_config(client, bearer):
    body = get(client, bearer, q="config").json()
    assert body["media-endpoint"] == "https://indie.awfulwoman.com/media"
    assert body["syndicate-to"] == []
    assert {c["uid"] for c in body["channels"]} == {"main", "pages"}
    types = {t["type"]: t for t in body["post-types"]}
    assert types["note"]["name"] == "Note"
    assert types["note"]["required-properties"] == ["content"]
    assert "config" in body["q"] and "source" in body["q"] and "syndicate-to" in body["q"]


def test_single_key_queries(client, bearer):
    assert get(client, bearer, q="syndicate-to").json() == {"syndicate-to": []}
    assert get(client, bearer, q="media-endpoint").json() == {
        "media-endpoint": "https://indie.awfulwoman.com/media"
    }
    assert get(client, bearer, q="channel").json()["channels"][0] == {"uid": "main", "name": "Main"}
    assert [t["type"] for t in get(client, bearer, q="post-types").json()["post-types"]][
        0
    ] == "note"


def test_category_query_with_filter(client, bearer, state):
    state.config.publication.categories = ["indieweb", "python", "hugo"]
    assert get(client, bearer, q="category").json() == {
        "categories": ["indieweb", "python", "hugo"]
    }
    assert get(client, bearer, q="category", filter="py").json() == {"categories": ["python"]}
    assert get(client, bearer, q="category", limit="2").json() == {
        "categories": ["indieweb", "python"]
    }


def test_source_single_post_as_mf2(client, bearer):
    url = make(client, bearer, content="Hello", **{"category[]": ["a", "b"]})
    body = get(client, bearer, q="source", url=url).json()
    assert body["type"] == ["h-entry"]
    assert body["properties"]["category"] == ["a", "b"]
    assert body["properties"]["content"] == [{"html": "<p>Hello</p>\n", "value": "Hello"}]
    assert body["properties"]["published"] == ["2026-10-14T19:10:47+02:00"]


def test_source_property_selection_drops_type(client, bearer):
    url = make(client, bearer, content="Hello", name="Title")
    body = get(client, bearer, q="source", url=url, **{"properties[]": ["name"]}).json()
    assert body == {"properties": {"name": ["Title"]}}


def test_source_unknown_url_is_400(client, bearer):
    r = get(client, bearer, q="source", url="https://awfulwoman.com/notes/nope/")
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_source_unknown_uid_is_404(client, bearer):
    assert get(client, bearer, q="source", uid="nope").status_code == 404


def test_source_by_uid(client, bearer, state):
    url = make(client, bearer, content="Hello")
    uid = state.db.query("SELECT uid FROM posts")[0][0]
    assert get(client, bearer, q="source", uid=uid).json()["properties"]["url"] == [url]


def test_source_list_newest_first_with_paging(client, bearer):
    for i in range(1, 4):
        make(client, bearer, content=f"post {i}", published=f"2026-10-0{i}T10:00:00+02:00")
    first = get(client, bearer, q="source", limit="2").json()
    texts = [it["properties"]["content"][0]["value"] for it in first["items"]]
    assert texts == ["post 3", "post 2"]
    assert "after" in first["paging"] and "before" not in first["paging"]
    second = get(client, bearer, q="source", limit="2", after=first["paging"]["after"]).json()
    assert [it["properties"]["content"][0]["value"] for it in second["items"]] == ["post 1"]
    assert "before" in second["paging"] and "after" not in second["paging"]


def test_source_list_filters(client, bearer):
    make(client, bearer, content="about cats", **{"category[]": ["pets"]})
    make(client, bearer, content="about dogs", published="2026-10-13T10:00:00+02:00")
    by_cat = get(client, bearer, q="source", category="pets").json()["items"]
    assert len(by_cat) == 1 and by_cat[0]["properties"]["category"] == ["pets"]
    by_text = get(client, bearer, q="source", filter="dogs").json()["items"]
    assert len(by_text) == 1
    by_type = get(client, bearer, q="source", **{"post-type": "bookmark"}).json()["items"]
    assert by_type == []


def test_source_list_hides_deleted_posts(client, bearer, state):
    make(client, bearer, content="gone")
    state.db.execute("UPDATE posts SET deleted = '2026-10-15T00:00:00+02:00'")
    assert get(client, bearer, q="source").json()["items"] == []


def test_urlencoded_url_param(client, bearer):
    url = make(client, bearer, content="Hello")
    assert (
        client.get(f"/micropub?q=source&url={quote(url, safe='')}", headers=bearer).status_code
        == 200
    )


def test_source_list_filters_by_status(client, bearer):
    make(client, bearer, content="live", published="2026-10-13T10:00:00+02:00")
    make(client, bearer, content="wip", **{"post-status": "draft"})
    drafts = get(client, bearer, q="source", **{"post-status": "draft"}).json()["items"]
    assert [it["properties"]["content"][0]["value"] for it in drafts] == ["wip"]
    live = get(client, bearer, q="source", **{"post-status": "published"}).json()["items"]
    assert [it["properties"]["content"][0]["value"] for it in live] == ["live"]
