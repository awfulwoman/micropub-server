# 11 Deployment

## Container

- Python 3.13 slim base. Install with `uv` from `uv.lock`.
- Run as a non-root user (uid 1000). Only `/data` is writable.
- `EXPOSE 8000`. The command is
  `uvicorn micropub_server.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`.
  The proxy headers are needed because Traefik terminates TLS.
- Logs as JSON lines on stdout. Never log tokens, codes, passwords or the
  `Authorization` header.
- Images are built by a GitHub Actions workflow that runs the tests, then
  publishes `ghcr.io/awfulwoman/micropub-server:latest` and a `sha-<short>`
  tag. Build and test locally before pushing.

## Health

`GET /health`, no auth:

```json
{ "status": "ok", "store": "ok", "db": "ok" }
```

- `store` is `degraded` when the startup check found the GitHub token
  cannot push (see [07](07-content-stores.md)).
- Returns `503` only when the database is unusable.
- The compose healthcheck probes it with Python, since the slim image has no
  curl:
  `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"`.

## Infra

Replace the `composition-indiekit` role with `composition-micropub-server` on
`vps-hetzner-public01`:

- Hostname `indie.{{ domainname_personal }}` (`indie.awfulwoman.com`). The
  public CNAME already exists in `infra-public-resources`.
- Keep the Traefik router that blocks `/auth/new-password` with a 418. The new
  server has no such path, but the block costs nothing.
- One container plus a volume. **No MongoDB.**
- `config.yaml` rendered from a template into `composition_config`, mounted
  read-only.
- Env vars: reuse the existing vault vars.
  - `vault_composition_indiekit_secret` becomes `SECRET`.
  - `vault_composition_indiekit_password_secret` becomes `PASSWORD_SECRET`
    (bcrypt, accepted).
  - `vault_composition_indiekit_github_token` becomes `GITHUB_TOKEN`.
- ZFS policy `low`, as now.

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
