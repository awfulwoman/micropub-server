import pytest

from micropub_server.stores import Put, StoreError

SITE = """\
version: 1
mapping:
  name: headline
  published: date
omit: [uid, slug, url, channel]
post_types:
  note:
    name: Note from site
    post:
      path: content/n/{yyyy}/{slug}.md
      url: n/{slug}/
"""


@pytest.fixture
def with_site_config(state, site):
    state.config.site_config_path = "data/micropub.yaml"
    (site / "data").mkdir()
    (site / "data" / "micropub.yaml").write_text(SITE)
    return state


async def test_site_file_overrides_post_types_and_mapping(with_site_config):
    s = with_site_config
    await s.refresh_site_config()
    assert s.site_config_status == "ok"
    assert s.post_types["note"].name == "Note from site"
    assert list(s.post_types) == ["note"]  # the site's list replaces the local one
    assert s.template.mapping == {"name": "headline", "published": "date"}
    assert "channel" in s.template.omit


async def test_posts_use_the_site_shape(client, with_site_config, bearer, site):
    await with_site_config.refresh_site_config()
    r = client.post("/micropub", data={"name": "Hello"}, headers=bearer)
    assert r.status_code == 202, r.text
    assert (site / "content/n/2026/hello.md").exists()
    assert "headline: Hello" in (site / "content/n/2026/hello.md").read_text()


async def test_disabled_when_no_path(state):
    await state.refresh_site_config()
    assert state.site_config_status == "disabled"
    assert "note" in state.post_types and state.template.mapping["name"] == "title"


async def test_missing_file_falls_back_to_local_config(state):
    state.config.site_config_path = "data/micropub.yaml"
    await state.refresh_site_config()
    assert state.site_config_status == "fallback"
    assert "article" in state.post_types


async def test_invalid_file_falls_back(with_site_config, site):
    (site / "data" / "micropub.yaml").write_text("post_types: [this, is, wrong]\n")
    await with_site_config.refresh_site_config()
    assert with_site_config.site_config_status == "fallback"
    assert "article" in with_site_config.post_types


async def test_unparseable_yaml_falls_back(with_site_config, site):
    (site / "data" / "micropub.yaml").write_text("a: [unclosed\n")
    await with_site_config.refresh_site_config()
    assert with_site_config.site_config_status == "fallback"


async def test_unknown_keys_are_rejected(with_site_config, site):
    (site / "data" / "micropub.yaml").write_text(SITE + "bogus: 1\n")
    await with_site_config.refresh_site_config()
    assert with_site_config.site_config_status == "fallback"


async def test_transient_store_error_keeps_last_good_config(with_site_config, monkeypatch):
    s = with_site_config
    await s.refresh_site_config()
    assert s.site_config_status == "ok"

    async def down(path):
        raise StoreError("GitHub is down", 503)

    monkeypatch.setattr(s.store, "read", down)
    await s.refresh_site_config()
    assert s.site_config_status == "stale"
    assert s.post_types["note"].name == "Note from site"


async def test_file_removed_after_loading_falls_back(with_site_config, site):
    s = with_site_config
    await s.refresh_site_config()
    (site / "data" / "micropub.yaml").unlink()
    await s.refresh_site_config()
    assert s.site_config_status == "fallback" and "article" in s.post_types


async def test_health_reports_site_config(client, with_site_config):
    await with_site_config.refresh_site_config()
    assert client.get("/health").json()["site_config"] == "ok"


def test_health_omits_site_config_when_disabled(client):
    assert "site_config" not in client.get("/health").json()


async def test_config_query_shows_site_post_types(client, with_site_config, bearer):
    await with_site_config.refresh_site_config()
    types = client.get("/micropub", params={"q": "post-types"}, headers=bearer).json()["post-types"]
    assert [t["type"] for t in types] == ["note"]


async def test_store_put_then_refresh_roundtrip(with_site_config, site):
    # the same path the real flow uses: the site repo changes, the server re-reads it
    s = with_site_config
    await s.refresh_site_config()
    await s.store.commit(
        [Put("data/micropub.yaml", SITE.replace("Note from site", "Changed").encode())], "m"
    )
    await s.refresh_site_config()
    assert s.post_types["note"].name == "Changed"
