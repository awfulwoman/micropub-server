"""GitHub store using the Git Data API: one atomic commit per Micropub action."""

from __future__ import annotations

import base64

import httpx

from .base import Change, Delete, Move, Put, StoreError

_HINT = (
    "Check the token: it needs Contents: Read and write on this repository "
    "(a fine-grained token with read-only Contents fails writes)."
)


class GitHubStore:
    name = "GitHub"

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        token: str,
        branch: str = "main",
        base_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
    ):
        self.owner, self.repo, self.branch = owner, repo, branch
        self.uid = f"https://github.com/{owner}/{repo}"
        self._base = f"{base_url.rstrip('/')}/repos/{owner}/{repo}"
        self._client = client or httpx.AsyncClient(timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "micropub-server",
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise StoreError(f"GitHub request failed: {exc}") from exc
        return response

    @staticmethod
    def _fail(action: str, response: httpx.Response) -> StoreError:
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        message = f"GitHub could not {action}: HTTP {response.status_code}: {detail}."
        if response.status_code in (401, 403, 404):
            message += f" {_HINT}"
        return StoreError(message, response.status_code)

    async def exists(self, path: str) -> bool:
        response = await self._request("GET", f"/contents/{path}", params={"ref": self.branch})
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise self._fail(f"check {path}", response)
        return True

    async def read(self, path: str) -> bytes:
        response = await self._request("GET", f"/contents/{path}", params={"ref": self.branch})
        if response.status_code != 200:
            raise self._fail(f"read {path}", response)
        return base64.b64decode(response.json()["content"])

    async def commit(self, changes: list[Change], message: str) -> None:
        entries: list[tuple[str, bytes | None]] = []  # (path, content or None for delete)
        for change in changes:
            if isinstance(change, Put):
                entries.append((change.path, change.content))
            elif isinstance(change, Delete):
                entries.append((change.path, None))
            elif isinstance(change, Move):
                content = (
                    change.content
                    if change.content is not None
                    else await self.read(change.old_path)
                )
                entries.append((change.old_path, None))
                entries.append((change.new_path, content))

        for attempt in range(3):
            response = await self._try_commit(entries, message)
            if response is None:
                return
            if response.status_code != 422 or attempt == 2:
                raise self._fail("update the branch", response)

    async def _try_commit(self, entries, message: str) -> httpx.Response | None:
        """Return None on success, or the failing ref-update response (for a retry)."""
        ref = await self._request("GET", f"/git/ref/heads/{self.branch}")
        if ref.status_code != 200:
            raise self._fail(f"read branch {self.branch}", ref)
        head = ref.json()["object"]["sha"]

        parent = await self._request("GET", f"/git/commits/{head}")
        if parent.status_code != 200:
            raise self._fail("read the head commit", parent)
        base_tree = parent.json()["tree"]["sha"]

        tree = []
        for path, content in entries:
            sha = None
            if content is not None:
                blob = await self._request(
                    "POST",
                    "/git/blobs",
                    json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
                )
                if blob.status_code != 201:
                    raise self._fail(f"upload {path}", blob)
                sha = blob.json()["sha"]
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})

        new_tree = await self._request(
            "POST", "/git/trees", json={"base_tree": base_tree, "tree": tree}
        )
        if new_tree.status_code != 201:
            raise self._fail("create the tree", new_tree)

        commit = await self._request(
            "POST",
            "/git/commits",
            json={"message": message, "tree": new_tree.json()["sha"], "parents": [head]},
        )
        if commit.status_code != 201:
            raise self._fail("create the commit", commit)

        update = await self._request(
            "PATCH",
            f"/git/refs/heads/{self.branch}",
            json={"sha": commit.json()["sha"], "force": False},
        )
        return None if update.status_code == 200 else update

    async def check(self) -> str | None:
        try:
            response = await self._request("GET", "")
        except StoreError as exc:
            return str(exc)
        if response.status_code != 200:
            return str(self._fail("read the repository", response))
        if not response.json().get("permissions", {}).get("push"):
            return (
                f"GitHub token cannot write to {self.owner}/{self.repo}: "
                "it needs Contents: Read and write."
            )
        return None
