"""The files in docs/deploy are meant to be copied into other repos, so they must stay valid."""

from pathlib import Path

from ruamel.yaml import YAML

from micropub_server.config import SiteOverlay, load_config

DEPLOY = Path(__file__).resolve().parent.parent / "docs" / "deploy"
DEV = Path(__file__).resolve().parent.parent / "dev" / "config.yaml"


def test_the_site_file_is_a_valid_overlay():
    overlay = SiteOverlay.model_validate(
        YAML(typ="safe").load((DEPLOY / "site-micropub.yaml").read_text())
    )
    assert overlay.mapping["bookmark-of"] == "bookmark_of" and "channel" in overlay.omit
    assert {"note", "article", "bookmark", "photo", "checkin", "page"} <= set(overlay.post_types)
    assert overlay.post_types["article"].import_slug == "title"


def test_the_site_file_matches_the_dev_config_post_types():
    site = SiteOverlay.model_validate(
        YAML(typ="safe").load((DEPLOY / "site-micropub.yaml").read_text())
    )
    dev = load_config(DEV)
    assert {k: v.model_dump() for k, v in site.post_types.items()} == {
        k: v.model_dump() for k, v in dev.post_types.items()
    }


def test_the_production_config_loads():
    cfg = load_config(DEPLOY / "production-config.example.yaml")
    assert cfg.store.type == "github" and cfg.site_config_path == "data/micropub.yaml"
    assert cfg.syndication.mastodon.user == "awfulwoman" and cfg.syndication.bluesky is None
