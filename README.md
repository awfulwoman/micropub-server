# micropub-server

A small Python service that lets IndieWeb clients publish to a static site.
It speaks [Micropub](https://micropub.spec.indieweb.org) and
[IndieAuth](https://indieauth.spec.indieweb.org), turns each post into a
Markdown file with front matter, and commits it to the site's Git repo. The
site's own CI builds and deploys it.

It is a Python re-implementation of the parts of
[Indiekit](https://github.com/getindiekit/indiekit) that awfulwoman.com uses.
Indiekit is the reference for behaviour, not for code.

## Status

Built and tested:

- **IndieAuth**: single-use codes, PKCE, hashed revocable tokens, consent page, profile from your
  h-card, introspection, revocation, metadata.
- **Micropub**: create (form and JSON), update, delete, undelete, all the queries, media uploads
  with photo cleaning (orientation applied, all metadata including GPS stripped) and photos kept
  in page bundles. Notes, articles, bookmarks, likes, replies, reposts, quotes, photos, checkins
  and band names.
- **Publishing**: one Git commit per action to GitHub (or a directory), in the shape your Hugo
  site already uses. The site can own its own post types in `data/micropub.yaml`.
- **Importing** the posts already on the site so they can be queried and edited.
- **Syndication** to Mastodon (or any Mastodon-API server, e.g. GoToSocial) and Bluesky, after the post is live. A GitHub `workflow_run` webhook
  tells the server when the site build finished, so each post shows building, live or failed.
- **A web UI** for writing, editing, deleting and sharing posts, uploading files, and seeing which
  apps have access.
- **A container image** (non-root, read-only filesystem) published to GHCR by CI, which smoke-tests
  the real image before pushing.

Not built: fetching `like-of` targets' microformats
(`enrich_post_data`), HEIC photos, and a live path preview in the forms. The infra role that
deploys it lives in the infra repo.

## Run it locally

Everything runs as a container. You need Docker.

```sh
cp .env.example .env
openssl rand -hex 32                                   # put this in SECRET
docker compose build
docker compose run --rm -T micropub-server hash-password <<< 'your password'   # put this in PASSWORD_HASH
docker compose up
```

Posts land in `./dev/site/content/`. The server listens on
`http://127.0.0.1:8000`. Micropub clients need HTTPS, so for a real client
test, put a tunnel in front of it.

Useful commands (all work through `docker compose run --rm micropub-server …`, or
`docker-compose` where the plugin is not installed):

| Command | Does |
| --- | --- |
| `serve` | Run the server. The default. |
| `hash-password` | Print an argon2id hash. Reads stdin when there is no terminal. |
| `check` | Validate config and environment, and check the store. |
| `migrate` | Apply database migrations. |
| `token` | Print a new access token for a script, once: `token --scope update --label "GitHub Actions"`. |
| `import` | Load the posts already on the site. `--source PATH` reads a local checkout, `--dry-run` changes nothing, `--check-urls` asks the live site for every URL. |

## Develop

```sh
uv sync
uv run pytest                  # unit and HTTP tests
uv run ruff check . && uv run ruff format .
docker build -t micropub-server:smoke . && uv run python scripts/smoke.py micropub-server:smoke
```

The smoke test starts the built image, signs in through IndieAuth, posts a
note and checks the file. CI runs it before publishing.

## Configuration

Structure lives in a YAML file (`CONFIG_FILE`, default `/config/config.yaml`).
Secrets and per-deployment values are environment variables. A working example is in [`dev/config.yaml`](dev/config.yaml).

| Variable | Purpose |
| --- | --- |
| `BASE_URL` | Public URL of this server |
| `PUBLICATION_URL` | The site it publishes to (your IndieAuth identity) |
| `SECRET` | 16+ random characters |
| `PASSWORD_HASH` | argon2id hash from `hash-password` (`PASSWORD_SECRET` accepts a bcrypt hash) |
| `GITHUB_TOKEN` | Fine-grained token, Contents: Read and write, for the GitHub store |
| `MASTODON_ACCESS_TOKEN` | Needed when `syndication.mastodon` is configured |
| `BLUESKY_PASSWORD` | An app password, needed when `syndication.bluesky` is configured |

## Images

CI publishes `ghcr.io/awfulwoman/micropub-server` (amd64 and arm64) on every
push to `main`, tagged `latest` and `sha-<short>`. Production pins the `sha-`
tag.
