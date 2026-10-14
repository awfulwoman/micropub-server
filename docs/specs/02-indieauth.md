# 02 IndieAuth

The server is both the **authorization server** (it signs the user in and
issues tokens) and the **resource server** (Micropub and media check those
tokens). There is one user. Their identity is the publication URL,
`https://awfulwoman.com/`.

## Endpoints

| Method and path | Purpose | Indiekit equivalent |
| --- | --- | --- |
| `GET /.well-known/oauth-authorization-server` | Server metadata (RFC 8414) | `GET /auth/metadata`, linked from the docs page |
| `GET /auth` (with parameters) | Start authorization: validate, then show consent | same |
| `GET /auth` (no parameters) | Human-readable docs page | same |
| `POST /auth` | Redeem a code for the profile URL only | same |
| `GET /auth/consent?request_uri=…` | Password and scope form | same |
| `POST /auth/consent` | Check the password, issue a code, redirect | same |
| `POST /auth/token` | Redeem a code for an access token | same. Indiekit also wrongly accepts `GET` |
| `POST /auth/introspect` | Token introspection (RFC 7662) | same, but without authorization |
| `POST /auth/revoke` | Token revocation (RFC 7009) | missing |
| `GET /auth/userinfo` | Profile for tokens with `profile` scope | same |
| `GET, POST /auth/new-password` | Do not build. Set the password with a CLI command (see below) | web form |

Keep the Indiekit paths so the existing `<head>` links still work.

### Discovery on the site

The site `<head>` carries these links. The last one is new compared with what
awfulwoman.com has today:

```html
<link rel="micropub" href="https://indie.awfulwoman.com/micropub">
<link rel="authorization_endpoint" href="https://indie.awfulwoman.com/auth">
<link rel="token_endpoint" href="https://indie.awfulwoman.com/auth/token">
<link rel="indieauth-metadata" href="https://indie.awfulwoman.com/.well-known/oauth-authorization-server">
```

### Metadata document

```json
{
  "issuer": "https://indie.awfulwoman.com/",
  "authorization_endpoint": "https://indie.awfulwoman.com/auth",
  "token_endpoint": "https://indie.awfulwoman.com/auth/token",
  "introspection_endpoint": "https://indie.awfulwoman.com/auth/introspect",
  "introspection_endpoint_auth_methods_supported": ["Bearer"],
  "revocation_endpoint": "https://indie.awfulwoman.com/auth/revoke",
  "revocation_endpoint_auth_methods_supported": ["none"],
  "userinfo_endpoint": "https://indie.awfulwoman.com/auth/userinfo",
  "scopes_supported": ["create", "update", "delete", "draft", "media", "profile"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "code_challenge_methods_supported": ["S256"],
  "authorization_response_iss_parameter_supported": true,
  "service_documentation": "https://indie.awfulwoman.com/auth"
}
```

`issuer` must be a prefix of the authorization endpoint, have no query or
fragment, and match the `iss` value sent on the redirect.

## Authorization flow

### 1. `GET /auth`

Required query parameters:

| Parameter | Rule |
| --- | --- |
| `response_type` | `code`. Also accept `id` (deprecated) and treat it as a profile-only flow. Indiekit defaults it to `id` for clients from `indieauth.com`. |
| `client_id` | Absolute URL. No `..` or `.` path segments. |
| `redirect_uri` | Absolute URL. |
| `state` | Any string, sent back unchanged. |

Optional: `me`, `scope` (space-separated), `code_challenge`,
`code_challenge_method`.

Checks, in order:

1. Missing required parameter: `400 invalid_request`. Before `redirect_uri`
   is known to be valid, show an error page. Never redirect to an unchecked
   URL.
2. If either PKCE parameter is present, both must be, and the method must be
   `S256`. PKCE stays optional, because some older clients do not send it.
   Log a warning when a client omits it.
3. **`redirect_uri` check.** Pass when it has the same scheme, host and port
   as `client_id`. Otherwise fetch `client_id` (5 s timeout) and accept
   `redirect_uri` only if it exactly matches a `rel="redirect_uri"` link, from
   the HTML (mf2py `rels`) or from an HTTP `Link` header.
4. **Client information** for the consent screen. Fetch `client_id`:
   - JSON client metadata document with a `client_id` field: use
     `client_name`, `client_uri`, `logo_uri`.
   - Otherwise HTML: use the first `h-app` or `h-x-app` (`name`, `url`,
     `logo`).
   - Otherwise show the bare `client_id`.
   - Do not fetch loopback, link-local or private addresses (SSRF guard).
     Indiekit has `isFetchableOrigin` for this.
5. Store the validated request server-side under a random
   `request_uri = urn:ietf:params:oauth:request_uri:<uuid>`, with a 10-minute
   expiry, in SQLite. Indiekit keeps it in process memory, so it leaks and is
   lost on restart.
6. Redirect to `/auth/consent?request_uri=…`.

### 2. Consent: `GET` and `POST /auth/consent`

- `GET` loads the stored request. Unknown or expired: `400`. It shows:
  - the client name and logo
  - the `me` being authorised
  - the requested scopes, as checkboxes that are ticked by default, so the
    user can grant fewer
  - a password field
- Title is "Sign in" when no scope was requested (authentication only) and
  "Authorize" otherwise.
- `POST` checks the password against the stored hash (argon2id, or bcrypt
  during cut-over). On failure, show the form again with `422` and an error.
  Rate-limit failures: for example 5 per minute per IP, then `429`.
- On success:
  - create an **authorization code**: 32+ random bytes, base64url. Store
    `client_id`, `redirect_uri`, scope, `code_challenge`, `me` and an expiry
    (10 min, per IndieAuth) in SQLite.
  - delete the stored request.
  - redirect to `redirect_uri` with `code`, `state` and `iss`, plus the
    original `me` if one was sent.

`me` in the code is always the configured publication URL in canonical form
(lower-case host, trailing slash on an empty path). It never comes from the
client.

### 3. Redeem a code

Both endpoints share the same validation. The request body is form-encoded.
Also accept JSON, as Indiekit does.

| Parameter | Authorization endpoint `POST /auth` | Token endpoint `POST /auth/token` |
| --- | --- | --- |
| `grant_type` | optional, must be `authorization_code` if present | required, `authorization_code` |
| `code` | required | required |
| `client_id` | required, must match the code (canonical compare) | same |
| `redirect_uri` | required, must match exactly | same |
| `code_verifier` | required if the code had a challenge | same |

Checks:

- Code exists, has not expired and has not been used. **Mark it used inside
  the same transaction that reads it.** If a used code is presented again,
  revoke every token issued from it (OAuth 2.0 §4.1.2).
- PKCE: `BASE64URL(SHA256(code_verifier)) == code_challenge`, compared in
  constant time.
- Failure: `400` with `{"error": "invalid_grant"}` (or `invalid_request` for
  missing parameters).

Responses. Send JSON when the request accepts JSON, which is the default.
Send form-encoded only when the client asks for it.

Authorization endpoint (profile URL only):

```json
{ "me": "https://awfulwoman.com/" }
```

Token endpoint:

```json
{
  "access_token": "<opaque>",
  "token_type": "Bearer",
  "scope": "create update media",
  "me": "https://awfulwoman.com/",
  "expires_in": 7776000,
  "profile": { "name": "…", "url": "…", "photo": "…" }
}
```

- A code issued with no scope **must not** be redeemable at the token
  endpoint. That flow is authentication only. Return `invalid_grant`.
- Include `profile` only when the `profile` scope was granted. It comes from
  the configured profile values, falling back to the first `h-card` on `me`
  whose `url` matches `me`, or else the first `h-card`. The fields are
  `name`, `url` and `photo`.

## Access tokens

Indiekit uses JWTs signed with `SECRET` that last 90 days and cannot be
revoked. Use **opaque random tokens** instead:

- 32 random bytes, base64url, with a prefix such as `mps_` so a leaked token
  is easy to spot in logs.
- Store only `sha256(token)` in SQLite, with `client_id`, scope, `me`,
  `issued_at`, `expires_at`, `last_used_at` and `revoked_at`.
- Default lifetime: 90 days, configurable. Report `expires_in`.

### Verifying a request (resource server)

Used by `/micropub`, `/media` and `/syndicate`:

1. Find the token in the `Authorization: Bearer` header, else the
   `access_token` form field or JSON body field.
2. If both are sent, reject with `400 invalid_request`. Micropub forbids
   sending both.
3. No token: `401` with `{"error": "unauthorized"}`.
4. Look up the hash. Unknown, expired or revoked: `401` with
   `{"error": "unauthorized"}`. Indiekit returns a localised message, which is
   fine as `error_description`.
5. `me` must equal the publication URL after canonicalisation. Otherwise
   `403 forbidden`.
6. Update `last_used_at`. Pass the token's scopes to the handler.

Indiekit calls its own introspection endpoint over HTTP for every request.
Do the lookup in-process instead.

### Introspection: `POST /auth/introspect`

- Requires authorization. The IndieAuth spec says the endpoint MUST require
  it, and Indiekit does not. Accept a bearer token with any valid scope
  belonging to this server. A static `INTROSPECTION_SECRET` is an option for
  third-party resource servers.
- Body: `token=<token>`.
- Active:
  `{"active": true, "me", "client_id", "scope", "exp", "iat"}`.
- Anything else: `{"active": false}`.

### Revocation: `POST /auth/revoke`

- Body: `token=<token>`. Always return `200`, even for unknown tokens
  (RFC 7009).
- Also accept the legacy Micropub form, `action=revoke&token=…`, sent to the
  token endpoint.

### Userinfo: `GET /auth/userinfo`

Needs a bearer token with the `profile` scope. Otherwise `403` with
`{"error": "insufficient_scope", "scope": "profile"}`. Returns the profile
object.

## The admin password

- One password for the one user. Store it as an argon2id hash in the
  `PASSWORD_HASH` env var (from the vault). Also accept `PASSWORD_SECRET`
  with a bcrypt hash, so the current vaulted value works during cut-over.
- Set it from the CLI, never the web:
  `micropub-server hash-password`. It reads from the TTY without echo and
  prints the hash. It must also work with no TTY, reading one line from
  stdin, so it works from Claude Code's `!` prompt (the `getpass` failure we
  hit with Indiekit).
- The server refuses to start without a password hash.

## Signing in to the server's own UI

Indiekit signs its own UI in through its own IndieAuth flow, acting as its
own client. For a single-user admin UI, a simpler password session cookie is
enough:

- `itsdangerous`-signed, `HttpOnly`, `Secure`, `SameSite=Lax`.
- 30-day expiry.
- CSRF token on every form.

## Errors

OAuth error JSON: `{"error": "...", "error_description": "..."}`.

| Situation | Status | `error` |
| --- | --- | --- |
| Missing or malformed parameter | 400 | `invalid_request` |
| Bad, expired, reused or mismatched code | 400 | `invalid_grant` |
| Unsupported `grant_type` | 400 | `unsupported_grant_type` |
| No, bad or expired token | 401 | `unauthorized` (Micropub's name) |
| Token for another `me` | 403 | `forbidden` |
| Missing scope | 403 | `insufficient_scope` (+ `scope`) |

Errors **after** `redirect_uri` is validated, such as the user cancelling
consent, go back to the client:
`redirect_uri?error=access_denied&state=…&iss=…`.
