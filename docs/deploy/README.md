# Deploying micropub-server

This replaces the Indiekit container at `indie.awfulwoman.com`. The URLs stay the same
(`/micropub`, `/media`, `/auth`, `/auth/token`), so the site's `<head>` links keep working.
Apps need to sign in again, because tokens change format.

The files next to this one are meant to be copied:

| File | Goes in | Purpose |
| --- | --- | --- |
| `site-micropub.yaml` | the site repo, as `data/micropub.yaml` | The site's own post types and front matter mapping |
| `production-config.example.yaml` | the infra role, rendered to `/config/config.yaml` | The server's settings: store, channels, syndication |

Both are checked by tests, so they stay loadable.

## What has and has not been proved

Tested here: the whole API and web UI (over 600 tests), the importer against the real site content,
and the built image under a read-only filesystem as a non-root user (the smoke test runs in CI
before every image is published).

**Not tested against the real services**, because that needs your credentials: the GitHub store
against GitHub itself (it is tested against a mock of GitHub's API), Mastodon and Bluesky against
the real networks, and real Micropub apps (Quill and so on). The first deploy is that test, which
is why step 6 below starts with a scratch hostname.

## 1. The image

CI publishes `ghcr.io/awfulwoman/micropub-server` for amd64 and arm64 on every push to `main`,
tagged `latest` and `sha-<short>`. The package is private, which the hosts already cope with
(`system-docker` logs in to GHCR). Production pins a `sha-` tag, so a rollback is a one-line change
and the nightly Semaphore run, which deploys infra's `main`, stays predictable.

## 2. The site repo (awfulwoman/awfulwoman.com)

Commit these without any Claude or co-author trailers (that repo's rule).

1. **`data/micropub.yaml`**: copy `site-micropub.yaml`. From then on a layout change and the
   matching change here go in one commit.
2. **`layouts/_default/baseof.html`**: the three links are there already. Add the metadata link
   that newer IndieAuth clients use:

   ```html
   <link rel="indieauth-metadata" href="https://indie.awfulwoman.com/.well-known/oauth-authorization-server">
   ```

3. **Layouts for replies, reposts, quotes and checkins**: they are written correctly but never
   shown. Tracked in awfulwoman/awfulwoman.com#1. Do this before relying on those post types.
4. **A tag list for suggestions** (optional): a Hugo JSON output at `/tags/index.json` that is a
   plain list of tag names, then set `publication.categories` to its URL.
5. **Share after deploy**: add a last step to `.github/workflows/build.yaml`, after the rsync:

   ```yaml
   - name: Share new posts
     if: github.ref == 'refs/heads/main'
     run: |
       curl --fail-with-body -sS -X POST https://indie.awfulwoman.com/syndicate \
         -H "Authorization: Bearer ${{ secrets.SYNDICATE_TOKEN }}"
   ```

   `SYNDICATE_TOKEN` is an access token with only the `update` scope. Make it on the server and
   store it as a repository secret (it is shown once, and can be revoked from the apps page):

   ```sh
   docker exec micropub-server micropub-server token --scope update --label "GitHub Actions" --days 365
   ```

   Without this step posts are shared the next time anyone calls `/syndicate` or presses
   "Share now".
6. **Photos** are stripped of location by the server. The pre-commit hook `exif.sh` does not run for
   commits made through the API, so consider a CI check that fails the build if any image under
   `content/` still has GPS data.

## 3. The infra repo

Create `composition-micropub-server` with the `awfulwoman-ansible-create-composition` skill and its
conventions. It is a replacement for `composition-indiekit` on `vps-hetzner-public01`.

- `composition_name: micropub-server`, `composition_dns_subdomains: []` (the name is public, and
  already has its CNAME in `infra-public-resources`), variables `composition_micropub_server_*`.
- Secrets in `inventory/group_vars/infra/vault_composition_micropub_server.yaml`. **Copy the values**
  from the Indiekit vault variables rather than pointing at them, so the old role can be deleted:

  | New variable | Becomes | From |
  | --- | --- | --- |
  | `vault_composition_micropub_server_secret` | `SECRET` | `vault_composition_indiekit_secret` |
  | `vault_composition_micropub_server_password_secret` | `PASSWORD_SECRET` | `vault_composition_indiekit_password_secret` (a bcrypt hash, accepted as is) |
  | `vault_composition_micropub_server_github_token` | `GITHUB_TOKEN` | `vault_composition_indiekit_github_token` |
  | `vault_composition_micropub_server_mastodon_token` | `MASTODON_ACCESS_TOKEN` | a new token with `write:statuses` and `write:media` |

  Later, replace the bcrypt hash with an argon2 one (`hash-password`, see the README) and set
  `PASSWORD_HASH` instead.
- `templates/docker-compose.yaml.j2`:

  ```yaml
  # code: language=ansible-jinja
  name: "{{ composition_name }}"
  services:
    micropub-server:
      container_name: micropub-server
      image: "{{ composition_micropub_server_image }}"   # ghcr.io/awfulwoman/micropub-server:sha-<short>
      restart: unless-stopped
      read_only: true
      tmpfs: [/tmp]
      env_file: .environment_vars
      environment:
        BASE_URL: "https://indie.{{ domainname_personal }}/"
        PUBLICATION_URL: "https://{{ domainname_personal }}/"
      volumes:
        - "{{ composition_config }}/config.yaml:/config/config.yaml:ro"
        - "{{ composition_config }}/data:/data"          # owned by uid 1000
      labels:
        - "traefik.enable=true"
        - "traefik.http.routers.micropub-server.rule=Host(`indie.{{ domainname_personal }}`)"
        - "traefik.http.routers.micropub-server.tls=true"
        - "traefik.http.services.micropub-server.loadbalancer.server.port=8000"
      networks:
        - "{{ default_docker_network }}"
      # The image has its own HEALTHCHECK (GET /health), so none is needed here.

  networks:
    "{{ default_docker_network }}":
      external: true
  ```

  The container needs no MongoDB and no second network.
- Tasks: render the compose file, `.environment_vars` (mode 0600), and `config.yaml` (from
  `production-config.example.yaml`); create `{{ composition_config }}/data` owned by uid 1000;
  `docker_compose_v2` with `pull: missing`.
- ZFS: dataset `micropub-server`, policy `low` (the posts themselves live in Git; losing the
  database means apps sign in again and the queue of posts waiting to be shared is lost).
- **Monitoring**: a Gatus endpoint for `https://indie.awfulwoman.com/health` with
  `[STATUS] == 200` and `[BODY].store == ok`. `/health` returns `"store": "degraded"` when the
  GitHub token cannot write, which is exactly the failure that cost an hour on the first Indiekit
  deploy. Alerts go to Home Assistant.
- Keep the Traefik router that blocks `/auth/new-password`. The new server has no such page, but
  the block costs nothing.

## 4. Check the GitHub token before anything else

The token needs **Contents: Read and write** on `awfulwoman/awfulwoman.com`. After the first start:

```sh
docker exec micropub-server micropub-server check
```

It prints `ok: GitHub store ...` or says exactly what is wrong. `/health` and the web UI's status
page report the same thing.

## 5. Load the posts that already exist

From a checkout of the site on the server, or any machine with the config, run a dry run first:

```sh
cd <the composition directory on the host>
docker compose run --rm -v /path/to/site-checkout:/site:ro micropub-server import --source /site --dry-run
docker compose run --rm -v /path/to/site-checkout:/site:ro micropub-server import --source /site --check-urls
```

`--check-urls` asks the live site for every imported URL. On the real content, 2,917 of 2,918 were
served; the one that was not is dated in the future. Anything it reports that is not a future post
means a URL was derived wrongly: look at the post type's `import_slug` and its `url` template.

Re-running is safe. It never touches posts the server created itself.

## 6. Cut over

1. Deploy alongside Indiekit under a scratch hostname (for example `micropub-test.awfulwoman.com`),
   with a **test copy** of the repo as the store, and use Quill against it. Sign in, publish one of
   each type you use, edit one, delete and restore one, upload a photo, check the commit history of
   the test repo.
2. Point `indie.awfulwoman.com` at the new container (replace the role).
3. Sign in again from your apps.
4. Make one real post, then watch the site's build and the new page.
5. Delete the Indiekit role, its vault variables, and its MongoDB data when satisfied.

## Rolling back

Redeploy the previous infra commit. The new server only ever writes to the site repo through
ordinary Git commits, so anything it published can be reverted with `git revert` like any other
change, and nothing it does is outside the repo except its own small database.
