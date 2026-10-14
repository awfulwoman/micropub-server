"""Starlette application factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.routing import Route

from .auth.client import Resolver, system_resolver
from .auth.service import AuthService
from .config import Config, Settings
from .db import Database
from .stores import Store


@dataclass
class AppState:
    config: Config
    settings: Settings
    db: Database
    auth: AuthService
    store: Store
    resolver: Resolver = system_resolver
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


def create_app(state: AppState) -> Starlette:
    from . import routes_auth

    routes: list[Route] = [*routes_auth.routes()]
    app = Starlette(routes=routes)
    app.state.s = state
    return app
