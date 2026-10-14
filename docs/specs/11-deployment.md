# 11 Deployment

## Container

The app ships only as an OCI image. Production, CI and local testing all run
the same image. No host Python installs.

### Image

A multi-stage `Dockerfile` at the repo root:

```dockerfile
# Build stage: resolve dependencies with uv into a venv
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /usr/local/bin/uv   # pin an exact uv tag
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Runtime stage: venv and source only, no build tools
FROM python:3.13-slim
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data /config && chown app:app /data
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CONFIG_FILE=/config/config.yaml \
    DATA_DIR=/data \
    PORT=8000
USER app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["micropub-server"]
CMD ["serve"]
```

Rules:

- **Never root.** `USER app` (uid and gid 1000). No entrypoint that starts as
  root and drops privileges (podderton uses `gosu` for that). `/data` is
  owned by uid 1000 on the host, which the infra role sets.
- **Writable paths:** `/data` only. The image is compatible with
  `read_only: true` plus a `tmpfs` at `/tmp`. The compose file uses both.
- **`/config/config.yaml`** is mounted read-only from the infra role.
- **One process:** `micropub-server serve` runs Uvicorn with
  `--proxy-headers --forwarded-allow-ips='*'`, because Traefik terminates TLS.
  Logs are JSON lines on stdout.
- **Image extras:** Pillow and `pillow-heif` ship as wheels, so no system
  packages are needed. If HEIC needs `libheif`, install it in the runtime
  stage only.
- **`.dockerignore`:** `.git`, `.venv`, `tests/`, `docs/`, `data/`, `*.db`,
  `.env`.
- **OCI labels:** set by `docker/metadata-action` (source, revision,
  created). Plus `org.opencontainers.image.description`.
- **Size:** about 250 MB today (the slim Python base is about 120 MB). Image processing
  libraries will add to it in phase 2. Do not optimise early.

### CLI subcommands in the image

`micropub-server` is the entrypoint. Its subcommands:

| Command | Purpose | Example |
| --- | --- | --- |
| `serve` (default) | Run the server | `docker run … ghcr.io/awfulwoman/micropub-server` |
| `hash-password` | Read a password from the TTY, or from stdin with no TTY, and print the argon2id hash | `docker run --rm -it ghcr.io/awfulwoman/micropub-server hash-password` |
| `import` | Load existing posts ([10](10-data-model.md#import-command)) | `docker compose run --rm micropub-server import --dry-run` |
| `check` | Validate config and env, check store access, then exit | `docker compose run --rm micropub-server check` |
| `migrate` | Apply database migrations. `serve` also runs them at startup. | |

`hash-password` must work in all of these:

- `docker run -it` (TTY, no echo)
- `echo pw | docker run -i` (stdin)
- Claude Code's `!` prompt (no TTY)

## Publishing to GHCR

Images are published to GitHub Container Registry as
**`ghcr.io/awfulwoman/micropub-server`**, built by GitHub Actions. The
workflow file is `.github/workflows/docker.yml`, using the same pattern as
podderton and mail-archive-server.

### Triggers

- push to `main`
- tags matching `v*`
- pull requests to `main`: build and test only, **no push**
- `workflow_dispatch`

### Jobs

1. **`test`**
   - `astral-sh/setup-uv`, then `uv sync --frozen`.
   - `uv run ruff check`, then `uv run pytest`.
   - Tests use the file-system store and respx mocks. They never call real
     GitHub, Mastodon or Bluesky.
2. **`build`** (`needs: test`)
   - Permissions: `contents: read`, `packages: write`.
   - QEMU and Buildx.
   - **Smoke test first.** Build `linux/amd64` with `load: true` (tag
     `micropub-server:smoke`), then run `scripts/smoke.py` (Python, run with `uv run`) against it:
     - start it with a test config, the file-system store and a test
       password hash
     - wait for `/health` to return 200
     - sign in through the full auth flow, post one note through Micropub,
       and assert the file appears
     - this runs on pull requests too, which never push, so it must use the
       locally loaded image
   - Log in to `ghcr.io` with `${{ github.actor }}` and
     `${{ secrets.GITHUB_TOKEN }}`. Skipped on pull requests.
   - `docker/metadata-action` with
     `images: ghcr.io/${{ github.repository }}` and these tags:
     - `type=raw,value=latest,enable={{is_default_branch}}`
     - `type=ref,event=pr`
     - `type=semver,pattern={{version}}`
     - `type=semver,pattern={{major}}.{{minor}}`
     - `type=sha`
   - `docker/build-push-action` with:
     - `platforms: linux/amd64,linux/arm64`. arm64 is for the Raspberry Pis
       and local runs on the Mac mini.
     - push only when not a pull request.
     - `cache-from` and `cache-to: type=gha`. The amd64 layers from the smoke
       build are reused.

### Package settings

- The repo starts **private** on GitHub, so the package is private too. The
  infra host already logs in to GHCR (`system-docker`'s "Log in to GHCR so
  private images can be pulled" task), so private pulls work. Make it public
  later if the repo becomes public.
- Link the package to the repo, which `metadata-action` labels do, so the
  repo's Actions token can push.
- **Retention:** a weekly workflow (`cleanup.yml`, `actions/delete-package-versions`)
  keeps the newest 10 versions plus `latest` and version-number tags, and
  deletes the rest (old `sha-*` and untagged layers). The action cannot filter by
  age, so this replaces the earlier "older than 90 days" rule.

### Which tag production runs

- The infra role pins **`sha-<short>`**, not `latest`. That matches the infra
  rule that only images we publish ourselves may use `:latest`, but
  pinning a sha makes rollbacks a one-line change and lets the nightly
  Semaphore run stay predictable.
- Releases: tag `vX.Y.Z` on `main` to get `X.Y.Z` and `X.Y` tags. Before
  1.0, pinning `sha-*` is enough.

## Local development

Build and run the real image locally (the coding rules ask for this). A
`compose.yaml` in the repo:

```yaml
name: micropub-server
services:
  micropub-server:
    build: .
    image: micropub-server:dev
    ports:
      - "127.0.0.1:8000:8000"
    env_file: .env                 # BASE_URL, PUBLICATION_URL, SECRET, PASSWORD_HASH
    volumes:
      - ./dev/config.yaml:/config/config.yaml:ro
      - ./dev/site:/site           # file-system store target
      - data:/data
    read_only: true
    tmpfs: [/tmp]
volumes:
  data:
```

- `dev/config.yaml` uses `store.type: filesystem` and `directory: /site`, so
  posts land in `./dev/site/content/…` and can be checked by eye.
- `BASE_URL=http://localhost:8000/`. Micropub clients need HTTPS, so for
  real client tests use the test hostname from the cut-over plan, or a
  Tailscale Funnel or `cloudflared` tunnel.
- `docker compose up --build` to run. `docker compose run --rm micropub-server check`
  to validate the config.
- Unit tests run outside the container for speed (`uv run pytest`). The
  smoke test runs against the container, both locally
  (`scripts/smoke.py`) and in CI. The script keeps its working files under the repo, not `/tmp`,
  because Colima and Docker Desktop share `$HOME` but not the system temp directory.

## Health

`GET /health`, no auth:

```json
{ "status": "ok", "store": "ok", "db": "ok", "version": "sha-3bd7a18" }
```

- `store` is `degraded` when the startup check found the GitHub token
  cannot push (see [07](07-content-stores.md)).
- Returns `503` only when the database is unusable. A degraded store still
  returns `200`, so Traefik keeps routing, and sign-in still works for
  diagnosis.
- `version` comes from the `org.opencontainers.image.revision` build arg,
  passed into the image as the `APP_VERSION` env var.
- The image `HEALTHCHECK` above covers Docker. The compose file in infra does
  not need its own.

## Infra

Replace the `composition-indiekit` role with `composition-micropub-server` on
`vps-hetzner-public01`. Create it with the `awfulwoman-ansible-create-composition`
skill, and follow its conventions:

- Role `roles/composition-micropub-server/`. `composition_name: micropub-server`.
- Variables `composition_micropub_server_*`, e.g.
  `composition_micropub_server_image`.
- Secrets in `inventory/group_vars/infra/vault_composition_micropub_server.yaml`,
  named `vault_composition_micropub_server_<thing>`. **Copy the values** from
  the indiekit vault vars, rather than pointing at them, so the Indiekit role
  can be deleted cleanly.
- `composition_dns_subdomains: []`, because the name is public and lives in
  `infra-public-resources`.
- Every loop sets `loop_control.loop_var`.

Further details:

- Hostname `indie.{{ domainname_personal }}` (`indie.awfulwoman.com`). The
  public CNAME already exists in `infra-public-resources`.
- Keep the Traefik router that blocks `/auth/new-password` with a 418. The new
  server has no such path, but the block costs nothing.
- Image `ghcr.io/awfulwoman/micropub-server:sha-<short>` (pinned, see
  above), `pull: missing`.
- One container plus a volume (`{{ composition_config }}/data:/data`, owned
  by uid 1000). **No MongoDB.** `read_only: true`, `tmpfs: /tmp`.
- `config.yaml` rendered from a template into `composition_config`, mounted
  read-only.
- Env vars from the new vault vars, whose values are copied from Indiekit's:
  - `vault_composition_micropub_server_secret` (was `…indiekit_secret`)
    becomes `SECRET`.
  - `vault_composition_micropub_server_password_secret` (was
    `…indiekit_password_secret`, bcrypt) becomes `PASSWORD_SECRET`.
  - `vault_composition_micropub_server_github_token` (was
    `…indiekit_github_token`) becomes `GITHUB_TOKEN`.
- ZFS policy `low`, as now.

### Deploys follow the pin, not the merge

The nightly Semaphore run deploys infra's GitHub `main`. A merged server
change is **not** live until the `sha-*` pin in
`composition_micropub_server_image` is bumped in infra, and that change is
pushed. Optionally add a Renovate rule (infra has `renovate.json`) to open
pin-bump PRs when a new image is published.

### Monitoring

- **Gatus** (`composition-gatus`): add an endpoint for
  `https://indie.awfulwoman.com/health`. Conditions: `[STATUS] == 200`,
  `[BODY].store == ok`, `[BODY].db == ok`, `[RESPONSE_TIME] < 2000`. Check
  every 5 minutes.
- **Alerts go to Home Assistant**, not email, like all monitoring here.
- **Logs:** public01 runs `logtide-agent`. Before fixing the JSON log field
  names (`time`, `level`, `msg`, …), check what logtide parses, and match it
  so logs need no extra parsing rules.
- Metrics are optional, a later `/metrics` for VictoriaMetrics: posts
  created, store errors, token checks.

## Cut-over from Indiekit

1. Deploy the new server on a second hostname, such as
   `micropub.awfulwoman.com`, or on a path. Test it with Quill against a
   branch or a scratch repo using the file-system store.
2. Point the `indie` router at the new container. The URLs stay the same,
   so the site `<head>` does not change.
3. Clients get `401` on their old Indiekit JWTs and must sign in again.
   Expected.
4. Optionally run `micropub-server import` to load existing posts.
5. Remove the Indiekit and Mongo containers, and the role, once happy.

## Security checklist

- HTTPS only. Traefik handles it. Set `Strict-Transport-Security`.
- Cookies `Secure`, `HttpOnly`, `SameSite=Lax`. CSRF tokens on every form.
- Rate-limit `/auth/consent` POSTs and token redemption.
- SSRF guard on every outbound fetch to a user-supplied URL (`client_id`,
  `me`, `like-of`, categories URL):
  - resolve the host first.
  - reject loopback, link-local, private and Tailscale (100.64.0.0/10)
    addresses.
  - 5 s timeout and a 1 MB body limit.
- Request body limit: 25 MB on `/micropub` and `/media`, 64 KB elsewhere.
- Constant-time comparisons for PKCE and token hashes.
- Content-Security-Policy on the HTML pages: `default-src 'self'`.
