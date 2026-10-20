# 03 Micropub

One endpoint, `/micropub`. `GET` is for queries and `POST` is for actions.
Every request needs a valid token (see [02](02-indieauth.md)).

## POST: actions

### Choosing the action

- Form-encoded and multipart: the `action` field, or `?action=` in the query.
  Default `create`.
- JSON: the `action` key. Default `create`.
- Allowed: `create`, `update`, `delete`, `undelete`. Anything else:
  `400 invalid_request`.
- Every action except `create` needs `url`, from the body or the query.
  Missing: `400 invalid_request`.

### Scope check

| Action | Needs | Notes |
| --- | --- | --- |
| create | `create` (or the deprecated `post`) | `draft` scope also works, and forces `post-status: draft` |
| update | `update` | `draft` scope also works, but only on posts whose `post-status` is `draft` |
| delete | `delete` | |
| undelete | `create` | `draft` scope also works, and restores as draft |

Failure: `403` with `{"error": "insufficient_scope", "scope": "<needed>"}`.

### Create

Input formats:

1. **Form-encoded** (`application/x-www-form-urlencoded`).
   - `h=entry` is the type; default `entry`.
   - `name[]=a&name[]=b`, or a repeated key, becomes a list.
   - Drop the reserved keys `access_token`, `h`, `action` and `url` from the
     properties.
   - Indiekit decodes `+` as a space except in date-looking values. Do the
     same, but use the framework's standard form decoding first, so values
     are not decoded twice.
2. **Multipart** (`multipart/form-data`). Same as form-encoded, plus files in
   the `photo`, `video` and `audio` fields (`photo[]` too). Upload each one
   through the media pipeline ([04](04-media-endpoint.md)) **in-process**, and
   append the resulting URL to that property. Indiekit re-POSTs them to its
   own `/media` over HTTP.
3. **JSON** (`application/json`), in mf2 form:
   `{"type": ["h-entry"], "properties": {"content": ["…"]}}`. Convert to JF2
   (see [05](05-post-model.md)). Nested mf2 objects, such as an h-cite in
   `like-of` or an h-card in `checkin`, become nested JF2 objects. The
   `{"html": "…"}` content form is kept.

Steps:

1. Parse to JF2. Throw away any client-sent `uid`. Indiekit does this so a
   client cannot influence numbering.
2. Optional "enrich": fetch referenced URLs (`like-of`, `repost-of`,
   `bookmark-of`, `in-reply-to`) and attach their h-entry as `references`.
   Off by default (config `publication.enrich_post_data`).
3. Resolve `mp-syndicate-to`: keep only the UIDs of configured targets.
   Accept a string, a list, or an empty list or object, which is what an
   unticked list sends. An empty object crashed Indiekit until it was fixed.
4. Normalise the properties ([05](05-post-model.md)).
5. Post type discovery. If the type has no configured `post.path`, return
   `501 not_implemented` with a message naming the type.
6. Render the path and URL templates. `url` is absolute, relative to `me`.
7. Set `post-status`: `draft` in draft mode, otherwise the client value, or
   `published`.
8. Assign `uid`, a UUIDv7. If a post with the same URL already exists, keep
   its `uid` and overwrite it. That matches Indiekit, but see the collision
   note below.
9. Save the post record ([10](10-data-model.md)).
10. Render the file ([06](06-post-templates.md)) and write it to the store
    with the commit message `create {post-type} post`.
11. Respond `202 Accepted` with `Location: {url}` and
    `{"success": "create_pending", "success_description": "Post will be created at {url}"}`.
    It's 202 rather than 201 because the site still has to build.

**Collision rule (we differ from Indiekit).** Indiekit silently overwrites a
post whose rendered URL matches an existing one. That happens, for example,
with two untitled notes in the same minute under an `{HH}{mm}` path. Instead:
if the path or URL exists in the database **or** the path exists in the store,
append `-2`, `-3`, … and check again. Never overwrite on create. The suffix goes
on the filename (`1910.md` becomes `1910-2.md`) or, for a page bundle
(`…/index.md`), on the bundle directory (`hello/` becomes `hello-2/`), and on the
last URL segment. It cannot go on the slug, because paths like `{HH}{mm}.md`
contain no slug.

**Ordering and failure.** Write to the store first. Commit the database
record only after the store write succeeds. If the store fails, return
`502`. Use the store's error text and keep the GitHub status code. Indiekit
returned an empty message for a `403`, which hid the token-permission
problem.

### Update

JSON only (form-encoded updates are rejected with 400). Body, following the Micropub spec:

```json
{
  "action": "update",
  "url": "https://awfulwoman.com/notes/2026/10/14/1910/",
  "replace": { "content": ["new text"] },
  "add": { "category": ["indieweb"] },
  "delete": { "category": ["old"] }
}
```

- `delete` can be a list of property names, which removes the whole properties, or an object of
  `{property: [values]}`, which removes those values only.
- Every value in `replace`, `add` and a `delete` object must be a list, otherwise 400.
- At least one of `add`, `replace` and `delete` is required. (Indiekit checks for `remove`, so
  a delete-only update is rejected, and its `add` stops after the first key. Both are bugs
  here.)
- Operations apply in the order add, replace, delete, and **every** key is applied.
- `uid`, `url`, `post-type` and `type` cannot be changed by a client.
- **A slug is fixed once chosen.** Editing a title does not move the post. Only `mp-slug` in
  an update changes the slug, and that moves the post.
- **A type is fixed unless the edit changes what discovery says, leaving the title out.**
  Adding a photo to a note makes it a photo post and moves it. Adding, changing or removing a
  title never does: on this site notes may have titles, so a title alone never turns a note
  into an article or back.
- **Front matter added to the file outside the server is kept.** Before applying the edit,
  the current file is read and any key it has that the database lacks (a title, `aliases`, a
  `guid` added by hand or by a script) is added to the post. The database wins where both have
  a value; aliases are combined. Without this, the next save would render from the stale
  database copy and drop them (this happened on 2026-10-20, see the commit history).
- The `channel` is kept unless `mp-channel` is given.
- `post-status` can be replaced (`draft` to `published` publishes a draft).
- The post is rendered again. If the file would be identical and nothing about its place
  changed, the response is `200` with
  `"Post at {url} not updated as no properties changed"` and nothing is committed. If only
  server-side commands changed (`mp-syndicate-to`), they are saved without a commit.
- Otherwise `updated` is set and the file is committed. If the **slug, publication time or
  type** changed, the post moves (path and URL are rendered again, with `-2`, `-3` ... if
  taken). A page bundle moves together with its photos, in the same commit. A move returns
  `201` with the new `Location`; an in-place edit returns `200`. A published post that moves
  keeps its old URL path in `aliases`, so links to it still work.
- With the `draft` scope only posts that are drafts can be edited, and they stay drafts.
  Anything else is `403 insufficient_scope`.
- Unknown URL: `400 invalid_request`. A deleted post cannot be updated.
- A store failure is `502 store_error` and the database is left as it was.

### Delete

1. Load the post record. Not found: `400 invalid_request`. Already deleted: `200`, nothing to
   do.
2. Delete the post's file in one commit (`delete {post-type} post`). **Photos in its bundle are
   kept**, so an undelete can bring the post back whole.
3. Keep the full properties in `deleted_properties`. Reduce the live properties to the ones needed
   to rebuild the path (`post-type`, `published`, `slug`, `type`, `uid`, `url`), plus `deleted`.
4. `200` with `{"success": "delete", "success_description": "Post deleted from {url}"}`. Deleted
   posts are hidden from `q=source`.

### Undelete

1. The post must be deleted, else `400`.
2. Restore the properties (`post-status: draft` in draft mode), render the file again and commit
   it at its original path (`undelete {post-type} post`).
3. `200` with `Location: {url}` and `{"success": "delete_undelete", ...}`.

## GET: queries

`q` is required. Missing: `400 invalid_request`. Unknown: `400 invalid_request`
(the Micropub spec). Indiekit returns `501`.

Common optional parameters: `filter` (case-insensitive substring), `limit`,
`offset`, and for lists `after` and `before` (opaque cursors).

### `q=config`

```json
{
  "media-endpoint": "https://indie.awfulwoman.com/media",
  "syndicate-to": [ { "uid": "…", "name": "…", "checked": true,
                      "service": { "name", "url", "photo" },
                      "user": { "name", "url", "photo" } } ],
  "post-types": [ { "type": "note", "name": "Note",
                    "h": "entry", "properties": ["content", "category"],
                    "required-properties": ["content"] } ],
  "categories": ["…"],
  "channels": [ { "uid": "notes", "name": "Notes" } ],
  "q": ["category", "channel", "config", "media-endpoint", "post-types",
        "source", "syndicate-to"]
}
```

`categories` is the configured list, or the result of fetching a configured
JSON URL. Cache it for `cache_ttl` (default 7 days). A good source is a Hugo
output of all tags (`/tags/index.json`).

### Single keys

`q=syndicate-to`, `q=media-endpoint`, `q=post-types`, `q=category` (alias of
`categories`) and `q=channel` (alias of `channels`) each return
`{ "<key>": <the same value as in config> }`. Apply `filter`, then
`offset`/`limit`, to lists.

### `q=source`

With `url` (or `uid`, an Indiekit extension):

- Return the post as mf2: `{"type": ["h-entry"], "properties": {…}}`. Every
  value is a list, nested JF2 objects become nested mf2, and
  `content.text` becomes `content[0].value` alongside `html`.
- `properties[]=name&properties[]=content` returns only those properties, and
  then **without** `type` (Micropub spec).
- Unknown `url`: `400 invalid_request`. Unknown `uid`: `404`.

Without `url` (the post list extension):

```json
{ "items": [ { "type": ["h-entry"], "properties": { … } } ],
  "paging": { "after": "<cursor>", "before": "<cursor>" } }
```

- Newest first, by `published` then `uid`.
- Filters: `category=<exact tag>`, `filter=<substring>` (matches `name` and
  `content.text`), `post-type=<type>` and `post-status=<draft|published>` (our extensions).
- `limit` defaults to 40, with a maximum of 100.
- Cursors are opaque, for example base64 of `published|uid`.

## Errors

Same JSON shape as [02](02-indieauth.md):
`{"error": "…", "error_description": "…"}`.

| Case | Status | `error` |
| --- | --- | --- |
| Bad or missing parameters, unknown post | 400 | `invalid_request` |
| No or invalid token | 401 | `unauthorized` |
| Token for another `me` | 403 | `forbidden` |
| Scope missing | 403 | `insufficient_scope` |
| Post type not configured | 501 | `not_implemented` |
| Unsupported media type | 415 | `invalid_request` |
| Store failed | 502 | `store_error` (our extension) |
