# 00 Overview

## Purpose

Publish to awfulwoman.com from any Micropub client (Quill, iA Writer,
Indigenous, the iOS share sheet) without editing files by hand.

The flow:

1. A client discovers the endpoints from the `<link>` tags in the site's
   `<head>`.
2. The user signs in through IndieAuth with a password.
3. The client sends a post to the Micropub endpoint.
4. The server normalises the post, picks a post type, renders a Markdown file
   with front matter, and commits it to `awfulwoman/awfulwoman.com` on `main`.
5. The site's GitHub Actions workflow builds and deploys it. The server does
   not build the site.

## Reference implementation

Indiekit v1.0.0-beta.31, upstream commit `5308a2f` (October 2026), which runs
today at `indie.awfulwoman.com`. Where this spec says "Indiekit does X", it
means that version. Where we differ on purpose, see
[12 Indiekit divergences](12-indiekit-divergences.md).

## Goals

- **Drop-in replacement.** Same public URLs (`/micropub`, `/media`, `/auth`,
  `/auth/token`), so the site `<head>` and signed-in clients keep working.
  Clients must sign in again because tokens change format.
- **Standards first.** Follow the Micropub and IndieAuth specs, including the
  places where Indiekit does not.
- **Output that matches the site.** Files land in the paths and front-matter
  shape that the existing Hugo templates already read (snake_case keys,
  `HHMM` filenames, page bundles for photos). See
  [06 Post templates](06-post-templates.md).
- **Small and boring.** One process, SQLite, YAML config, no plugin loader.
- **12-factor.** Config from a file plus environment variables, logs to
  stdout, state in one volume.

## Non-goals

- Multi-user or multi-publication hosting.
- A plugin system. New post types, stores or syndicators are code changes.
- Building or deploying the site.
- Microsub, Webmention sending or receiving, and the JF2 feed.
- Stores other than GitHub and the local file system.
- Localisation of the UI.
- Refresh tokens. They are optional in IndieAuth and Indiekit lacks them too.

## Architecture

```
client ──HTTPS──> Traefik ──> micropub-server ──HTTPS──> GitHub contents API
                                   │                ──> Mastodon / Bluesky
                                   └── SQLite (/data/micropub.db)
```

Modules, one per spec:

| Module | Spec | Job |
| --- | --- | --- |
| `auth` | 02 | IndieAuth authorization server and token verification |
| `micropub` | 03 | Micropub endpoint: actions and queries |
| `media` | 04 | Media endpoint |
| `posts` | 05 | JF2 handling, normalisation, type discovery, paths |
| `templates` | 06 | JF2 to file content |
| `stores` | 07 | Write files to GitHub or disk |
| `syndication` | 08 | Cross-posting |
| `config` | 09 | Load and validate settings |
| `db` | 10 | SQLite access and migrations |

## Suggested stack

Use maintained PyPI packages rather than hand-rolled code.

| Need | Package |
| --- | --- |
| HTTP framework | FastAPI (or Starlette) + Uvicorn |
| HTML forms for sign-in and consent | Jinja2, plain HTML and CSS |
| Outbound HTTP | httpx |
| Microformats parsing (client info, `redirect_uri` discovery) | mf2py |
| mf2 to JF2 | small local function. No maintained PyPI package exists, and it is ~50 lines (see 05) |
| Markdown to HTML | markdown-it-py |
| HTML to Markdown | markdownify |
| YAML front matter | ruamel.yaml (keeps key order, safe dump) |
| Password hashing | argon2-cffi. Accept existing bcrypt hashes via `bcrypt` during cut-over |
| Signed values, if needed | itsdangerous |
| Images (rotate, resize, strip EXIF) | Pillow |
| File type sniffing | filetype |
| Mastodon | Mastodon.py |
| Bluesky | atproto |
| Database | sqlite3 from the standard library, or SQLAlchemy Core |
| Settings | pydantic-settings |
| Tests | pytest, respx (mock httpx) |

## Build phases

Each phase ends deployable.

1. **Auth and notes.** IndieAuth (password, consent, codes, tokens,
   introspection, metadata). Micropub create for notes, form-encoded and JSON.
   `q=config`. GitHub store. Hugo template for notes. Container and health
   check.
2. **All post types.** Article, bookmark, like, reply, repost, photo,
   checkin. Media endpoint with EXIF stripping. `q=source`, `q=category`,
   `q=syndicate-to`, `q=post-types`.
3. **Editing.** Update (replace, add, delete), delete, undelete. Draft scope.
4. **Syndication.** Mastodon, then Bluesky. `/syndicate` endpoint.
5. **Small admin UI.** List recent posts, show the source, re-run
   syndication. Optional.

## Open questions

- Should the server read existing posts from the repo so `q=source` and
  updates work for posts it did not create? Indiekit cannot. A one-off import
  script reading `content/` would cover it.
- Checkins: create them through Micropub (`checkin` property, h-card
  location) or keep them historical only?
- Quotes, bandnames and guinea pig profiles: custom post types through `h`
  values, or out of scope?
