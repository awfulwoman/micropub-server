# 09 Configuration

Two sources, following 12-factor:

- **`config.yaml`**: structure that belongs in Git, such as the publication,
  post types, the front-matter mapping and syndication targets. The path
  comes from `CONFIG_FILE`, default `/config/config.yaml`.
- **Environment variables**: secrets, and anything that varies between
  deployments.

Validate everything at startup with pydantic-settings. A bad config stops
the process with a clear message. Also warn about unknown path tokens
([05](05-post-model.md#path-templates)).

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CONFIG_FILE` | | `/config/config.yaml` | Config path |
| `DATA_DIR` | | `/data` | SQLite database location |
| `BASE_URL` | yes | | Public URL of this server, e.g. `https://indie.awfulwoman.com/`. Used for `issuer` and endpoint URLs. Indiekit works it out from request headers, so set it explicitly. |
| `PUBLICATION_URL` | yes | | `me`, e.g. `https://awfulwoman.com/`. Same name as Indiekit. |
| `PASSWORD_HASH` | one of these | | argon2id hash of the sign-in password |
| `PASSWORD_SECRET` | one of these | | bcrypt hash. Indiekit's name, accepted during cut-over |
| `SECRET` | yes | | Key for session cookies and CSRF. Indiekit's name. 32+ random bytes |
| `GITHUB_TOKEN` | if GitHub store | | Fine-grained PAT, Contents read and write |
| `MASTODON_ACCESS_TOKEN` | if Mastodon | | |
| `BLUESKY_PASSWORD` | if Bluesky | | App password |
| `WEBHOOK_SECRET` | | | Enables `POST /webhooks/github` (spec 08). The same secret goes in the GitHub webhook |
| `PORT` | | `8000` | Listen port |
| `LOG_LEVEL` | | `info` | |
| `TZ` | | `UTC` | Process time zone. Posts use `publication.time_zone`. |

The infra composition maps the existing vault vars onto these: `SECRET`,
`PASSWORD_SECRET` and `GITHUB_TOKEN` already exist.

## `config.yaml`

```yaml
# Settings for the server itself
application:
  name: Micropub server
  time_zone: Europe/Berlin         # IANA name, or "client" to keep client dates
  locale: en-GB                    # month names in path tokens
  token_ttl_days: 90
  cache_ttl_seconds: 604800        # remote categories, client info, profiles
  max_body_bytes: 26214400         # largest upload (/micropub, /media, forms with photos)
  max_form_bytes: 65536            # largest body anywhere else

publication:
  locale: en-GB
  slug_separator: "-"
  enrich_post_data: false          # fetch mf2 for like-of etc.
  fetch_target_meta: true          # save title, author, embed of liked/quoted/bookmarked pages
  fetch_target_image: true         # download a bookmark's card image into its bundle (16)
  categories: https://awfulwoman.com/tags/index.json   # list, or URL to a JSON list
  profile:                         # overrides h-card discovery for the profile scope
    name: Charlie O'Hara
    photo: https://awfulwoman.com/avatar.jpg
  commit_message: "{action} {post_type} {file_type}"

store:
  type: github                     # github | filesystem
  owner: awfulwoman
  repo: awfulwoman.com
  branch: main
  # directory: ./site              # for filesystem

media_store: null                  # null = same as store

media:
  max_bytes: 20971520
  image:
    max_width: 2560
    max_height: 2560
    jpeg_quality: 85
  upload_path: content/_uploads/{yyyy}/{MM}/{md5}.{ext}
  card:                            # bookmark link card images (16)
    max_bytes: 5242880
    max_width: 800
    format: webp
    quality: 80

template:
  type: hugo
  front_matter: yaml
  # JF2 property -> front matter key. Defaults as in spec 06.
  mapping:
    name: title
    published: date
    updated: lastmod
    category: tags
    bookmark-of: bookmark_of
    like-of: like_of
    repost-of: repost_of
    in-reply-to: in_reply_to
    quotation-of: quote_of
    photo: images
  omit: [uid, slug, url, publishDate, channel]

post_types:
  note:
    name: Note
    post:
      path: content/notes/{yyyy}/{MM}/{dd}/{slug}/index.md
      url: notes/{yyyy}/{MM}/{dd}/{slug}/
    properties: [content, category, location, photo]
    required_properties: [content]
  article:
    name: Post
    post:
      path: content/posts/{yyyy}/{MM}/{dd}/{slug}/index.md
      url: posts/{slug}/
    media:
      path: content/posts/{yyyy}/{slug}/{filename}
    properties: [name, content, summary, category, photo]
    required_properties: [name, content]
  bookmark:
    name: Bookmark
    discovery: bookmark-of
    post:
      path: content/bookmarks/{yyyy}/{MM}/{dd}/{slug}/index.md
      url: bookmarks/{yyyy}/{MM}/{dd}/{slug}/
    properties: [bookmark-of, name, content, category]
    required_properties: [bookmark-of]
  photo:
    name: Photo
    discovery_without: [content]   # a photo with words is a note, titled or not
    discovery_without_type: note
    post:
      path: content/photos/{yyyy}/{MM}/{dd}/{slug}/index.md
      url: photos/{yyyy}/{MM}/{dd}/{slug}/
    media:
      path: content/photos/{yyyy}/{MM}/{dd}/{slug}/{filename}
    properties: [photo, name, category, location]
    required_properties: [photo]
  quote:
    name: Quote
    discovery: quotation-of
    post:
      path: content/quotes/{yyyy}/{MM}/{dd}/{slug}.md
      url: quotes/{yyyy}/{MM}/{dd}/{slug}/
    properties: [quotation-of, content, author]
    required_properties: [quotation-of, content]
  checkin:
    name: Checkin
    discovery: checkin
    discovery_first: true          # a checkin with a photo stays a checkin
    post:
      path: content/checkins/{yyyy}/{MM}/{dd}/{slug}/index.md
      url: checkins/{yyyy}/{MM}/{dd}/{slug}/
    media:
      path: content/checkins/{yyyy}/{MM}/{dd}/{slug}/{filename}
    properties: [checkin, content, photo, category]
    required_properties: [checkin]
  page:
    name: Page
    channel: pages                 # chosen by mp-channel=pages
    post:
      path: content/{slug}.md      # at the site root, so the URL is /{slug}/
      url: "{slug}/"
    properties: [name, content]
    required_properties: [name, content]
  # like, reply, repost: as in spec 06

channels:                          # keyed by uid. The first is the default.
  main: {name: Main}
  pages: {name: Pages}

deploy:
  workflow: Build site and push to server   # the run that means "the site is live"

syndication:
  require_live: true            # fetch the post's public URL first; leave it queued if not served yet
  mastodon:
    url: https://indieweb.social
    user: awfulwoman
    checked: false              # true ticks it by default in clients
    character_limit: 500
    include_permalink: true
    visibility: public          # public | unlisted | private
    service_name: Mastodon      # label in clients; e.g. GoToSocial (any Mastodon-API server works)
  # bluesky:
  #   handle: awfulwoman.com
  #   service: https://bsky.social
  #   checked: false
  #   include_permalink: true

```

Notes:

- **`template.mapping`, `template.omit` and `post_types` are the fallback
  copy.** At run time they come from the site repo's `data/micropub.yaml`
  ([06](06-post-templates.md#the-site-owns-the-shape)). The config key
  `site_config_path: data/micropub.yaml` (relative to the store) points at
  it. Set it to `null` to use only the local copy.
- `topics_path: data/topics.yaml` (relative to the store) points at the
  site's topic map ([15](15-topics.md)). Unset or unreadable: no topics.
- `post_types` is a keyed map, as in Indiekit ADR 0005. Each key is the type
  name used by post type discovery.
- `properties` and `required_properties` only feed `q=config` and
  `q=post-types`, so clients can build forms. Optionally enforce
  `required_properties` on create, with `400` naming the missing one.
- YAML because the infra repo prefers YAML over JSON over TOML. Indiekit's
  `indiekit.config.js` cannot be read directly. Translate it once by hand.
