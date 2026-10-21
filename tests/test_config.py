import pytest

from micropub_server.config import Config, load_config

YAML = """
application:
  time_zone: Europe/Berlin
publication:
  slug_separator: "-"
store:
  type: filesystem
  directory: /tmp/site
post_types:
  note:
    name: Note
    post:
      path: content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}.md
      url: notes/{yyyy}/{MM}/{dd}/{HH}{mm}/
    properties: [content, category]
    required_properties: [content]
  bookmark:
    name: Bookmark
    discovery: bookmark-of
    post:
      path: content/bookmarks/{slug}.md
"""


def test_load_config(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(YAML)
    cfg = load_config(f)
    assert isinstance(cfg, Config)
    assert cfg.application.time_zone == "Europe/Berlin"
    assert cfg.post_types["note"].post.path.startswith("content/notes/")
    assert cfg.post_types["bookmark"].discovery == "bookmark-of"
    assert cfg.post_types["bookmark"].post.url is None
    assert cfg.store.type == "filesystem"


def test_defaults(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("store:\n  type: filesystem\n  directory: /tmp/x\n")
    cfg = load_config(f)
    assert cfg.application.time_zone == "UTC"
    assert cfg.application.token_ttl_days == 90
    assert cfg.publication.slug_separator == "-"
    assert cfg.template.mapping["name"] == "title"
    assert "uid" in cfg.template.omit


def test_sharing_targets_are_unticked_by_default(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "store:\n  type: filesystem\n  directory: /tmp/x\n"
        "syndication:\n  mastodon:\n    url: https://m.example\n    user: me\n"
        "  bluesky:\n    handle: me.bsky.social\n"
    )
    cfg = load_config(f)
    assert cfg.syndication.mastodon.checked is False
    assert cfg.syndication.bluesky.checked is False


def test_unknown_key_rejected(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("store:\n  type: filesystem\n  directory: /x\nbogus: 1\n")
    with pytest.raises(ValueError, match="bogus"):
        load_config(f)


def test_bad_time_zone_rejected(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "application:\n  time_zone: Mars/Base\nstore:\n  type: filesystem\n  directory: /x\n"
    )
    with pytest.raises(ValueError, match="time_zone"):
        load_config(f)
