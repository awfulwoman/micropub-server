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
- Backups: the infra ZFS dataset policy `low` is enough. The posts
  themselves live in Git. Losing the database loses tokens (clients sign in
  again), syndication state and undelete history.

## Importing existing posts (optional)

A CLI command, `micropub-server import`, would let `q=source` and updates
work on posts this server did not create:

1. Walk `content/` in the store (or a local checkout).
2. Parse the front matter.
3. Reverse the mapping in [06](06-post-templates.md) into JF2.
4. Work out `url` from the path.
5. Insert rows with `post_status` taken from `draft`.

Skip files that do not parse. Report a count per section.
