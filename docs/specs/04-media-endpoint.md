# 04 Media endpoint

`/media`. `POST` uploads or deletes files. `GET` answers queries. Advertised
as `media-endpoint` in `q=config`. Needs a token with the `media` or `create`
scope. Delete needs `delete`.

## Upload: `POST /media`

- `multipart/form-data` with exactly one file part named `file`. Missing:
  `400 invalid_request`.
- Size limit: 20 MB by default (config `media.max_bytes`). Over it:
  `413 invalid_request`.

Steps:

1. **Sniff the real type** from the bytes (`filetype`). Do not trust the
   client's `Content-Type`. Unknown: `415`.
2. **Media type**: `image/*` is `photo`, `video/*` is `video` and `audio/*`
   is `audio`. Anything else: `415`.
3. That media type must have a `media.path` configured. Otherwise
   `501 not_implemented`.
4. **Process images** with Pillow:
   - Apply the EXIF orientation, then drop **all** metadata (EXIF, GPS, XMP,
     ICC except sRGB). Indiekit gets this as a side effect of sharp
     re-encoding. We do it on purpose. It replaces the `exif.sh` pre-commit
     hook, which never runs for commits made through the API.
   - Resize down to fit within `media.image.max_width` × `max_height`
     (default 2560 × 2560). Never upscale.
   - Re-encode in the same format. JPEG quality 85, progressive. Keep PNG
     and WebP as they are. Convert HEIC to JPEG if `pillow-heif` is
     installed.
   - **Never store an image with GPS data.** Test this with a fixture.
5. **File properties**:
   - `filename`: the slugified basename of the uploaded name plus the sniffed
     extension.
   - `ext`, `md5` (of the processed bytes), `content-type`, `media-type`.
   - `published`: now, in the configured time zone.
6. **Path tokens** (on top of the date tokens in
   [05](05-post-model.md#path-templates)): `{filename}`, `{ext}`, `{md5}`,
   `{D60}`, `{n}`. Render `media.path` (store path) and `media.url` (public
   URL, default the same as the path), and make the URL absolute against `me`.
7. If a file already exists at that path: when the `md5` matches, reuse it.
   Otherwise add `-2`, `-3`, … before the extension.
8. Write to the media store. The message is `upload {media-type} file`.
9. Save the media record with a UUIDv7 `uid`.
10. Respond `201 Created` with `Location: {url}` and
    `{"success": "create", "success_description": "Media uploaded to {url}"}`.

## Delete: `POST /media` with `action=delete&url=…`

This is a Micropub extension.

- Look up the record. Unknown: `400 invalid_request`.
- Delete it from the store. The message is `delete {media-type} file`.
- Remove the record.
- `200` with `{"success": "delete", …}`.

## Queries: `GET /media`

`q=source` with `url` or `uid` returns:

```json
{ "uid": "…", "url": "…", "content-type": "image/jpeg",
  "media-type": "photo", "published": "2026-10-14T19:10:00+02:00" }
```

Without `url`, it returns `{"items": [...], "paging": {...}}`, newest first.
It supports `limit`, `after` and `before`, and `filter` on the URL, as in the
media-source extension.

`q=last` (a common extension): the most recent upload. Useful for clients
that upload first and post afterwards.

## Photos with posts: page bundles

The site keeps photos inside a Hugo page bundle
(`content/photos/2019/6453/index.md` next to its image files). The media path
for photos needs the post's path, but uploads arrive **before** the post
exists. Two options:

1. **Upload to a holding area, move on create (recommended).**
   - Uploads go to `content/_uploads/{yyyy}/{MM}/{md5}.{ext}`. The `_` stops
     Hugo publishing it as a section; check the site config.
   - When a post's `photo` properties point at holding-area URLs, the create
     step moves each file into the post's bundle directory, rewrites the URLs
     to bundle-relative filenames, and writes the front matter as
     `images: [{file, alt}]` (see [06](06-post-templates.md)).
   - Do all of it in **one commit**: the Git Data API in
     [07](07-content-stores.md) allows that.
2. Upload straight to `static/media/{yyyy}/{MM}/…` and link by absolute URL.
   This is simpler, but breaks the page-bundle convention and Hugo image
   processing.

Phase 2 builds option 1.
