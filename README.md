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
- **Syndication** to Mastodon and Bluesky, after the post is live. A GitHub `workflow_run` webhook
  tells the server when the site build finished, so each post shows building, live or failed.
- **A web UI** for writing, editing, deleting and sharing posts, uploading files, and seeing which
  apps have access.
- **A container image** (non-root, read-only filesystem) published to GHCR by CI, which smoke-tests
  the real image before pushing.

Not built: fetching `like-of` targets' microformats
(`enrich_post_data`), HEIC photos, and a live path preview in the forms. The infra role that
deploys it lives in the infra repo. See [`docs/specs/`](docs/specs/00-overview.md).

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
Secrets and per-deployment values are environment variables. Both are
described in [`docs/specs/09-configuration.md`](docs/specs/09-configuration.md).
A working example is in [`dev/config.yaml`](dev/config.yaml).

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

## Specs

Start with [`docs/specs/00-overview.md`](docs/specs/00-overview.md).

| Spec | Covers |
| --- | --- |
| [00 Overview](docs/specs/00-overview.md) | Goals, scope, architecture, build phases |
| [01 Standards](docs/specs/01-standards.md) | Which open standards, and how much of each |
| [02 IndieAuth](docs/specs/02-indieauth.md) | Sign-in, codes, tokens, introspection, metadata |
| [03 Micropub](docs/specs/03-micropub.md) | Create, update, delete, undelete, queries |
| [04 Media endpoint](docs/specs/04-media-endpoint.md) | Uploads, image processing, media queries |
| [05 Post model](docs/specs/05-post-model.md) | JF2, normalisation, post type discovery, slugs, paths |
| [06 Post templates](docs/specs/06-post-templates.md) | Rendering Hugo front matter, awfulwoman.com mappings |
| [07 Content stores](docs/specs/07-content-stores.md) | The store interface and the GitHub store |
| [08 Syndication](docs/specs/08-syndication.md) | Mastodon and Bluesky cross-posting |
| [09 Configuration](docs/specs/09-configuration.md) | YAML config and environment variables |
| [10 Data model](docs/specs/10-data-model.md) | SQLite schema |
| [11 Deployment](docs/specs/11-deployment.md) | Container image, GHCR publishing, local dev, infra, cut-over |
| [12 Indiekit divergences](docs/specs/12-indiekit-divergences.md) | Indiekit bugs and choices we do not copy |
| [13 Web UI](docs/specs/13-web-ui.md) | Writing and managing posts in the browser, share bookmarklet |
| [14 Integrations](docs/specs/14-integrations.md) | Site repo, gateway tools, create-draft skill, Karakeep, OwnTracks, infra |
