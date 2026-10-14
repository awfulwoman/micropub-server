"""Command line: serve, hash-password, check, migrate."""

from __future__ import annotations

import argparse
import getpass
import sys

from .auth.passwords import hash_password
from .bootstrap import ConfigError, build_state
from .config import Settings


def _hash_password(_args) -> int:
    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Again: "):
            print("The passwords do not match", file=sys.stderr)
            return 1
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        print("The password is empty", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def _state():
    return build_state(Settings())


def _check(_args) -> int:
    import asyncio

    try:
        state = _state()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    problem = asyncio.run(state.store.check())
    if problem:
        print(f"store problem: {problem}", file=sys.stderr)
        return 1
    print(
        f"ok: {state.store.name} store ({state.store.uid}), {len(state.config.post_types)} post types"
    )
    return 0


def _migrate(_args) -> int:
    try:
        _state()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    print("ok: database is up to date")
    return 0


def _serve(_args) -> int:
    import uvicorn

    from .app import create_app
    from .logs import configure_logging

    settings = Settings()
    configure_logging(settings.log_level)
    try:
        state = build_state(settings)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    uvicorn.run(
        create_app(state),
        host="0.0.0.0",  # noqa: S104 - runs in a container behind Traefik
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
        access_log=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="micropub-server")
    sub = parser.add_subparsers(dest="command")
    for name, func, help_ in [
        ("serve", _serve, "run the server (default)"),
        ("hash-password", _hash_password, "print an argon2id hash for PASSWORD_HASH"),
        ("check", _check, "validate config and store access, then exit"),
        ("migrate", _migrate, "apply database migrations, then exit"),
    ]:
        sub.add_parser(name, help=help_).set_defaults(func=func)
    args = parser.parse_args(argv)
    return (args.func if getattr(args, "func", None) else _serve)(args)


if __name__ == "__main__":
    raise SystemExit(main())
