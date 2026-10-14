# 01 Standards

Every behaviour in these specs traces to one of these documents. If a spec
here and a standard disagree, the standard wins. Fix the spec.

| Standard | URL | Use |
| --- | --- | --- |
| Micropub (W3C Recommendation) | https://www.w3.org/TR/micropub/ and https://micropub.spec.indieweb.org | Create, update, delete, query, media endpoint |
| Micropub extensions | https://github.com/indieweb/micropub-extensions | Query extensions, `mp-*` commands, delete media |
| IndieAuth (Living Standard) | https://indieauth.spec.indieweb.org | Identity, authorization, tokens |
| OAuth 2.0 (RFC 6749) | https://www.rfc-editor.org/rfc/rfc6749 | Base protocol under IndieAuth |
| PKCE (RFC 7636) | https://www.rfc-editor.org/rfc/rfc7636 | `S256` code challenges |
| OAuth Token Introspection (RFC 7662) | https://www.rfc-editor.org/rfc/rfc7662 | Introspection endpoint shape |
| OAuth Token Revocation (RFC 7009) | https://www.rfc-editor.org/rfc/rfc7009 | Revocation endpoint |
| OAuth Authorization Server Metadata (RFC 8414) | https://www.rfc-editor.org/rfc/rfc8414 | `/.well-known/oauth-authorization-server` |
| OAuth Pushed Authorization Requests (RFC 9126) | https://www.rfc-editor.org/rfc/rfc9126 | Indiekit borrows the `request_uri` idea internally (see 02) |
| microformats2 parsing | https://microformats.org/wiki/microformats2-parsing | mf2 JSON shape |
| JF2 | https://jf2.spec.indieweb.org | Internal post representation |
| Post Type Discovery | https://ptd.spec.indieweb.org | Choosing the post type |
| Geo URI (RFC 5870) | https://www.rfc-editor.org/rfc/rfc5870 | `location=geo:…` |
| NewBase60 | https://tantek.pbworks.com/w/page/19402946/NewBase60 | `{D60}` path token |

## Conformance targets

### IndieAuth

- [ ] Discovery through `rel="indieauth-metadata"` and the legacy
  `authorization_endpoint` and `token_endpoint` links
- [ ] Server metadata document
- [ ] Client information discovery (h-app, or a JSON client metadata document)
- [ ] `redirect_uri` checked against the client, or the client's declared
  `rel="redirect_uri"`
- [ ] Authorization code flow, PKCE `S256`. PKCE optional for older clients
- [ ] Profile URL response (redeem a code at the authorization endpoint)
- [ ] Access token response, with `profile` scope data
- [ ] Single-use, short-lived codes (Indiekit fails this, see 12)
- [ ] Token introspection that requires authorization (Indiekit fails this)
- [ ] Token revocation (Indiekit lacks it)
- [ ] `iss` parameter on the redirect
- [ ] Error responses in OAuth 2.0 format

### Micropub

- [ ] Create, form-encoded and JSON
- [ ] Create, multipart with file uploads
- [ ] Update: `replace`, `add`, `delete` (Indiekit breaks `delete`, see 12)
- [ ] Delete and undelete
- [ ] `q=config`, `q=source` (all or chosen properties), `q=syndicate-to`
- [ ] Extensions: `q=category`, `q=channel`, `q=post-types`, `q=source` post
  list with `limit` and `after`/`before` paging, `filter`
- [ ] Server commands: `mp-slug`, `mp-syndicate-to`, `mp-photo-alt`,
  `mp-channel`
- [ ] Media endpoint, `q=source` on media, media delete extension
- [ ] Error responses: `invalid_request`, `unauthorized`, `forbidden`,
  `insufficient_scope`

### Scopes

| Scope | Allows |
| --- | --- |
| `create` | Create and undelete posts. Upload media |
| `update` | Update posts |
| `delete` | Delete posts and media |
| `draft` | Create and update posts, but forces `post-status: draft` |
| `media` | Upload media |
| `profile` | Token response includes the user's profile |
| `post` | Deprecated alias of `create`. Accept it |

## Test clients

Check each phase against real clients, not only unit tests.

- Quill: https://quill.p3k.io
- micropub.rocks test suite: https://micropub.rocks
- IndieAuth test client: https://indieauth.rocks (if available) or Quill's
  sign-in
- iA Writer (articles), Indigenous or similar (likes, bookmarks)
