# 16 Link cards for bookmarks

A bookmark on awfulwoman.com shows a card for the page it points at: the
page's image, title, site name and description, as social media shows a
shared link.

The server finds these facts when the bookmark is saved and commits them with
the post, as it does for the target's author
([06](06-post-templates.md#facts-about-the-link-target)). The site never
fetches anything while it builds, and a card stays after the target page
is gone.

## What is saved

The new front matter keys go after `bookmark_of`, with `bookmark_author`:

```yaml
bookmark_of: 'https://www.snopes.com/fact-check/jack-black-mom/'
bookmark_author: Alex Kasprak
bookmark_site: Snopes.com
bookmark_description: >-
  Judith Love Cohen was a tenacious engineer. This fact check is just a tribute.
bookmark_image:
  file: card.webp
  alt: ''
  width: 800
  height: 419
```

| Key | JF2 property | Source, best first | Limit |
| --- | --- | --- | --- |
| `bookmark_site` | `bookmark-site` | `og:site_name`, then `application-name`, then the host without `www.` | 100 chars |
| `bookmark_description` | `bookmark-description` | `og:description`, then `twitter:description`, then `<meta name="description">` | 300 chars |
| `bookmark_image` | `bookmark-image` | `og:image` (`og:image:secure_url` first), then `twitter:image` | see below |

- The card's title is the bookmark's own `title`. The server does not save
  a second one.
- `alt` is `og:image:alt` or `twitter:image:alt`, else empty. The image sits
  next to the title, inside the same link, so an empty `alt` is correct.
- `width` and `height` are of the saved file, so the site can reserve the
  space and the page does not move while the image loads.
- An empty value is never written. A page with only a description gets a
  card with no image.
- Descriptions are often the start of the article. The text is cleaned like
  the other facts (whitespace folded, cut at a word with `…`).

These keys join `author` in the bookmark row of `KINDS` in `target_meta.py`.
`extract_page` reads them from the meta tags it already collects. The
`_Head` parser in the web UI's link preview (`/api/preview`) reads the same
tags. Merge the two parsers, so the preview and the saved card always agree.

## The image

The server downloads the image once and commits it into the bookmark's page
bundle. It never links to the image on the other site. The other site can
remove it, and a hotlinked image shows each reader's IP address to that site.

### Fetching

- Use the same guarded client as the page fetch: no private addresses, every
  redirect hop checked, `https` or `http` only. An image URL that is relative
  is made absolute against the page's final URL.
- At most **5 MB** (`media.card.max_bytes`). Stop reading when the limit is
  reached, and do not trust `Content-Length`.
- Sniff the type from the bytes (as in [04](04-media-endpoint.md)). Accept
  JPEG, PNG, WebP and GIF. Refuse SVG and everything else.
- Refuse images over 200 million pixels before decoding, and images smaller
  than 200 × 100. Small images are mostly site logos and tracking pixels.
- The image fetch shares the 12 second limit of the page fetch. If the time
  runs out, save the text facts without the image.

### Processing

With Pillow, as for uploads ([04](04-media-endpoint.md#upload-post-media)):

- Apply the EXIF orientation, then drop **all** metadata. The GPS fixture
  test covers card images too.
- Resize down to at most **800 px wide** (`media.card.max_width`). Never
  upscale. 800 px is two times the card's width on the page.
- Encode as **WebP**, quality 80 (`media.card.format`, `media.card.quality`).
  The first frame of a GIF only. The 53 images saved by the old `scraped/`
  importer average 190 KB as PNG and JPEG. WebP at 800 px should be about a
  quarter of that (check on those 53 first), which matters with 1,500 bookmarks in the site repo.
- The file name is **`card.webp`**. Do not use a name that has `cover`,
  `feature` or `thumbnail` in it: `get-featured-image.html` would then use
  the other site's image as the bookmark's own share card (see
  [The bookmark's own share card](#the-bookmarks-own-share-card)).

### Page bundles

The image needs a bundle, so bookmarks become bundles. In the site's
`data/micropub.yaml`, change the bookmark path to:

```yaml
post:
  path: content/bookmarks/{yyyy}/{MM}/{dd}/{slug}/index.md
  url: bookmarks/{yyyy}/{MM}/{dd}/{slug}/
```

The URL does not change: Hugo serves `x.md` and `x/index.md` at the same
place. All bookmarks get a bundle, also those with no image, so that a later
edit can add one without moving the post. Only 11 old bookmarks are flat
files today. The backfill moves them when it adds an image.

The post file and its image go in **one commit**, through the Git Data API
([07](07-content-stores.md#multi-file-changes-moves-photo-bundles)). The
message stays `create bookmark post`. The image gets a `media` row linked to
the post, as bundle attachments do ([10](10-data-model.md)), so a move or a
delete of the post finds it.

## When it runs

The rules are those of the other target facts
([06](06-post-templates.md#facts-about-the-link-target)), with these
additions:

- **Create:** the page is fetched once, and the text facts and the image come
  from that one response. The image fetch follows straight after.
- **Edit that changes `bookmark-of`:** drop the old `bookmark_site`,
  `bookmark_description` and `bookmark_image`, delete the old `card.webp` in
  the same commit, then fetch the new page.
- **Edit that does not change `bookmark-of`:** nothing is fetched. Keys a
  client sends are kept, as for the other target facts.
- **Delete `bookmark-image` in an edit** (`delete: [bookmark-image]`): the
  server deletes `card.webp` in the same commit. This is how to remove a bad
  image. The web UI's edit form shows the card with a "Remove image"
  checkbox, and a "Fetch the card again" button that runs the lookup on the
  current `bookmark-of`.
- `publication.fetch_target_meta: false` turns off the text facts and the
  image. `publication.fetch_target_image: false` turns off only the image.
- Nothing here ever fails a post. A page that does not answer or has no tags
  gives a bookmark with no card, as today.

## Backfill

A one-off job, run locally. New bookmarks get their card from the server on
create, so nothing runs on CI.

One command, `micropub-server backfill-cards`, in two modes, the same as for
the other target facts ([06](06-post-templates.md#backfilling-existing-posts)).
Both modes are resumable and safe to run again.

### 1. On a site checkout: `--checkout <path>` (preferred)

```
uv run micropub-server backfill-cards --checkout ../awfulwoman.com --step scraped --dry-run
uv run micropub-server backfill-cards --checkout ../awfulwoman.com --step live --before 2026-10-14 --limit 200
uv run micropub-server backfill-cards --checkout ../awfulwoman.com --step archive --before 2026-10-14
```

It edits files in place and makes no commits, no database writes and no
GitHub API calls. Review the diff (and the images) and commit after each step
or batch, as with `backfill_targets.py`. The steps:

1. **`scraped`: the old cards, with no network.** 55 bookmarks from 2021
   have `scraped/opengraph.json` and `scraped/files/<image>`. For each:
   write `bookmark_site` (from `siteName`) and `bookmark_description` into
   the front matter, process the image to `card.webp`, write
   `bookmark_image`, and delete the `scraped/` directory. One of these
   images is `cover.jpg`, which is the post's own share card today. After
   this step it is not.
2. **`live`: fetch each bookmarked page.** Every bookmark with no card keys
   and no `bookmark_card` marker is fetched as on create. A bookmark that
   already has its own `title` and `bookmark_author` keeps them: the command
   only adds keys.
3. **`archive`: the Wayback Machine for pages that are gone.** For each
   bookmark with `bookmark_card.result: gone`, ask
   `https://archive.org/wayback/available?url=<url>&timestamp=<bookmark date>`
   for the snapshot nearest the day it was bookmarked. Fetch the snapshot
   with the `id_` flag (`/web/<timestamp>id_/<url>`) to get the original
   page without the Wayback toolbar, and the image in the same way. Then
   process as in `live`. Add `bookmark_card_archived: <snapshot URL>` so the
   site can say where the card came from.

### What the command remembers

The result for each bookmark is kept **in the bookmark's own front matter**,
not in a separate state file. It is committed with the cards, so a run on any
machine, or the server mode below, skips the same posts:

```yaml
bookmark_card:
  result: gone        # gone | none
  checked: 2026-10-26
```

| Outcome | Written | Next run |
| --- | --- | --- |
| Card found | the card keys, no marker | skipped: it has card keys |
| **Gone:** DNS failure, connection refused, `404`, `410`, or a `200` with no title and no card tags (a parked domain) | `result: gone` | `live` skips it; `archive` tries it |
| **None:** the page answered but has no card tags, or `archive` found no snapshot or no tags | `result: none` | skipped by every step |
| **Retry:** timeout, `403`, `429`, `5xx` | nothing | tried again |

- A `403`, `429` or `5xx` is not gone. Many sites block scripts, and the
  archive copy may be worse than a later try.
- `archive` replaces `gone` with the card keys and `bookmark_card_archived`,
  or with `result: none`.
- **Skip** a bookmark that has any of `bookmark_site`,
  `bookmark_description` or `bookmark_image`. Never overwrite.
- `--recheck` ignores the marker for one run.
- The site does not show `bookmark_card`. It is a front matter extra, so it
  survives edits through the server ([06](06-post-templates.md)). An edit
  that changes `bookmark-of`, or "Fetch the card again" in the web UI,
  removes it.
- When it is done the command prints a count for each outcome and lists the
  retry URLs.

Options: `--dry-run`, `--limit N`, `--since YYYY`, `--before YYYY-MM-DD`,
`--delay` (default 1 second between live fetches, 4
seconds between Wayback requests, because the Wayback Machine blocks faster
clients), and `--no-image` to save only the text facts.

`--before` keeps the command off bookmarks the server created itself (from
the cut-over on 2026-10-14). `import` does not touch those, so the database
would not learn their new keys, and the next edit through the server would
write the file without them. Use server mode for them.

A flat bookmark (`x.md`) that gets an image is moved to `x/index.md`. Its URL
does not change.

After the commits are pushed, bring the server's database in step: copy the
checkout's `content/bookmarks` and `data/` into the container and run
`micropub-server import --source <copy> --type bookmark`. The import reads
the card keys and the marker into properties, and `card.webp` into a `media`
row. It makes no GitHub API calls.

### 2. Through the server: no `--checkout`

For bookmarks the server created itself. It runs the `live` and `archive`
steps on posts in the database, saving each post as an ordinary edit (one
commit through the GitHub API each, `lastmod` changes). It reads and writes
the same `bookmark_card` marker. Same options as `backfill-targets`
(`--dry-run`, `--delay`), plus `--step` and `--since`. Use it for a few
posts, not the whole site: the API commit limit applies.

## On the site

The awfulwoman.com repo owns how the card looks. This is the shape the keys
are made for.

A partial `bookmark-card.html`, used in `single.html` in place of the
"Bookmarked …" line when the post has a card, and in lists:

```html
<a class="c-link-card h-cite u-bookmark-of" href="{bookmark_of}">
  <img class="u-photo" src="card.webp" alt="" width="800" height="419" loading="lazy">
  <span class="p-name">{title}</span>
  <span class="p-publication">{bookmark_site}</span>
  <span class="p-summary">{bookmark_description}</span>
</a>
```

- HTML and CSS only, no JavaScript.
- Hugo makes the smaller sizes (`srcset`) from `card.webp` at build time.
- With no image, the card shows the text only. With no card keys at all,
  the page shows the plain "Bookmarked …" line, as today.
- `bookmark_card_archived` adds a line such as "Card from the Internet
  Archive, 3 May 2021".
- The `h-cite` markup sends the same facts with any Webmention to the
  bookmarked page.

### The bookmark's own share card

When someone shares the bookmark itself, the site sends the card it makes
itself (the title on `card-base.png`). It does not use `card.webp` as
`og:image`. The image belongs to the other site, and putting it on a share
card for awfulwoman.com is a step past showing it next to a link to its
source. To change this later, add `card.webp` to `get-featured-image.html`
after the bundle's own images.

## Configuration

```yaml
publication:
  fetch_target_meta: true          # already there; also turns off cards
  fetch_target_image: true         # download og:image into the bundle

media:
  card:
    max_bytes: 5242880
    max_width: 800
    format: webp
    quality: 80
```

## Integrations

| Repo | Change |
| --- | --- |
| awfulwoman.com | Bookmark `post.path` to `…/{slug}/index.md` in `data/micropub.yaml` |
| awfulwoman.com | Map `bookmark-site`, `bookmark-description`, `bookmark-image` to snake_case in `data/micropub.yaml` |
| awfulwoman.com | `bookmark-card.html` partial and CSS; use it in `single.html` and lists |
| micropub-server | Card facts in `target_meta.py`; one shared head parser with `/api/preview` |
| micropub-server | Image fetch and processing; bundle commit; delete on link change or `delete: [bookmark-image]` |
| micropub-server | `backfill-cards` CLI command, with `--checkout` mode; importer reads the keys, the marker and `card.webp` |
| micropub-server | Edit form: show the card, "Remove image", "Fetch the card again" |
| infra | `fetch_target_image` and `media.card` in `composition-micropub-server` config; new keys in its fallback mapping |

## Open questions

- **Repo size.** About 1,500 bookmarks at about 50 KB each is about 75 MB
  added to the site repo, once. If that is too much, keep cards only for
  bookmarks from a given year onwards (`--since`).
- **Likes.** Likes of articles (not toots) could use the same card. This
  spec is for bookmarks only. `KINDS` makes it a small change later.
