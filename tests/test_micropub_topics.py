"""Topics (spec 15): worked out from tags, or set with mp-topic, written as `topics`."""

import re

import pytest
from ruamel.yaml import YAML

from .test_micropub_edit import NOTE, fm, full, make, update  # noqa: F401

TOPICS = """\
web:
  title: Web development
  tags: [web, html, css]
tech:
  title: Technology
  tags: [tech, apple]
lgbtq:
  title: LGBTQ
  tags: [trans, pride]
life:
  title: Life
  tags: [berlin, home]
"""


@pytest.fixture
def topics(state, site):
    state.config.topics_path = "data/topics.yaml"
    (site / "data").mkdir(exist_ok=True)
    (site / "data" / "topics.yaml").write_text(TOPICS)
    return state


async def test_topic_map_loads_with_the_site_config(topics):
    await topics.refresh_site_config()
    assert list(topics.topic_map) == ["web", "tech", "lgbtq", "life"]
    assert topics.topic_titles["lgbtq"] == "LGBTQ"


async def test_a_missing_or_broken_map_means_no_topics(topics, site):
    (site / "data" / "topics.yaml").write_text("a: [unclosed\n")
    await topics.refresh_site_config()
    assert topics.topic_map == {}
    (site / "data" / "topics.yaml").unlink()
    await topics.refresh_site_config()
    assert topics.topic_map == {}


async def test_topics_come_from_tags_on_create(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    make(client, full, content="x", **{"category[]": ["Trans", "pride", "css", "nothing"]})
    front, _ = fm(site, NOTE)
    assert front["topics"] == ["lgbtq", "web"]
    assert front["tags"] == ["Trans", "pride", "css", "nothing"]


async def test_no_matching_tags_means_no_topics_key(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    make(client, full, content="x", **{"category[]": ["nothing"]})
    assert "topics" not in fm(site, NOTE)[0]


async def test_mp_topic_overrides_the_map_and_drops_unknown(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    make(client, full, content="x", **{"category[]": ["css"], "mp-topic[]": ["life", "nope"]})
    assert fm(site, NOTE)[0]["topics"] == ["life"]


async def test_more_than_three_mp_topics_is_refused(client, full, topics):  # noqa: F811
    await topics.refresh_site_config()
    r = client.post(
        "/micropub",
        data={"content": "x", "mp-topic[]": ["web", "tech", "lgbtq", "life"]},
        headers=full,
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


async def test_changing_tags_works_topics_out_again(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    url = make(client, full, content="x", **{"category[]": ["css"]})
    update(client, full, url, replace={"category": ["berlin"]})
    assert fm(site, NOTE)[0]["topics"] == ["life"]


async def test_an_update_with_mp_topic_sets_them(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    url = make(client, full, content="x", **{"category[]": ["css"]})
    r = update(client, full, url, replace={"mp-topic": ["tech", "life"]})
    assert r.status_code == 200, r.text
    assert fm(site, NOTE)[0]["topics"] == ["tech", "life"]


async def test_an_update_of_other_things_keeps_topics(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    url = make(client, full, content="x", **{"category[]": ["css"], "mp-topic[]": ["life"]})
    update(client, full, url, replace={"content": ["y"]})
    assert fm(site, NOTE)[0]["topics"] == ["life"]


async def test_topics_added_to_the_file_outside_the_server_survive(client, full, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    url = make(client, full, content="x")
    path = site / NOTE
    path.write_text(path.read_text().replace("---\n", "---\ntopics:\n  - tech\n", 1))
    update(client, full, url, replace={"content": ["y"]})
    front = YAML(typ="safe").load(path.read_text().split("---\n")[1])
    assert front["topics"] == ["tech"]


async def test_q_source_returns_topic(client, full, topics):  # noqa: F811
    await topics.refresh_site_config()
    url = make(client, full, content="x", **{"category[]": ["css"]})
    r = client.get("/micropub", params={"q": "source", "url": url}, headers=full)
    assert r.json()["properties"]["topic"] == ["web"]


async def test_without_a_map_nothing_is_written(client, full, site, state):  # noqa: F811
    make(client, full, content="x", **{"category[]": ["css"], "mp-topic[]": ["web"]})
    assert "topics" not in fm(site, NOTE)[0]


# --- web UI ------------------------------------------------------------------------
from .test_ui_edit import edit as ui_edit  # noqa: E402
from .test_ui_edit import make as ui_make  # noqa: E402
from .test_ui_edit import ui  # noqa: E402, F401


async def test_the_form_offers_topic_checkboxes(ui, topics):  # noqa: F811
    await topics.refresh_site_config()
    html = ui.get("/posts/new/note").text
    assert 'name="topic" value="lgbtq"' in html and "Web development" in html


async def test_no_boxes_ticked_means_topics_from_tags(ui, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    ui_make(ui, content="x", category="css")
    assert fm(site, NOTE)[0]["topics"] == ["web"]


async def test_ticked_boxes_are_the_topics(ui, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    ui_make(ui, content="x", category="css", topic=["life", "tech"])
    assert fm(site, NOTE)[0]["topics"] == ["life", "tech"]


async def test_edit_form_ticks_the_current_topics_and_saves_changes(ui, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    post = ui_make(ui, content="x", category="css", topic=["life"])
    html = ui.get(f"{post}/edit").text
    assert re.search(r'value="life"\s+checked', html)
    assert not re.search(r'value="web"\s+checked', html)
    ui_edit(ui, post, {"content": "x", "category": "css", "topic": ["tech"]})
    assert fm(site, NOTE)[0]["topics"] == ["tech"]


async def test_unticking_every_box_removes_the_topics(ui, site, topics):  # noqa: F811
    await topics.refresh_site_config()
    post = ui_make(ui, content="x", category="nothing", topic=["life"])
    ui_edit(ui, post, {"content": "x", "category": "nothing"})
    assert "topics" not in fm(site, NOTE)[0]
