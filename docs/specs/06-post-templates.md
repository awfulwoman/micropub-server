# 06 Post templates

A post template turns JF2 properties into file content. We need one: Hugo,
with YAML front matter, shaped to what awfulwoman.com already uses.

Indiekit's Hugo preset is the starting point, but its output does not match
the site (see the end of this page).

## Input

The post's JF2 minus internal properties: no `mp-*`, `post-type`, `uid`, or
`slug`. `url` is not written either, because the file path decides the URL.

## Output

```
---
<YAML front matter>
---

<content.text>
```

- One blank line after the closing `---`, then the Markdown body, ending in
  a newline. The site's pre-commit hook enforces trailing newlines, and API
  commits skip that hook, so the server must get it right.
- No trailing whitespace.
- YAML: block style, keys in the order given below, strings quoted only when
  needed, `allow_unicode`. Write dates as quoted strings, because Hugo parses
  them either way.

## The site owns the shape

Paths, URL patterns and front-matter keys belong to the **site**, not the
server. They live in the site repo as **`data/micropub.yaml`** (the Hugo
`data/` directory, so layouts can read it too). The server:

- reads it through the store at startup, and again every `cache_ttl`, or
  when `/syndicate` or the admin "reload" button is hit after a deploy.
- falls back to the copy in its own `config.yaml` if the file is missing or
  invalid. It logs the reason and reports `site_config: fallback` on
  `/health`.
- uses the same file in reverse for `import` ([10](10-data-model.md#import-command)).

So when a layout change renames a key, the same commit in the site repo
updates `data/micropub.yaml`, and the server follows. The server and the
layouts cannot drift apart.

Shape of the file (the post-type and mapping blocks from
[09](09-configuration.md), nothing secret):

```yaml
# awfulwoman.com/data/micropub.yaml
version: 1
mapping:
  name: title
  published: date
  category: tags
  bookmark-of: bookmark_of
  # …
omit: [uid, slug, url, publishDate, channel]
post_types:
  note:
    name: Note
    post:
      path: content/notes/{yyyy}/{MM}/{dd}/{slug}.md
      url: notes/{yyyy}/{MM}/{dd}/{slug}/
  # …
```

The server's `config.yaml` keeps only what is about the server: the store,
secrets, syndication, media limits.

### Aliases

A post type may list `post.aliases`: extra URL paths, rendered like `post.url`,
that are written to the `aliases:` front-matter key (merged with any the
importer kept, no duplicates). awfulwoman.com uses it so that
`/posts/YYYY/MM/DD/<slug>/` redirects to the canonical `/posts/<slug>/`:

```yaml
article:
  post:
    path: content/posts/{yyyy}/{MM}/{dd}/{slug}/index.md
    url: posts/{slug}/
    aliases: ["/posts/{yyyy}/{MM}/{dd}/{slug}/"]
```

## Field mapping

The defaults below match awfulwoman.com today. They are the first contents of
`data/micropub.yaml`:

| JF2 property | Front matter key | Notes |
| --- | --- | --- |
| `name` | `title` | |
| `published` | `date` | `2026-10-14T19:10:00+02:00` |
| `updated` | `lastmod` | |
| `deleted` | `expiryDate` | only matters for undelete history |
| `post-status: draft` | `draft: true` | left out when published |
| `summary` | `description` | |
| `category` | `tags` | list |
| `bookmark-of` | `bookmark_of` | plus `basedomain`: the host of the URL without `www.` |
| `like-of` | `like_of` | |
| `repost-of` | `repost_of` | |
| `in-reply-to` | `in_reply_to` | |
| `quotation-of` | `quote_of` | |
| `photo` | `images` | `[{file, alt}]` with bundle-relative filenames (see [04](04-media-endpoint.md)) |
| `video` | `videos` | list of URLs |
| `audio` | `audio` | URL |
| `location` (geo) | `place_geo: {lat, lng}` | |
| `checkin` (h-card) | `place_name`, `place_geo` | from the h-card's `name`, `latitude`, `longitude`. With no `name` on the post, set `title: "Checked in at {place_name}"`, as the existing checkins do. |
| `syndication` | `syndication` | list of URLs |
| `visibility` | `visibility` | |
| `content.text` | body | not in the front matter |
| `front-matter` (a dict, set by the importer) | its keys, verbatim | slug, strapline, aliases, oEmbed blobs ... never dropped |
| anything else | the same key with `-` replaced by `_` | so nothing is silently lost |
| photo entry extras (`width`, `height`) | kept next to `file` and `alt` | |
| `basedomain` | passed through | **derived once, when a bookmark is created or its link changes**, never while rendering, so old bookmarks do not gain it on an unrelated edit |
| `like-title`, `like-author`, `like-text`, `like-oembed` (and `quote-…`; `bookmark-author`) | `like_title`, `like_author`, `like_text`, `like_oembed` … | **looked up once, when the post is created or its link changes** (see below). A value the client sent is never replaced. `*_text` and `*_oembed` are only kept when the target offers an embed. |

Key order: `title`, `date`, `lastmod`, `draft`, the type-specific keys
(`bookmark_of`, `basedomain`, `like_of`, `like_title`, …), `tags`, `images`,
`syndication`, then the rest in alphabetical order.

Indiekit also writes `publishDate` (a copy of `date`) and `uid`. **Leave
both out.** The test note on 2026-10-14 showed they are only clutter.

## Per-type defaults for awfulwoman.com

Store paths follow the newer `YYYY/MM/DD/HHMM` layout. URLs come from the
directory structure, because `hugo.yaml` only sets a permalink for `posts`.

| Type | `post.path` | `post.url` | Bundle? |
| --- | --- | --- | --- |
| article | `content/posts/{yyyy}/{MM}/{dd}/{slug}/index.md` | `posts/{slug}/` | yes |
| note | `content/notes/{yyyy}/{MM}/{dd}/{slug}.md` | `notes/{yyyy}/{MM}/{dd}/{slug}/` | no |
| bookmark | `content/bookmarks/{yyyy}/{MM}/{dd}/{slug}.md` | `bookmarks/{yyyy}/{MM}/{dd}/{slug}/` | no |
| like | `content/likes/{yyyy}/{MM}/{dd}/{slug}.md` | `likes/{yyyy}/{MM}/{dd}/{slug}/` | no |
| reply | `content/replies/{yyyy}/{MM}/{dd}/{slug}.md` | `replies/{yyyy}/{MM}/{dd}/{slug}/` | no |
| repost | `content/reposts/{yyyy}/{MM}/{dd}/{slug}.md` | `reposts/{yyyy}/{MM}/{dd}/{slug}/` | no |
| quote | `content/quotes/{yyyy}/{MM}/{dd}/{slug}.md` | `quotes/{yyyy}/{MM}/{dd}/{slug}/` | no |
| photo | `content/photos/{yyyy}/{MM}/{dd}/{slug}/index.md` | `photos/{yyyy}/{MM}/{dd}/{slug}/` | yes |
| checkin | `content/checkins/{yyyy}/{MM}/{dd}/{slug}/index.md` | `checkins/{yyyy}/{MM}/{dd}/{slug}/` | yes (photos from Swarm) |
| page | `content/{slug}.md` | `{slug}/` | no |

Two posts of the same type in the same minute get `-2` from the collision
rule in [03](03-micropub.md#create).

## How each in-scope type arrives

| Type | Sent by client as | Discovered by |
| --- | --- | --- |
| quote | `quotation-of=<url>` plus `content` (the quoted text). Optional `author` h-card. | `discovery: quotation-of` |
| checkin | `checkin` = h-card (`name`, `latitude`, `longitude`, `url`), as OwnYourSwarm sends | `discovery: checkin`, `discovery_first: true` |
| page | `name` and `content`, with `mp-channel=pages`. The web UI's page form sets this ([13](13-web-ui.md)). | channel binding (step 0 in [05](05-post-model.md#post-type-discovery)) |

A page sits at the site root, so `content/uses.md` is served at `/uses/`:

```markdown
---
title: Uses
date: "2026-10-14T19:15:00+02:00"
---

The kit I use.
```

Pages are titled, so use `{slug}` in their path, not `{slug}`. They are not listed by
date, so a site normally keeps them out of its feeds and indexes. The importer only takes
pages that have a `date` (the rest, such as `about.md`, are not posts) and only files
directly in `content/`.

## Examples

A note sent as `content=Hello&category[]=indieweb` at 19:10 Berlin time is
written to `content/notes/2026/10/14/1910.md`:

```markdown
---
date: "2026-10-14T19:10:00+02:00"
tags:
  - indieweb
---

Hello
```

A bookmark sent as
`bookmark-of=https://hackaday.io/project/1&name=Internet-Week Clock&content=Ridiculous`:

```markdown
---
title: "Internet-Week Clock"
date: "2026-10-14T19:12:00+02:00"
bookmark_of: https://hackaday.io/project/1
basedomain: hackaday.io
---

Ridiculous
```

## Gaps on the site side (not this server's job)

The layouts only read `bookmark_of`, `like_of`, `basedomain`, `images`,
`videos` and `audio`. Replies (`in_reply_to`), reposts (`repost_of`), quotes
(`quote_of`) and checkins (`place_*`) are written correctly but the site
**does not show** their targets. Track that in the awfulwoman.com repo.

`opengraph.html` uses `.Params.images` as a list of URL strings, but photo
posts store `[{file, alt}]`. Check how it renders before switching to
bundles.

## What Indiekit's Hugo preset does, for reference

- It camel-cases every key (`bookmarkOf`, `likeOf`, `inReplyTo`). The site
  uses snake_case, so these values are invisible to the layouts.
- It writes `date` and `publishDate`, `lastmod`, `expiryDate`, `draft`,
  `title`, and `images` (a list of photo URLs), then every other property
  as is, including `uid`, `category`, `syndication` and `location`.
- It puts the body after the front matter as `content.text`, falling back to
  `content.html`.
- It supports YAML, TOML and JSON front matter. We only need YAML.


## Facts about the link target

When a like, quote or bookmark is created, and when an edit changes its
`like-of`, `quotation-of` or `bookmark-of`, the server looks the target up
(`target_meta.py`) and saves what it learns in the front matter, so the site
never fetches anything while it builds. Unrelated edits do not look again, and
a changed link throws away the old page's values first.

| Kind | Saved |
| --- | --- |
| like, quote | `title`, `author`, `text`, `oembed` |
| bookmark | `author` (it already has its own title), and the link card: `site`, `description`, `image` ([16](16-link-cards.md)) |

How each is found, best source first:

- **title:** the page's h-entry `name`, then `og:title`, then `<title>`, with
  a trailing ` | Site name` removed. A post with no title of its own (a toot, a
  tweet) has none: its implied name is not a title.
- **author:** the h-entry's author h-card, then `<meta name="author">`, then
  JSON-LD. Never the site name.
- **oembed:** Twitter and X use `publish.twitter.com/oembed`; any other page
  that advertises `application/json+oembed` (Mastodon does) is asked there. The
  markup is cleaned: no scripts or event handlers, iframes only over https. The
  site adds the provider's script itself.
- **text:** the original words of the embedded post, from the oEmbed markup or
  the page's h-entry. Kept only alongside an `oembed`, so the page can show the
  words if the embed does not load.

The fetch uses the same guarded client as the link preview (no private
addresses, every redirect hop checked), a 12 second limit, and never fails a
post: if nothing is found the post is saved without the extra keys and the site
falls back to the bare URL. Set `publication.fetch_target_meta: false` to turn it off.

### Backfilling existing posts

Two ways, both skipping posts that already have any of these keys:

1. **In a site checkout (preferred for large runs).**
   `uv run python scripts/backfill_targets.py <checkout> --type like --dry-run`, then without
   `--dry-run`. It only inserts lines after the `like_of` / `quote_of` / `bookmark_of` line
   (`target_files.py`), so the result is a plain git diff, and `lastmod` is untouched. Commit and
   push it, then bring the server's database in step, or the next edit through the server would
   write the file without the new keys: copy the checkout's `content/<section>` and `data/` into the
   container and run `micropub-server import --source <copy> --type like`. That import makes no
   GitHub API calls.
2. **Through the server.** `micropub-server backfill-targets --type like --dry-run`, then without.
   Each post is saved as an ordinary edit (one commit through the GitHub API each, `lastmod`
   changes). Use it for posts the server created itself, which `import` will not touch ("a post
   already exists at this path").

The GitHub store shares its token's 5,000 requests an hour with anything else using that account.
`import` without `--source` reads every file through the API, so a whole-site import (about 3,000
files) can use the hour up and make every commit fail with HTTP 403 until it resets.
