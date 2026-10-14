# 08 Syndication

Cross-post published posts to other networks (POSSE), then record the copies
in the post's `syndication` property. Phase 4.

## Targets

A target has:

```python
class Target(Protocol):
    uid: str            # e.g. "https://indieweb.social/@awfulwoman"
    name: str           # "@awfulwoman on indieweb.social"
    checked: bool       # ticked by default in clients
    service: dict       # {"name", "url", "photo"}
    user: dict          # {"name", "url", "photo"}

    async def syndicate(self, post: dict, publication: Publication) -> str | None:
        """Return the URL of the copy, or None if not syndicated."""
```

`q=syndicate-to` lists `uid`, `name`, `checked`, `service` and `user`. Make
`service.photo` an absolute URL on this server.

Match a requested `mp-syndicate-to` value to a target by **origin**, as
Indiekit does. So `https://indieweb.social/@awfulwoman` and
`https://indieweb.social/` both select the Mastodon target.

## When it runs

Indiekit never syndicates during create. The post must be live before a
copy can link to it. Instead:

1. Create saves `mp-syndicate-to` on the post record. It is never written to
   the file.
2. Something calls `POST /syndicate`:
   - with `?source_url=<post url>`: that post.
   - with no URL: the **oldest** published (not draft) post that still has
     `mp-syndicate-to`.
3. For each requested target whose origin is not already in `syndication`,
   call `syndicate()`.
4. Update the post internally, as a Micropub update. Add the returned URLs
   to `syndication`. Remove `mp-syndicate-to` if every target succeeded,
   otherwise keep only the failed ones. This changes the file, so it makes
   one more commit.
5. Respond `200` with
   `{"success": "OK", "success_description": "Post updated at {url}"}`, or
   `{"success": "OK", "success_description": "No posts awaiting syndication"}`.
   With `redirect_uri`, redirect there instead. The admin UI uses that.

### Triggering `/syndicate`

Indiekit expects a webhook after deploy. For us:

- The awfulwoman.com GitHub Actions workflow adds a last step after rsync:
  `curl -X POST https://indie.awfulwoman.com/syndicate -H "Authorization: Bearer $SYNDICATE_TOKEN"`.
- That token is an access token with only the `update` scope, kept as a repo
  secret.
- Also accept a JWT `HS256` webhook signed with `WEBHOOK_SECRET`, in the
  `x-webhook-signature` header, as Indiekit does for Netlify. Optional.

Before syndicating, check the post's public URL returns `200` (it is live).
If not, skip it and leave `mp-syndicate-to` for the next trigger.

## Mastodon

Settings: `url` (instance), `user`, `checked`, `character_limit` (default 500),
`include_permalink` (default true), `visibility`. The token is the `MASTODON_ACCESS_TOKEN`
environment variable. **Built on httpx directly**, not Mastodon.py: it is synchronous and
built on `requests`, and the three calls needed (status, favourite/reblog, media) are small.
Every status carries the post's `uid` as an `Idempotency-Key`, so a retry never double-posts.

| Post | Action on Mastodon |
| --- | --- |
| `like-of` a status on the same instance | Favourite it. Return the original status URL. |
| `repost-of` a status on the same instance | Boost it. Return the original status URL. |
| `repost-of` with content | A status with the text and the URL. |
| `in-reply-to` a status on the same instance | A status replying to that status id. |
| `in-reply-to` elsewhere | Skip (return None), so it does not show as an orphan. |
| has `name` (article) | A status `"{name} {url}"`. |
| otherwise | A status from the content: HTML to plain text, links kept, shortened to the character limit with `…`, plus `\n\n{url}` when `include_permalink`. |
| `photo` | Upload up to 4 images with alt text, attached to the status. |
| `category` | Add hashtags, CamelCased (`self hosting` becomes `#SelfHosting`), deduplicated, within the limit. |

The status ID for like, boost and reply is the last path segment of the
target URL.

## Bluesky

Settings: `handle`, `password` (app password, env `BLUESKY_PASSWORD`),
`service` (default `https://bsky.social`), `checked`,
`include_permalink`. Library: atproto.

| Post | Action on Bluesky |
| --- | --- |
| `like-of` a bsky.app URL | `like(uri, cid)` |
| `repost-of` a bsky.app URL, no content | `repost(uri, cid)` |
| `repost-of` with content | Quote post (`app.bsky.embed.record`, or `recordWithMedia` with photos). |
| `in-reply-to` a bsky.app URL | Reply, with `root` and `parent` refs. |
| otherwise | A post with rich text (link and hashtag facets), 300 graphemes, permalink appended. |
| `photo` | Up to 4 images. Upload each as a blob. Resize to under 1 MB first. |
| bookmark or article | Add an external link card (`app.bsky.embed.external`) for the post URL. |

The returned URL is `https://bsky.app/profile/{handle}/post/{rkey}`.

## Failures

A failed target never fails the request. Log it, keep it in
`mp-syndicate-to`, and include it in the response:
`{"failed": ["https://bsky.app/"]}`.

A post that **cannot be mirrored** on a target (a like or reply aimed at another site) is
dropped from the queue without a copy, rather than failing forever.

## As built

- `POST /syndicate` needs a bearer token with the `update` scope (the optional signed webhook
  is not built). `?source_url=` (or `{"syndication": {"source_url": ...}}`) names a post.
- `syndication.require_live` (default true) fetches the post's public URL first and leaves the
  post queued if it is not served yet.
- The copy is recorded through the same path as an update, so it is one commit.
- Bluesky is not built yet.
