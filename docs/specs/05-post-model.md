# 05 Post model

Inside the server, a post is a **JF2** object (Indiekit ADR 0003): a flat
dict where single values are scalars, lists are lists, and nested
microformats are dicts with a `type`. Convert at the edges:

- mf2 JSON in (Micropub create and update): convert to JF2.
- Form-encoded in: already flat, so build JF2 directly.
- `q=source` out: convert JF2 back to mf2.

## mf2 to JF2

For an mf2 item `{"type": ["h-entry"], "properties": {…}}`:

- `type`: drop the `h-` prefix from the first type (`entry`, `cite`, `card`,
  `event`).
- For each property:
  - a one-item list becomes a scalar, and a longer list stays a list.
  - a nested item `{type, properties}` is converted recursively.
  - `content` as `{"html": "…", "value": "…"}` becomes
    `{"html": "…", "text": "<value>"}`. A plain string stays a string, and
    normalisation fixes it up later.
  - `{"value": "…", "alt": "…"}` (photo with alt) becomes
    `{"url": "<value>", "alt": "…"}`.
- `mp-*` properties are kept as they are, then removed before rendering.

JF2 to mf2 is the reverse. Every value becomes a list, nested dicts with a
`type` become nested items, and `content.text` becomes `content.value`.

There is no maintained PyPI package for this, so write it as a small pure
function with tests built from the examples in the JF2 spec.

## Normalisation

Run on every create and update, in this order:

| Property | Rule |
| --- | --- |
| `published` | Missing: now. Present: parse ISO 8601. Output ISO 8601 with offset in the configured time zone, for example `2026-10-14T19:10:47+02:00`. Indiekit's `client` setting keeps the client string as sent. Support that as `time_zone: client`. |
| `name` | Trim. Empty: remove. |
| `content` | Always `{"text": md, "html": html}`. Text only: render HTML with markdown-it-py. HTML only: make text with markdownify. Both: keep as they are. |
| `summary` | Trim. |
| `category` | Always a list. Trim each, drop empties, fold case-insensitive duplicates onto the first spelling. An empty result removes the property. |
| `location` | `geo:lat,lon[,alt]` (RFC 5870) becomes `{"type": "geo", "latitude", "longitude", "altitude"?}`. An h-card or h-adr dict is kept. Anything unparseable is kept as given. |
| `photo` | Always a list of `{"url", "alt"?}`. `mp-photo-alt` (string or list) fills `alt` by index. **Guard against `mp-photo-alt` being shorter than `photo`, which crashes Indiekit.** URLs under `me` become relative paths. |
| `video`, `audio` | Always a list of `{"url"}`, made relative as for photo. |
| `slug` | See [Slugs](#slugs). |
| `channel` | If channels are configured: the `mp-channel` values that match configured UIDs, or else the first channel. Remove `mp-channel`. |
| `mp-syndicate-to` | Always a list of known target UIDs. |
| `syndication` | Always a list. |
| `post-status` | `published` or `draft`. Default `published`. |
| `visibility` | Pass through (`public`, `unlisted`, `private`). The template decides what to do with it. |

## Post type discovery

Based on the Post Type Discovery algorithm, with Indiekit's changes, checked
in this order:

0. **Explicit choice (our extension).** A post type wins outright when
   either:
   - `mp-channel` names a channel bound to it (`channel: bandnames` in its
     config), or
   - its `discovery` property is present **and** it has
     `discovery_first: true`.

   Bandnames need this. A bandname is only a `name`, which would otherwise
   be a note. Checkins use `discovery_first`, so a checkin with a photo
   (common from OwnYourSwarm) stays a checkin.
1. `type == "event"`: **event**.
2. A configured post type whose `h` value is not `entry` and equals `type`.
   This is for custom vocabularies.
3. The first matching property, in this order:

   | Property present | Type |
   | --- | --- |
   | `rsvp` | rsvp |
   | `repost-of` | repost |
   | `like-of` | like |
   | `in-reply-to` | reply |
   | `video` | video |
   | `photo` | photo |
   | a post type's configured `discovery` property, for example `bookmark-of` → bookmark, `checkin` → checkin, `quotation-of` → quote | that type |

   Configured discovery properties come **after** the built-in ones, as in
   Indiekit. So a bookmark that has a photo is typed `photo`. This was
   decided on 2026-10-14. Use `discovery_first` (step 0) for exceptions.
4. `children` is a non-empty list: **collection**.
5. Has `name` **and** content (or `summary`): **article**. This departs from
   the PTD spec, which compares `name` with the start of the content. The
   departure is deliberate, so a long note with an accidental `name` is still
   an article.
6. Otherwise: **note**.

The chosen type is saved as `post-type` but never written to front matter.

## Slugs

In order of preference:

1. `mp-slug`, if the client sent one.
2. The first 5 words of `name`.
3. The first 5 characters of `md5(published)`. This gives the random-looking
   `1fe47` seen on the test note.

A client-sent `slug` property is ignored, and an existing slug is kept unless `mp-slug` is given
(see update in [03](03-micropub.md#update)).

Then slugify: lower-case, ASCII-fold (`python-slugify`), and join with the
configured separator (default `-`). An empty result falls back to option 3.

On create, if the rendered path or URL is already taken, append `-2`, `-3`,
… (see [03](03-micropub.md#create)).

## Path templates

`post.path` (store path), `post.url` (public URL path) and `media.path` /
`media.url` are strings with `{token}` placeholders. They are rendered with
the post's `published` date **in the configured time zone**.

| Token | Example | Meaning |
| --- | --- | --- |
| `{y}` `{yyyy}` | 2026 | year |
| `{M}` `{MM}` | 9, 09 | month |
| `{MMM}` `{MMMM}` | Sep, September | month name (locale) |
| `{w}` `{ww}` | 1, 01 | week number |
| `{D}` `{DDD}` | 1, 001 | day of year |
| `{d}` `{dd}` | 1, 01 | day of month |
| `{h}` `{hh}` | 1, 01 | hour, 12-hour |
| `{H}` `{HH}` | 1, 01 | hour, 24-hour |
| `{m}` `{mm}` | 1, 01 | minute |
| `{s}` `{ss}` | 1, 01 | second |
| `{t}` | 1760462400 | Unix seconds |
| `{T}` | 1760462400000 | Unix milliseconds |
| `{D60}` | 5Ug | date in NewBase60 |
| `{n}` | 3 | 1 + the number of posts of this type already published that day |
| `{slug}` | `planning-security` | slug |
| `{uuid}` | `01a13bd3-…` | the post's `uid` (our extension) |
| `{channel}` | `notes` | channel UIDs joined by the slug separator |
| `{filename}` `{ext}` `{md5}` | | media only |

- Unknown tokens stay as written. **Log a warning at startup** when a
  configured template has an unknown token. That is the check we wanted when
  we doubted whether `{HH}{mm}` would work.
- `post.url` defaults to `post.path`.
- The final URL is `urljoin(me, url)`. Add a trailing slash when the URL has
  no extension, so it matches Hugo's pretty URLs.

## Canonical URLs

Use one helper everywhere (`me`, `client_id`, post URLs): lower-case scheme
and host, drop the default port, and give an empty path a trailing `/`.
