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
      path: content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}.md
      url: notes/{yyyy}/{MM}/{dd}/{HH}{mm}/
  # …
```

The server's `config.yaml` keeps only what is about the server: the store,
secrets, syndication, media limits.

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

Key order: `title`, `date`, `lastmod`, `draft`, the type-specific keys
(`bookmark_of`, `basedomain`, `like_of`, …), `tags`, `images`,
`syndication`, then the rest in alphabetical order.

Indiekit also writes `publishDate` (a copy of `date`) and `uid`. **Leave
both out.** The test note on 2026-10-14 showed they are only clutter.

## Per-type defaults for awfulwoman.com

Store paths follow the newer `YYYY/MM/DD/HHMM` layout. URLs come from the
directory structure, because `hugo.yaml` only sets a permalink for `posts`.

| Type | `post.path` | `post.url` | Bundle? |
| --- | --- | --- | --- |
| article | `content/posts/{yyyy}/{slug}/index.md` | `posts/{slug}/` | yes |
| note | `content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `notes/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| bookmark | `content/bookmarks/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `bookmarks/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| like | `content/likes/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `likes/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| reply | `content/replies/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `replies/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| repost | `content/reposts/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `reposts/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| quote | `content/quotes/{yyyy}/{MM}/{dd}/{HH}{mm}.md` | `quotes/{yyyy}/{MM}/{dd}/{HH}{mm}/` | no |
| photo | `content/photos/{yyyy}/{MM}/{dd}/{HH}{mm}/index.md` | `photos/{yyyy}/{MM}/{dd}/{HH}{mm}/` | yes |
| checkin | `content/checkins/{yyyy}/{MM}/{dd}/{HH}{mm}/index.md` | `checkins/{yyyy}/{MM}/{dd}/{HH}{mm}/` | yes (photos from Swarm) |
| bandname | `content/bandnames/{yyyy}/{MM}/{slug}.md` | `bandnames/{yyyy}/{MM}/{slug}/` | no |

Two posts of the same type in the same minute get `-2` from the collision
rule in [03](03-micropub.md#create).

## How each in-scope type arrives

| Type | Sent by client as | Discovered by |
| --- | --- | --- |
| quote | `quotation-of=<url>` plus `content` (the quoted text). Optional `author` h-card. | `discovery: quotation-of` |
| checkin | `checkin` = h-card (`name`, `latitude`, `longitude`, `url`), as OwnYourSwarm sends | `discovery: checkin`, `discovery_first: true` |
| bandname | `name` only, with `mp-channel=bandnames`. The web UI's band name form sets this ([13](13-web-ui.md)). | channel binding (step 0 in [05](05-post-model.md#post-type-discovery)) |

A bandname file has front matter only, with no body, matching
`content/bandnames/2024/11/wifebiscuits.md`:

```markdown
---
title: Wife Biscuits
date: "2026-10-14T19:15:00+02:00"
---
```

Bandnames are titled, so use `{slug}` in their path, not `{HH}{mm}`.

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
