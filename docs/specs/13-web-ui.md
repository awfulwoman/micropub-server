# 13 Web UI

A small website on `indie.awfulwoman.com` for writing, editing and managing
posts in a browser, without a separate Micropub app. Phase 5.

Indiekit's equivalents are `endpoint-posts` (`/posts`), `endpoint-share`
(`/share`) and `endpoint-files` (`/files`).

## Principles

- **One code path.** Every form submits to the same internal functions as
  Micropub (`micropub.create`, `update`, `delete`, `undelete`, and
  `media.upload`), called in-process with a scope that allows everything.
  The UI never writes files or database rows itself. A post made in the
  browser is the same as one made from Quill.
- **Plain HTML and CSS first.** Every page works with JavaScript turned off.
  Forms POST and redirect: POST, then redirect, then GET.
- **Small JS enhancements** on top, all optional:
  - photo previews and drag-and-drop
  - tag autocomplete from `q=category`
  - a live Markdown preview
  - a character counter when Mastodon cross-posting is ticked
- Server-rendered with Jinja2. One stylesheet, no build step, no framework.
  Must work on a phone.
- English only.

## Sign-in

- `GET /login`: a password form. `POST /login`: check the password (the
  same hash and rate limit as consent in [02](02-indieauth.md)). Then set
  the session cookie and redirect to `?next=` (a same-origin path only) or
  `/posts`.
- `POST /logout`: clear the session.
- Every UI route needs a session. Without one, redirect to
  `/login?next=<path>`.
- Session cookie: `itsdangerous`-signed with `SECRET`, `HttpOnly`, `Secure`,
  `SameSite=Lax`, 30 days.
- CSRF: a per-session token in a hidden field on every POST form, checked
  on every POST. Failure: `403`.
- The Micropub, media and auth endpoints ignore the session cookie. They
  only take bearer tokens. The two kinds of auth never mix.

## Pages

| Route | Page |
| --- | --- |
| `GET /` | Redirect to `/posts` when signed in, otherwise a short "this is a Micropub server" page with the endpoint links |
| `GET /posts` | Posts list |
| `GET /posts/new` | Choose a post type |
| `GET /posts/new/{type}` | Form for that type |
| `POST /posts/new/{type}` | Create |
| `GET /posts/{uid}` | One post: rendered preview, front matter, links |
| `GET /posts/{uid}/edit` | Edit form, filled in |
| `POST /posts/{uid}/edit` | Update |
| `GET, POST /posts/{uid}/delete` | Confirm, then delete |
| `GET, POST /posts/{uid}/undelete` | Confirm, then undelete |
| `POST /posts/{uid}/syndicate` | Run syndication for this post now |
| `GET /share` | Bookmarklet target: the bookmark form, filled in |
| `GET /files` | Uploaded media list |
| `GET /files/upload`, `POST /files/upload` | Upload on its own, then copy the URL |
| `GET, POST /files/{uid}/delete` | Confirm, then delete |
| `GET /tokens` | Issued access tokens, with revoke buttons |
| `POST /tokens/{id}/revoke` | Revoke one |
| `GET /status` | Health, store check, token permission check, version |

### Posts list: `/posts`

- Newest first, 40 per page, with cursor paging. Uses the same query as
  `q=source` ([03](03-micropub.md#q=source)).
- Each row shows:
  - the post type icon and name
  - the title, or the first 80 characters of the content
  - the date
  - a status badge: draft, deleted, waiting to cross-post, or imported
  - a link to the live URL
- Filters (GET parameters, so they can be bookmarked):
  - type
  - tag
  - status (published, draft, deleted)
  - free-text search on name and content
- A "New post" button.

### Choose a type: `/posts/new`

A grid of the configured post types, in config order, each with its name and
a one-line description. Types without a `post.path` are hidden.

### Forms per type

Each form is built from the type's `properties` list in the config
([09](09-configuration.md)). Required fields come from
`required_properties`. Field widgets:

| Property | Widget |
| --- | --- |
| `name` | text input. For a bandname this is the only field. |
| `content` | textarea for Markdown, with an optional live preview |
| `summary` | short textarea |
| `category` | text input, comma-separated, with tag autocomplete |
| `bookmark-of`, `like-of`, `repost-of`, `in-reply-to`, `quotation-of` | URL input. On blur (JS), fetch the page title through `GET /api/preview?url=` and fill `name` for bookmarks. |
| `photo` | file input (multiple), plus an alt text field per photo. Required. |
| `location` | latitude and longitude, with a "Use my location" button (JS, Geolocation API) |
| `checkin` | place name, latitude and longitude, with the same location button |
| `author` (quote) | name, plus an optional URL |
| `published` | `datetime-local`, hidden under "More options". Empty means now. |
| `mp-slug` | text, under "More options" |
| `mp-syndicate-to` | checkboxes, one per target, ticked when `checked` |
| `post-status` | two buttons: "Publish" and "Save as draft" |

Every form shows the final path and URL before submit, as a live preview
(JS), rendered from the path template with the current time.

### After submit

- **Create:** redirect to `/posts/{uid}` with a notice: "Committed. Live at
  {url} once the site builds." Include a link to the GitHub Actions run if
  it can be found (the latest run on `main` after the commit).
- **Errors:** show the form again with the values kept, a message at the
  top, and the message next to each bad field. Status `422`.
- **Store failure:** show the form again with the store's error text, as in
  [07](07-content-stores.md). Nothing is lost.

### Editing

- The edit form loads the post's JF2 and fills the fields.
- On submit, compare it with the stored post and send only the changes as a
  Micropub update (`replace` for changed fields, `delete` for cleared
  fields). So "no changes" gives "Nothing to update", not an empty commit.
- Imported posts can be edited too. A notice warns: "The first edit
  rewrites the front matter in the server's format." See
  [10](10-data-model.md#import-command).

### Share bookmarklet: `/share`

- `GET /share?url=…&name=…&content=…` opens the bookmark form with those
  values filled in. `content` is the selected text, saved as a quote in the
  Markdown.
- The `/posts/new` page shows the bookmarklet as a link to drag:

  ```js
  javascript:location.href='https://indie.awfulwoman.com/share?url='+encodeURIComponent(location.href)+'&name='+encodeURIComponent(document.title)+'&content='+encodeURIComponent(getSelection())
  ```

- Also a Web Share Target in the web app manifest (`share_target`, GET
  method, mapping `url`, `title` and `text`). Once installed on a phone, the
  server shows up in the share sheet.
- When signed out, it goes through `/login?next=/share?...` and keeps the
  values.

### Files: `/files`

A grid of uploads (thumbnails for photos), newest first, each with:

- its URL and a copy button
- the post it belongs to, if any
- a delete button

Deleting a file that a post uses needs a second confirmation that names
the post.

### Tokens: `/tokens`

A table of active tokens: client name and logo, scope, issued, last used,
expires. Each has a revoke button. Revoked and expired tokens are shown
greyed out for 30 days, then removed.

## Helper endpoint

`GET /api/preview?url=` (needs a session) fetches the URL with the SSRF guard
from [11](11-deployment.md) and returns
`{"name", "author", "site", "summary"}`. Used to fill in bookmark and quote
forms. Cached in the `cache` table.

## Security

- CSRF on every POST. `SameSite=Lax` cookies.
- `Content-Security-Policy: default-src 'self'; img-src 'self' https: data:;`
  No inline scripts. The bookmarklet is the only `javascript:` URL, and it
  is a link the user drags, not one the page runs.
- Escape all user content. Rendered Markdown previews go through an
  allowlist sanitiser (`nh3`).
- `next=` redirect targets must be same-origin paths.

## Phase plan

- **5a:** login, the posts list, `/posts/new` with forms for note, article,
  bookmark and bandname, the post page.
- **5b:** the remaining forms (photo with upload, checkin, quote, like,
  reply, repost), edit, delete and undelete.
- **5c:** `/share` and the bookmarklet, the Web Share Target, `/files`,
  `/tokens`, `/status`.
