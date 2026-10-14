# 07 Content stores

A store is where files go. We need two: **GitHub** for production and
**local file system** for development and tests. Posts and media can use
different stores. Indiekit has `store` and `mediaStore`. Both default to the
same store.

## Interface

```python
class Store(Protocol):
    name: str          # "GitHub"
    uid: str           # "https://github.com/awfulwoman/awfulwoman.com"

    async def exists(self, path: str) -> bool: ...
    async def read(self, path: str) -> bytes: ...
    async def commit(self, changes: list[Change], message: str) -> None: ...
```

`Change` is one of:

- `Put(path, content: bytes)`: create or replace.
- `Delete(path)`
- `Move(old_path, new_path, content: bytes | None)`

Indiekit's store API is one call per file (`createFile`, `updateFile`,
`deleteFile`), which means one commit per file. A photo post with two images
would make three commits and three site builds. With the `commit()` batch,
one Micropub action is **one commit**.

Errors raise `StoreError(message, status)`. The message must include the
upstream status and body. An empty message, like Indiekit's on a 403, is a
bug.

## GitHub store

Settings: `owner`, `repo`, `branch` (default `main`), and a token from
`GITHUB_TOKEN`. Base URL defaults to `https://api.github.com`. Allow an
override for GitHub Enterprise or Gitea.

### Token

A fine-grained PAT, limited to the one repository, with **Contents: read and
write**. Metadata read is automatic. The 2026-10-14 failure came from a
read-only token.

At **startup**, check the token:

1. `GET /repos/{owner}/{repo}` must return `200` with
   `permissions.push == true`.
2. Otherwise log a clear error ("GitHub token cannot write to
   owner/repo: needs Contents read and write") and report it on `/health`
   as degraded. Still start, so the auth endpoints work.

### Single-file changes

Use the contents API (what Indiekit uses):

- Read: `GET /repos/{o}/{r}/contents/{path}?ref={branch}`, then base64-decode
  `content`. Keep `sha`.
- Create: `PUT …/contents/{path}` with `{message, content: b64, branch}`.
  If the file exists, GitHub returns `422`. Treat that as a collision.
- Update: `PUT` with the current `sha`. A `409` (sha mismatch) means someone
  else committed. Read again and retry once.
- Delete: `DELETE …/contents/{path}` with `{message, sha, branch}`.

### Multi-file changes (moves, photo bundles)

Use the Git Data API so the change is one atomic commit:

1. `GET /repos/{o}/{r}/git/ref/heads/{branch}`: head commit sha.
2. `GET /repos/{o}/{r}/git/commits/{sha}`: base tree sha.
3. `POST /repos/{o}/{r}/git/blobs` for each binary file (base64).
4. `POST /repos/{o}/{r}/git/trees` with `base_tree` and the entries. A
   deletion is an entry with `sha: null`.
5. `POST /repos/{o}/{r}/git/commits` with `message`, `tree` and
   `parents: [head]`.
6. `PATCH /repos/{o}/{r}/git/refs/heads/{branch}` with `{sha}`
   (`force: false`). A `422` (not a fast-forward) means retry from step 1,
   up to 3 times.

Simplest is to always use this path, even for single files.

### Commit messages and authors

- Message: `{action} {post-type} {file-type}`, as in Indiekit:
  `create note post`, `upload photo file`. Configurable as a template.
- The author and committer come from the token's user, so they show as the
  site owner.
- **Never add attribution trailers.** The awfulwoman.com repo forbids
  `Co-Authored-By` and similar lines.

### Rate limits

Read `x-ratelimit-remaining`. Below 50: log a warning. On `403` or `429` with
`retry-after`, back off and return `503` to the client with `Retry-After`.

## File-system store

Settings: `directory`. Writes are atomic: write a temp file, then
`os.replace`. `commit()` applies its changes in order, with no rollback. It
is for development and for tests that check the rendered output byte for
byte.

## Possible later store: local Git checkout

Clone the repo into the data volume, write files, `git commit`, `git push`.
Faster for big media, and allows offline queueing. Not in the first phases.
