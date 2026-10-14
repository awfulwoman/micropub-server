# 14 Integrations

How the server fits with the other repos in `~/Code/awfulwoman`. Each section
says what changes **outside** this repo, and in which phase.

## awfulwoman.com (site repo)

| Change | Why | Phase |
| --- | --- | --- |
| Add `data/micropub.yaml`: post types, paths, front-matter mapping | The site owns its content shape ([06](06-post-templates.md#the-site-owns-the-shape)) | 1 |
| Add `rel="indieauth-metadata"` to `baseof.html` | Newer IndieAuth clients use it ([02](02-indieauth.md#discovery-on-the-site)) | 1 |
| Generate a tags list, e.g. a JSON output at `/tags/index.json` | Category suggestions for `q=category` and the web UI | 2 |
| Layouts for `in_reply_to`, `repost_of`, `quote_of` and `place_*` | These are written today but not shown (awfulwoman/awfulwoman.com#1) | 2 |
| Check `opengraph.html` with `images: [{file, alt}]` | It expects a list of URL strings | 2 |
| Final step in `.github/workflows/build.yaml`: `POST /syndicate` with `SYNDICATE_TOKEN` | Cross-post after the post is live ([08](08-syndication.md)) | 4 |
| CI step: fail the build if any image under `content/` has GPS EXIF | API commits skip the `exif.sh` pre-commit hook. The server strips EXIF, and this catches anything else. | 2 |

Commits the server makes to this repo never carry Claude or co-author
trailers ([07](07-content-stores.md#commit-messages-and-authors)).

## agents: the `awfulwoman-create-draft` skill

Today the skill runs
`/Users/charlie/Code/awfulwoman/personal-site/scripts/create/<type>`, which no
longer exists. The user and the repo were renamed, and `scripts/` is gone.

Once phase 1 is live, rewrite the skill to post through the server instead
of writing files:

- Create with `post-status: draft` (the skill's existing meaning), through
  the gateway tools below. This also works from other machines and from
  Hermes, unlike a local script.
- Keep the skill's table of inputs per type. Map each one onto Micropub
  properties: `quote` → `quotation-of` + `content`, `checkin` → a `checkin`
  h-card, `bandname` → `name` + `mp-channel=bandnames`.
- Report the post's URL and its web UI edit link (`/posts/{uid}/edit`).

`awfulwoman-list-drafts` can use `q=source` with a draft filter in the same
way.

Until then, the broken path is tracked in awfulwoman/agents#1.

## gateway: MCP tools

Gateway is how agents reach personal services. Add a `micropub` module:

| Tool | Calls |
| --- | --- |
| `create_post(type, properties, draft=True)` | `POST /micropub` (JSON) |
| `update_post(url, replace, add, delete)` | `POST /micropub` `action=update` |
| `get_post(url)` | `GET /micropub?q=source&url=` |
| `list_posts(type?, status?, limit?)` | `GET /micropub?q=source&post-type=…` |
| `upload_media(path)` | `POST /media` |

- Auth: a long-lived token from a dedicated client. Scopes: `create draft
  update media`, **without** `delete`. Agents create and edit drafts. A
  person publishes and deletes.
- `draft` scope means agent-created posts are always drafts, whatever the
  agent asks for ([03](03-micropub.md#scope-check)).
- Store the token in gateway's vault vars, like its other service tokens.
- Use the Bearer handling from gateway's other services
  (`hmac.compare_digest`) as the model for this server's token checks.
- Phase 2.

## Karakeep: publish bookmarks (optional)

github2karakeep already funnels links into Karakeep. A Karakeep tag such as
`publish` can become a bookmark post:

- An n8n workflow, or a gateway job, polls Karakeep for bookmarks tagged
  `publish`.
- For each one, call `create_post(bookmark, {bookmark-of, name, content: note})`
  as a draft, then remove the tag.
- After phase 2.

## OwnTracks: checkin location (optional)

The web UI's checkin form ([13](13-web-ui.md)) can fill in latitude and
longitude from gateway's `get_current_location`, as well as the browser's
Geolocation API. This helps on the desktop, where the browser has no GPS.
Phase 5b.

## infra

Covered in [11](11-deployment.md#infra):

- the `composition-micropub-server` role, created with the composition skill
- the pinned `sha-*` image
- the Gatus health check, alerting through Home Assistant
- logs matched to logtide's field names
- the ZFS `low` policy

## personal-cloud

Once the server is running, add it to the architecture diagrams:

- clients → micropub-server → GitHub → the site build → public01
- micropub-server → Mastodon and Bluesky
- gateway → micropub-server
