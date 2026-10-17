"""Configuration: YAML structure plus environment secrets (spec 09)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from ruamel.yaml import YAML

DEFAULT_MAPPING = {
    "name": "title",
    "published": "date",
    "updated": "lastmod",
    "summary": "description",
    "category": "tags",
    "bookmark-of": "bookmark_of",
    "like-of": "like_of",
    "repost-of": "repost_of",
    "in-reply-to": "in_reply_to",
    "quotation-of": "quote_of",
    "photo": "images",
    "video": "videos",
}
DEFAULT_OMIT = ["uid", "slug", "url", "publishDate", "channel"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Application(_Model):
    name: str = "Micropub server"
    time_zone: str = "UTC"
    locale: str = "en-GB"
    token_ttl_days: int = 90
    cache_ttl_seconds: int = 604_800
    # Largest request body, for the pages and endpoints that take uploads, and for everything else.
    max_body_bytes: int = 26_214_400
    max_form_bytes: int = 65_536
    max_webhook_bytes: int = 1_048_576

    @field_validator("time_zone")
    @classmethod
    def _check_zone(cls, value: str) -> str:
        if value == "client":
            return value
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"time_zone: unknown zone {value!r}") from exc
        return value


class Publication(_Model):
    locale: str = "en-GB"
    slug_separator: str = "-"
    enrich_post_data: bool = False
    categories: list[str] | str = Field(default_factory=list)
    profile: dict[str, str] = Field(default_factory=dict)
    commit_message: str = "{action} {post_type} {file_type}"


class Store(_Model):
    type: Literal["github", "filesystem"] = "github"
    owner: str | None = None
    repo: str | None = None
    branch: str = "main"
    directory: str | None = None
    base_url: str = "https://api.github.com"


class MediaImage(_Model):
    max_width: int = 2560
    max_height: int = 2560
    jpeg_quality: int = 85


class Media(_Model):
    max_bytes: int = 20_971_520
    image: MediaImage = Field(default_factory=MediaImage)
    # Where uploads go (a Hugo static/ path, so they are served from the site root), and
    # their public URL path. The URL defaults to the path without its leading "static/".
    upload_path: str = "static/media/{yyyy}/{MM}/{md5}.{ext}"
    upload_url: str | None = None

    @property
    def url_template(self) -> str:
        return self.upload_url or self.upload_path.removeprefix("static/")


class Template(_Model):
    type: Literal["hugo"] = "hugo"
    front_matter: Literal["yaml"] = "yaml"
    mapping: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_MAPPING))
    omit: list[str] = Field(default_factory=lambda: list(DEFAULT_OMIT))


class Paths(_Model):
    path: str
    url: str | None = None


class PostType(_Model):
    name: str
    discovery: str | None = None
    discovery_first: bool = False
    channel: str | None = None
    # How the importer derives {slug} for existing files that have no slug in their front matter:
    # "path" uses the file or folder name; "title" slugifies the title, as Hugo's :slug permalink does.
    import_slug: Literal["path", "title"] = "path"
    post: Paths
    media: Paths | None = None
    properties: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)


class Channel(_Model):
    name: str


class MastodonConfig(_Model):
    url: str
    user: str
    checked: bool = True
    character_limit: int = 500
    include_permalink: bool = True
    visibility: Literal["public", "unlisted", "private"] = "public"
    # Label shown in clients. Any server with the Mastodon client API works (GoToSocial, ...).
    service_name: str = "Mastodon"


class BlueskyConfig(_Model):
    handle: str
    service: str = "https://bsky.social"
    checked: bool = False
    include_permalink: bool = True


class Syndication(_Model):
    mastodon: MastodonConfig | None = None
    bluesky: BlueskyConfig | None = None
    # Only syndicate once the post is live on the site (it is fetched first).
    require_live: bool = True


class Deploy(_Model):
    """Which GitHub workflow run means "the site is live" (spec 08). Needs WEBHOOK_SECRET."""

    # The workflow's name. Unset: a successful run of any workflow on the store's branch counts.
    workflow: str | None = None


class SiteOverlay(_Model):
    """The site-owned file (data/micropub.yaml): the shape of its own content (spec 06)."""

    version: int = 1
    mapping: dict[str, str] | None = None
    omit: list[str] | None = None
    post_types: dict[str, PostType] | None = None


class Config(_Model):
    application: Application = Field(default_factory=Application)
    publication: Publication = Field(default_factory=Publication)
    store: Store
    media_store: Store | None = None
    media: Media = Field(default_factory=Media)
    template: Template = Field(default_factory=Template)
    post_types: dict[str, PostType] = Field(default_factory=dict)
    channels: dict[str, Channel] = Field(default_factory=dict)
    syndication: Syndication = Field(default_factory=Syndication)
    deploy: Deploy = Field(default_factory=Deploy)
    site_config_path: str | None = None


def load_config(path: str | Path) -> Config:
    data = YAML(typ="safe").load(Path(path).read_text()) or {}
    return Config.model_validate(data)


class Settings(BaseSettings):
    """Environment variables (secrets and per-deployment values)."""

    model_config = SettingsConfigDict(extra="ignore")

    config_file: str = "/config/config.yaml"
    data_dir: str = "/data"
    base_url: str = "http://localhost:8000/"
    publication_url: str = "https://example.com/"
    secret: str = ""
    password_hash: str = ""
    password_secret: str = ""
    github_token: str = ""
    mastodon_access_token: str = ""
    bluesky_password: str = ""
    webhook_secret: str = ""
    port: int = 8000
    log_level: str = "info"
    app_version: str = "dev"
