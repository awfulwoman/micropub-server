# 12 Indiekit divergences

Things Indiekit (v1.0.0-beta.31, upstream `5308a2f`) does that we do **not**
copy. Each is either a bug against the standards, a problem we hit in
production, or a choice that does not suit one Hugo site. Worth reporting the
bugs upstream.

## Bugs against the standards

| # | Indiekit behaviour | Where | Our behaviour |
| --- | --- | --- | --- |
| 1 | An update is rejected unless it has `replace`, `add` or **`remove`**. The spec's operation is `delete`, which the update code does handle, so a delete-only update returns 400. | `endpoint-micropub/lib/controllers/action.js` vs `lib/update.js` | Accept `replace`, `add`, `delete` |
| 2 | `addProperties` returns inside the loop, so only the **first** key of an `add` operation is applied | `endpoint-micropub/lib/update.js` | Apply every key |
| 3 | Authorization codes are stateless JWTs, valid for 10 minutes and usable **more than once**. OAuth 2.0 requires single use. | `endpoint-auth/lib/token.js` | Stored, hashed, single-use codes. Replay revokes the tokens issued from them. |
| 4 | The introspection endpoint needs no authorization. IndieAuth says it MUST. | `endpoint-auth/lib/controllers/introspection.js` | Requires a bearer token |
| 5 | No token revocation | | `POST /auth/revoke` (RFC 7009) |
| 6 | The token endpoint also answers `GET`, by mapping it to introspection | `endpoint-auth/index.js` | `POST` only |
| 7 | Unknown `q` returns 501. Micropub says 400 `invalid_request`. | `endpoint-micropub/lib/controllers/query.js` | 400 |
| 8 | A code issued with no scope (authentication only) can still be redeemed at the token endpoint | `endpoint-auth/lib/controllers/token.js` | `invalid_grant` |

## Problems we hit in production (2026-10-14)

| Problem | Our behaviour |
| --- | --- |
| No container image was published, so we had to build from an upstream commit | Publish our own image from CI |
| Store settings looked like env vars (`GITHUB_USER`, …) but only `GITHUB_TOKEN` is read. The container crash-looped with "repo not set". | One documented config file. Validate at startup and print the exact missing key. |
| A read-only GitHub token gave "GitHub store: ." with an empty message | Check push permission at startup. Store errors include status and body. |
| `/auth/new-password` is a public web form for setting the password | No such route. `hash-password` CLI, which also works without a TTY. |
| A failed store write left a post record in Mongo pointing at a file that did not exist | Write to the store first, then commit the record |
| `getpass` failed in a no-TTY shell when making the hash | The CLI reads stdin when there is no TTY |

## Design choices we change

| Indiekit | Ours | Why |
| --- | --- | --- |
| MongoDB | SQLite | One container, no extra service. Data is small. |
| Authorization request state in process memory, never cleaned up | `auth_requests` table with expiry | Survives restarts, no leak |
| Access tokens are 90-day JWTs | Opaque tokens, hashed, revocable, with `last_used_at` | Revocation and visibility |
| The Micropub endpoint introspects tokens over HTTP against itself | In-process lookup | Fewer moving parts |
| Multipart files re-POSTed over HTTP to its own `/media` | In-process media pipeline | Same |
| One commit per file | One commit per Micropub action (Git Data API) | One site build per post |
| Create silently overwrites a post at the same URL | Add `-2`, `-3` to the slug | Two posts in the same minute with `{HH}{mm}` paths |
| Hugo preset camel-cases keys (`bookmarkOf`) and writes `publishDate` and `uid` | Mapping set in config, snake_case defaults, no clutter | Match the existing site layouts |
| EXIF is stripped as a side effect of sharp re-encoding | Explicit strip, with a test fixture | Privacy: the `exif.sh` hook never runs for API commits |
| Plugin system (npm packages, `init()` hooks) | Plain modules | One site, one user |
| Localised UI in many languages | English only | Not needed |
| Its own UI signs in through its own IndieAuth flow | Password session cookie | Simpler for one user |
| Request host decides the application URL | `BASE_URL` env var | Predictable behind a proxy |

## Behaviour we keep on purpose

- JF2 as the stored post format (ADR 0003).
- Post type discovery treats `name` plus content as an article, without the
  PTD prefix check.
- Configured discovery properties (`bookmark-of`) are checked **after** the
  built-in ones, so a bookmark with a photo is a photo. Decided 2026-10-14.
  Our `discovery_first` and channel binding add exceptions for checkins and
  bandnames.
- `202 Accepted` on create, since the site still has to build.
- Optional PKCE, for older clients.
- Matching syndication targets by origin.
- Deferred syndication triggered by `/syndicate` after deploy.
- Endpoint paths (`/micropub`, `/media`, `/auth`, `/auth/token`,
  `/auth/introspect`, `/syndicate`), so the site `<head>` stays the same.
