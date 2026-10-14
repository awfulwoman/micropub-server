"""Build the application state from settings, with clear startup errors."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from .app import AppState
from .auth.service import AuthService
from .config import Config, Settings, load_config
from .db import Database
from .stores import FileSystemStore, Store
from .stores.github import GitHubStore


class ConfigError(Exception):
    pass


def _build_store(config: Config, settings: Settings) -> Store:
    store = config.store
    if store.type == "filesystem":
        if not store.directory:
            raise ConfigError("store.directory is required for the filesystem store")
        return FileSystemStore(store.directory)
    if not (store.owner and store.repo):
        raise ConfigError("store.owner and store.repo are required for the GitHub store")
    if not settings.github_token:
        raise ConfigError("GITHUB_TOKEN is required for the GitHub store")
    return GitHubStore(
        owner=store.owner,
        repo=store.repo,
        branch=store.branch,
        token=settings.github_token,
        base_url=store.base_url,
    )


def build_state(settings: Settings) -> AppState:
    problems = []
    parts = urlsplit(settings.publication_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        problems.append("PUBLICATION_URL must be a full URL such as https://example.com/")
    if len(settings.secret) < 16:
        problems.append("SECRET must be set to at least 16 random characters")
    if not (settings.password_hash or settings.password_secret):
        problems.append(
            "PASSWORD_HASH is required (create one with: micropub-server hash-password)"
        )
    if problems:
        raise ConfigError("; ".join(problems))

    path = Path(settings.config_file)
    try:
        config = load_config(path)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from exc
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc

    store = _build_store(config, settings)
    db = Database(Path(settings.data_dir) / "micropub.db")
    auth = AuthService(
        db, me=settings.publication_url, token_ttl_days=config.application.token_ttl_days
    )
    return AppState(config=config, settings=settings, db=db, auth=auth, store=store)
