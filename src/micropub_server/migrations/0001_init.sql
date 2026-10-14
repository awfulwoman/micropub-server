CREATE TABLE posts (
  uid                TEXT PRIMARY KEY,
  url                TEXT NOT NULL UNIQUE,
  path               TEXT NOT NULL UNIQUE,
  post_type          TEXT NOT NULL,
  post_status        TEXT NOT NULL DEFAULT 'published',
  published          TEXT NOT NULL,
  updated            TEXT,
  deleted            TEXT,
  properties         TEXT NOT NULL,
  deleted_properties TEXT,
  source             TEXT NOT NULL DEFAULT 'micropub',
  created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX posts_published ON posts (published DESC, uid DESC);
CREATE INDEX posts_type_day ON posts (post_type, substr(published, 1, 10));

CREATE TABLE media (
  uid          TEXT PRIMARY KEY,
  url          TEXT NOT NULL UNIQUE,
  path         TEXT NOT NULL,
  media_type   TEXT NOT NULL,
  content_type TEXT NOT NULL,
  md5          TEXT NOT NULL,
  published    TEXT NOT NULL,
  post_uid     TEXT REFERENCES posts(uid) ON DELETE SET NULL
);

CREATE TABLE auth_requests (
  id          TEXT PRIMARY KEY,
  params      TEXT NOT NULL,
  client      TEXT,
  expires_at  TEXT NOT NULL
);

CREATE TABLE auth_codes (
  code_hash      TEXT PRIMARY KEY,
  client_id      TEXT NOT NULL,
  redirect_uri   TEXT NOT NULL,
  me             TEXT NOT NULL,
  scope          TEXT,
  code_challenge TEXT,
  expires_at     TEXT NOT NULL,
  used_at        TEXT
);

CREATE TABLE tokens (
  token_hash   TEXT PRIMARY KEY,
  code_hash    TEXT,
  client_id    TEXT NOT NULL,
  me           TEXT NOT NULL,
  scope        TEXT NOT NULL,
  issued_at    TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at   TEXT
);

CREATE TABLE cache (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE login_attempts (
  ip TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX login_attempts_ip ON login_attempts (ip, at);
