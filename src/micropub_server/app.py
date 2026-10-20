"""Starlette application factory."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from .auth.client import Resolver, system_resolver
from .auth.service import AuthService
from .config import Config, PostType, Settings, SiteOverlay, Template
from .db import Database
from .stores import Store, StoreError
from .target_meta import TargetMeta, fetch_target_meta

log = logging.getLogger("micropub_server")


@dataclass
class AppState:
    config: Config
    settings: Settings
    db: Database
    auth: AuthService
    store: Store
    resolver: Resolver = system_resolver
    target_fetcher: Callable[[str, Resolver], Awaitable[TargetMeta]] = fetch_target_meta
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    store_problem: str | None = None
    site: SiteOverlay | None = None
    targets: list[Any] = field(default_factory=list)  # syndication targets
    site_config_status: str = "disabled"
    jinja: Environment = field(init=False)

    def __post_init__(self) -> None:
        folder = str(resources.files("micropub_server") / "templates")
        self.jinja = Environment(
            loader=FileSystemLoader(folder), autoescape=select_autoescape(["html"])
        )
        self.jinja.globals["app_name"] = self.config.application.name

    @property
    def base(self) -> str:
        return self.settings.base_url.rstrip("/") + "/"

    @property
    def me(self) -> str:
        return self.auth.me

    @property
    def post_types(self) -> dict[str, PostType]:
        """The site's list of post types if it provides one, else the local config's."""
        return (
            self.site.post_types if self.site and self.site.post_types else None
        ) or self.config.post_types

    @property
    def template(self) -> Template:
        base = self.config.template
        if not self.site:
            return base
        return Template(
            type=base.type,
            front_matter=base.front_matter,
            mapping=self.site.mapping if self.site.mapping is not None else base.mapping,
            omit=self.site.omit if self.site.omit is not None else base.omit,
        )

    async def refresh_site_config(self) -> None:
        """Read the site's own data/micropub.yaml. Keep working if it is missing or broken."""
        path = self.config.site_config_path
        if not path:
            self.site, self.site_config_status = None, "disabled"
            return
        try:
            raw = await self.store.read(path)
            data = YAML(typ="safe").load(raw) or {}
            self.site = SiteOverlay.model_validate(data)
            self.site_config_status = "ok"
        except StoreError as exc:
            if exc.status != 404 and self.site:  # transient: keep the last good copy
                self.site_config_status = "stale"
                log.warning("could not refresh %s, keeping the last good copy: %s", path, exc)
            else:
                self.site, self.site_config_status = None, "fallback"
                log.warning("site config %s unavailable, using the local config: %s", path, exc)
        except (YAMLError, ValidationError) as exc:
            self.site, self.site_config_status = None, "fallback"
            log.warning("site config %s is invalid, using the local config: %s", path, exc)


def create_app(state: AppState) -> Starlette:
    from . import (
        routes_auth,
        routes_media,
        routes_micropub,
        routes_system,
        routes_ui,
        routes_ui_extra,
        routes_webhook,
    )
    from .logs import AccessLogMiddleware
    from .security import SecurityMiddleware

    routes: list[Route] = [
        *routes_system.routes(),
        *routes_auth.routes(),
        *routes_micropub.routes(),
        *routes_media.routes(),
        *routes_ui.routes(),
        *routes_ui_extra.routes(),
        *routes_webhook.routes(),
    ]
    app = Starlette(
        routes=routes,
        middleware=[Middleware(SecurityMiddleware), Middleware(AccessLogMiddleware)],
        lifespan=routes_system.lifespan,
    )
    app.state.s = state
    return app
