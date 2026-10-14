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
- **Container only.** It ships as an OCI image published to
  `ghcr.io/awfulwoman/micropub-server` by GitHub Actions, runs as a non-root
  user, and is developed and smoke-tested as the same image
  ([11](11-deployment.md)).

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
| `web` | 13 | Browser UI: forms, posts list, share, files, tokens |
| (integrations) | 14 | Site workflow, gateway tools, agents skill, Karakeep, OwnTracks |
| `db` | 10 | SQLite access and migrations |

## Suggested stack

Use maintained PyPI packages rather than hand-rolled code.

| Need | Package |
| --- | --- |
| HTTP framework | Starlette + Uvicorn. Matches apple-calendar-server, mail-archive-server and gateway. |
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
| Python | `requires-python = ">=3.12"`, as the sibling repos. The image uses 3.13. |
| Tests | pytest + pytest-asyncio, as the sibling repos. respx to mock httpx. |
| Lint | ruff |

## Build phases

Each phase ends deployable.

1. **Auth and notes.** IndieAuth (password, consent, codes, tokens,
   introspection, metadata). Micropub create for notes, form-encoded and JSON.
   `q=config`. GitHub store. Hugo template for notes. Dockerfile,
   `compose.yaml` for local runs, and the GHCR workflow with smoke test.
2. **All post types.** Article, bookmark, like, reply, repost, photo,
   checkin, quote, bandname. Media endpoint with EXIF stripping. `q=source`, `q=category`,
   `q=syndicate-to`, `q=post-types`.
3. **Editing and import.** Update (replace, add, delete), delete, undelete.
   Draft scope. `import` command for existing posts.
4. **Syndication.** Mastodon, then Bluesky. `/syndicate` endpoint.
5. **Web UI** ([13](13-web-ui.md)). Write, edit and delete posts in the
   browser. Share bookmarklet and Web Share Target. Files and tokens pages.
   Split into 5a to 5c.

## Decisions (2026-10-14)

- **Discovery order:** a bookmark that has a photo is a **photo**, which
  keeps Indiekit's order. Checkins are the exception (see
  [05](05-post-model.md#post-type-discovery)).
- **Import:** build `micropub-server import`, so `q=source` and updates work
  on posts the server did not create ([10](10-data-model.md#import-command)).
- **In scope:** checkins, quotes and bandnames are all supported post types
  ([06](06-post-templates.md)). Guinea pig profiles stay out of scope.
