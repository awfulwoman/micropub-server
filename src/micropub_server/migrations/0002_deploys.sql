-- One row per commit the server makes for a post, so a finished site build can be matched to it.
CREATE TABLE deploys (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha         TEXT NOT NULL UNIQUE,
  post_uid    TEXT NOT NULL,
  action      TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending',
  run_url     TEXT,
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE INDEX deploys_post ON deploys (post_uid, id DESC);
