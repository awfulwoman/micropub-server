# 10 Data model

SQLite, one file at `$DATA_DIR/micropub.db`.

- WAL mode, `foreign_keys=ON`, `busy_timeout=5000`.
- Migrations are numbered SQL files applied at startup, with the version in
  `PRAGMA user_version`.

Indiekit uses MongoDB with two collections, `posts` and `media`, plus `keyv`
for caching. Its other state, such as authorization requests, lives only in
process memory. SQLite holds it all with no extra container.

## Tables

```sql
-- Published posts, stored as JF2 (Indiekit ADR 0003)
CREATE TABLE posts (
  uid                TEXT PRIMARY KEY,           -- UUIDv7
  url                TEXT NOT NULL UNIQUE,       -- absolute public URL
  path               TEXT NOT NULL,              -- store path
  post_type          TEXT NOT NULL,
  post_status        TEXT NOT NULL DEFAULT 'published',  -- published|draft
  published          TEXT NOT NULL,              -- ISO 8601 with offset
  updated            TEXT,
  deleted            TEXT,                       -- set when deleted
  properties         TEXT NOT NULL,              -- JF2 JSON (includes mp-* commands)
  deleted_properties TEXT,                       -- JF2 JSON kept for undelete
  source             TEXT NOT NULL DEFAULT 'micropub',  -- micropub|import (import rows are updated by re-importing, never overwrite micropub rows)
  created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX posts_published ON posts (published DESC, uid DESC);
CREATE INDEX posts_type_day ON posts (post_type, substr(published, 1, 10));
-- awaiting syndication
CREATE INDEX posts_syndicate ON posts (published)
  WHERE json_extract(properties, '$."mp-syndicate-to"') IS NOT NULL;

CREATE TABLE media (
  uid          TEXT PRIMARY KEY,
  url          TEXT NOT NULL UNIQUE,
  path         TEXT NOT NULL,
  media_type   TEXT NOT NULL,          -- photo|video|audio
  content_type TEXT NOT NULL,
  md5          TEXT NOT NULL,
  published    TEXT NOT NULL,
  post_uid     TEXT REFERENCES posts(uid) ON DELETE SET NULL  -- set when moved into a bundle
);

-- Validated /auth requests waiting for consent (the request_uri)
CREATE TABLE auth_requests (
  id          TEXT PRIMARY KEY,       -- uuid from request_uri
  params      TEXT NOT NULL,          -- JSON of the validated query
  client      TEXT,                   -- JSON {name, url, logo}
  expires_at  TEXT NOT NULL
);

CREATE TABLE auth_codes (
  code_hash      TEXT PRIMARY KEY,    -- sha256 of the code
  client_id      TEXT NOT NULL,
  redirect_uri   TEXT NOT NULL,
  me             TEXT NOT NULL,
  scope          TEXT,                -- NULL = authentication only
  code_challenge TEXT,
  expires_at     TEXT NOT NULL,
  used_at        TEXT                 -- single use
);

CREATE TABLE tokens (
  token_hash   TEXT PRIMARY KEY,      -- sha256 of the token
  code_hash    TEXT,                  -- revoke all if a code is replayed
  client_id    TEXT NOT NULL,
  me           TEXT NOT NULL,
  scope        TEXT NOT NULL,
  issued_at    TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at   TEXT
);

-- Remote fetch cache (categories, client info, h-card profiles)
CREATE TABLE cache (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE login_attempts (
  ip         TEXT NOT NULL,
  at         TEXT NOT NULL
);
```

## Rules

- `{n}` (the per-day counter for a post type) counts `posts` rows by
  `post_type` and local date, excluding the post itself. Indiekit counts
  across the whole collection on every render.
- Delete expired `auth_requests`, `auth_codes`, `cache` and
  `login_attempts` rows on startup and hourly.
- Never store raw tokens or codes. Only hashes.
- Snapshot consistency: ZFS snapshots of a live SQLite file in WAL mode
  are crash-consistent, which SQLite recovers from. That is enough for
  `low`. Also run `PRAGMA wal_checkpoint(TRUNCATE)` hourly, so the WAL file
  stays small and snapshots stay close to the main file.
- Backups: the infra ZFS dataset policy `low` is enough. The posts
  themselves live in Git. Losing the database loses tokens (clients sign in
  again), syndication state and undelete history.

## Import command

Built and checked against the real awfulwoman.com content (2,919 dated posts).

```
micropub-server import [--source PATH] [--dry-run] [--type NAME] [--check-urls]
```

- `--source PATH` reads a local checkout of the site repo (fast, and lets it hash photo
  attachments). Without it, files are listed and read through the store (the GitHub tree API,
  then one contents call per file).
- `--dry-run` reports and changes nothing. `--type bookmark` limits it to one post type.
- `--check-urls` then asks the **live site** for every imported, published URL and prints the
  ones that are not served.

How a file becomes a post:

1. **Type** comes from the section, using the static prefix of each post type's `post.path`
   (`content/notes/{yyyy}/...` gives `content/notes/`). The longest prefix wins. Markdown files
   outside every prefix (`about.md`, `guineapigs/`) are skipped and counted.
2. **Front matter** is parsed as YAML. A file with no `date`, or with invalid front matter, is a
   reported failure, and nothing else is affected.
3. **Reverse mapping** (inverse of [06](06-post-templates.md)): `title` is `name`, `date` is
   `published`, `lastmod` or `modified` is `updated`, `tags` is `category`, `description` is
   `summary`, the link keys (`bookmark_of`, `like_of`, `repost_of`, `in_reply_to`, `quote_of`)
   map back to their hyphenated names, `images` is `photo`, `draft: true` is
   `post-status: draft`, and `place_geo` is a `checkin` card for checkins or a `location` for
   everything else.
4. **Everything else is kept verbatim** in a `front-matter` property and written back exactly
   as found: `slug`, `strapline`, `aliases`, `menus`, `params`, `add_like`,
   `*_oembed_twitter` blobs, an existing `basedomain`, and any extra keys on photo entries such
   as `width` and `height`. Hugo layouts and permalinks depend on these, so a rewrite must never
   drop them.
5. **Dates** come in at least five shapes in the wild (`...Z`, `+0100` without a colon, a date
   only, YAML datetimes, a date object). All are parsed and stored in the site's time zone.
6. **URL**: if the file path matches the post type's `post.path` template, the URL comes from
   its `post.url` template with the captured tokens. Otherwise it is Hugo's default (the path
   without `content/`, `index.md` or `.md`, plus a trailing slash). The `{slug}` token is the
   front matter `slug` if there is one, else it depends on the type's **`import_slug`**:
   `path` (the file or folder name, the default) or `title` (the slugified title). Hugo's
   `/posts/:slug/` permalink falls back to the title, so articles need `import_slug: title`,
   while band names are served by filename. Getting this wrong gave seven wrong article URLs
   until `--check-urls` caught it.
7. **uid** is a UUIDv5 of the URL, so re-running is stable (this replaces the earlier idea of a
   UUIDv7 derived from the date).
8. **Bundle attachments** (photos, video and audio next to an `index.md`) get `media` rows
   linked to the post, so moving or syndicating the post finds them.
9. **Line endings** in post bodies are normalised to LF.
10. **Re-running** updates rows it created earlier and reports them `unchanged` when nothing
    differs. It never overwrites a row with `source = 'micropub'`; that is reported as a
    conflict.

### What a rewrite changes

Rendering all 2,918 imported posts back from the database and comparing them with the original
files differs only by: `modified` becoming `lastmod` (629 posts; Hugo's real key), trailing
whitespace trimmed from some titles and photo alt texts, and empty `tags:` dropped. Bodies match
exactly. The first edit of an old post therefore makes a small diff, and later edits are exact.

An edit never changes an imported post's type or URL by itself. A post's type only changes if
the edit changes what discovery says about it, so a note with a title (which discovery calls an
article) stays a note.
