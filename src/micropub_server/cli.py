"""Command line: serve, hash-password, check, migrate, token, import, backfill-targets, backfill-cards."""

from __future__ import annotations

import argparse
import getpass
import sys

from .auth.passwords import hash_password
from .backfill_cards import STEPS
from .bootstrap import ConfigError, build_state
from .config import Settings
from .target_enrich import CARD_KINDS
from .target_meta import KINDS


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


def _token(args) -> int:
    from .auth.service import AuthError

    try:
        state = _state()
        minted = state.auth.mint_token(args.label, args.scope, args.days)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    except AuthError as exc:
        print(f"error: {exc.description}", file=sys.stderr)
        return 1
    print(minted["access_token"])
    return 0


def _import(args) -> int:
    import asyncio

    from .importer import import_posts
    from .stores import FileSystemStore

    try:
        state = _state()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    source = FileSystemStore(args.source) if args.source else state.store

    async def run():  # one loop: the store's HTTP client cannot outlive the loop it was used in
        await state.refresh_site_config()
        return await import_posts(state, source, dry_run=args.dry_run, only_type=args.type)

    report = asyncio.run(run())
    verb = "would import" if args.dry_run else "imported"
    print(
        f"{verb} {report.imported}, unchanged {report.unchanged}, skipped {report.skipped}, failed {report.failed}"
    )
    for name, count in sorted(report.by_type.items()):
        print(f"  {name}: {count}")
    for path, url in report.samples:
        print(f"  e.g. {path} -> {url}")
    for path, reason in report.problems:
        print(f"  problem: {path}: {reason}", file=sys.stderr)
    if args.check_urls and not args.dry_run:
        from .importer import check_urls

        bad = asyncio.run(check_urls(state))  # a fresh loop is fine: it uses its own clients
        print(f"checked the live site: {len(bad)} imported URLs are not served")
        for url, status in bad:
            print(f"  {status or 'error'} {url}", file=sys.stderr)
    return 0


def _backfill_targets(args) -> int:
    import asyncio

    from .target_enrich import backfill_targets

    try:
        state = _state()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    async def run():
        await state.refresh_site_config()
        return await backfill_targets(
            state, dry_run=args.dry_run, delay=args.delay, only_type=args.type
        )

    report = asyncio.run(run())
    verb = "would update" if args.dry_run else "updated"
    print(
        f"{verb} {report.updated}, already done {report.skipped}, "
        f"nothing found {report.nothing_found}, failed {report.failed}"
    )
    for url, props in report.changes:
        print(f"  {url}")
        for key, value in props.items():
            print(f"    {key}: {value[:80]!r}")
    return 1 if report.failed else 0


def _backfill_cards(args) -> int:
    from pathlib import Path

    from .backfill_cards import backfill_scraped
    from .config import MediaCard

    root = Path(args.checkout)
    if not (root / f"content/{args.type}s").is_dir():
        print(f"no content/{args.type}s in {root}", file=sys.stderr)
        return 1
    report = backfill_scraped(root, MediaCard(), dry_run=args.dry_run, kind=args.type)
    verb = "would convert" if args.dry_run else "converted"
    print(f"{verb} {report.converted} (text only {report.no_image}), skipped {report.skipped}")
    for path, props in report.changes:
        print(f"  {path}")
        for key, value in props.items():
            print(f"    {key}: {str(value)[:80]!r}")
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
        server_header=False,
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
    tok = sub.add_parser("token", help="print a new access token for a script, shown once")
    tok.add_argument("--scope", required=True, help='for example "update" (space-separated)')
    tok.add_argument("--label", required=True, help="a name for the app, shown on the apps page")
    tok.add_argument(
        "--days", type=int, help="how long it lasts (default: the configured token lifetime)"
    )
    tok.set_defaults(func=_token)
    imp = sub.add_parser("import", help="load existing posts from the site so they can be edited")
    imp.add_argument(
        "--source", help="a local checkout of the site repo (default: read through the store)"
    )
    imp.add_argument(
        "--dry-run", action="store_true", help="report what would be imported, change nothing"
    )
    imp.add_argument("--type", help="only this post type, e.g. bookmark")
    imp.add_argument(
        "--check-urls",
        action="store_true",
        help="afterwards, ask the live site for every imported URL",
    )
    imp.set_defaults(func=_import)
    back = sub.add_parser(
        "backfill-targets",
        help="fetch the title, author and embed of the page each existing like, quote and "
        "bookmark points at (run import first)",
    )
    back.add_argument(
        "--dry-run", action="store_true", help="show what would be written, change nothing"
    )
    back.add_argument(
        "--delay", type=float, default=1.0, help="seconds between fetches (default 1)"
    )
    back.add_argument("--type", choices=sorted(KINDS), help="only this post type, e.g. like")
    back.set_defaults(func=_backfill_targets)
    cards = sub.add_parser(
        "backfill-cards",
        help="add link cards (site, description, image) to the bookmarks or likes of a site checkout",
    )
    cards.add_argument(
        "--type", choices=sorted(CARD_KINDS), default="bookmark", help="bookmark (default) or like"
    )
    cards.add_argument("--checkout", required=True, help="a local checkout of the site repo")
    cards.add_argument(
        "--step",
        required=True,
        choices=STEPS,
        help="scraped: the old scraped/ cards, with no network",
    )
    cards.add_argument(
        "--dry-run", action="store_true", help="show what would be written, change nothing"
    )
    cards.set_defaults(func=_backfill_cards)
    args = parser.parse_args(argv)
    return (args.func if getattr(args, "func", None) else _serve)(args)


if __name__ == "__main__":
    raise SystemExit(main())
