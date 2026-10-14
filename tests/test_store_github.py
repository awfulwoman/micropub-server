import base64
import json

import httpx
import pytest
import respx

from micropub_server.stores import Delete, Move, Put, StoreError
from micropub_server.stores.github import GitHubStore

API = "https://api.github.com"
REPO = f"{API}/repos/awfulwoman/awfulwoman.com"


@pytest.fixture
def store():
    return GitHubStore(owner="awfulwoman", repo="awfulwoman.com", branch="main", token="tok")


def mock_commit_flow(router, *, ref_sha="head1", patch_status=200):
    router.get(f"{REPO}/git/ref/heads/main").respond(200, json={"object": {"sha": ref_sha}})
    router.get(f"{REPO}/git/commits/{ref_sha}").respond(200, json={"tree": {"sha": "tree1"}})
    blobs = router.post(f"{REPO}/git/blobs").respond(201, json={"sha": "blob1"})
    trees = router.post(f"{REPO}/git/trees").respond(201, json={"sha": "tree2"})
    commits = router.post(f"{REPO}/git/commits").respond(201, json={"sha": "commit2"})
    patch = router.patch(f"{REPO}/git/refs/heads/main").respond(patch_status, json={})
    return blobs, trees, commits, patch


@respx.mock
async def test_commit_put_is_one_atomic_commit(store):
    blobs, trees, commits, patch = mock_commit_flow(respx)
    await store.commit([Put("content/notes/a.md", b"hello")], "create note post")

    blob_body = json.loads(blobs.calls[0].request.content)
    assert base64.b64decode(blob_body["content"]) == b"hello"
    assert blob_body["encoding"] == "base64"

    tree_body = json.loads(trees.calls[0].request.content)
    assert tree_body["base_tree"] == "tree1"
    assert tree_body["tree"] == [
        {"path": "content/notes/a.md", "mode": "100644", "type": "blob", "sha": "blob1"}
    ]
    commit_body = json.loads(commits.calls[0].request.content)
    assert commit_body == {"message": "create note post", "tree": "tree2", "parents": ["head1"]}
    patch_body = json.loads(patch.calls[0].request.content)
    assert patch_body == {"sha": "commit2", "force": False}
    # auth and headers
    req = blobs.calls[0].request
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["x-github-api-version"]


@respx.mock
async def test_delete_uses_null_sha(store):
    _, trees, *_ = mock_commit_flow(respx)
    await store.commit([Delete("old.md")], "m")
    tree = json.loads(trees.calls[0].request.content)["tree"]
    assert tree == [{"path": "old.md", "mode": "100644", "type": "blob", "sha": None}]


@respx.mock
async def test_move_with_content_is_delete_plus_put(store):
    _, trees, *_ = mock_commit_flow(respx)
    await store.commit([Move("a.md", "b.md", content=b"x")], "m")
    paths = {(e["path"], e["sha"]) for e in json.loads(trees.calls[0].request.content)["tree"]}
    assert paths == {("a.md", None), ("b.md", "blob1")}


@respx.mock
async def test_move_without_content_reads_old_file(store):
    respx.get(f"{REPO}/contents/a.md", params={"ref": "main"}).respond(
        200, json={"content": base64.b64encode(b"old").decode(), "encoding": "base64"}
    )
    blobs, *_ = mock_commit_flow(respx)
    await store.commit([Move("a.md", "b.md")], "m")
    assert base64.b64decode(json.loads(blobs.calls[0].request.content)["content"]) == b"old"


@respx.mock
async def test_retries_when_not_fast_forward(store):
    respx.get(f"{REPO}/git/ref/heads/main").mock(
        side_effect=[
            httpx.Response(200, json={"object": {"sha": "head1"}}),
            httpx.Response(200, json={"object": {"sha": "head2"}}),
        ]
    )
    respx.get(f"{REPO}/git/commits/head1").respond(200, json={"tree": {"sha": "t1"}})
    respx.get(f"{REPO}/git/commits/head2").respond(200, json={"tree": {"sha": "t2"}})
    respx.post(f"{REPO}/git/blobs").respond(201, json={"sha": "b"})
    respx.post(f"{REPO}/git/trees").respond(201, json={"sha": "tree"})
    respx.post(f"{REPO}/git/commits").respond(201, json={"sha": "c"})
    patch = respx.patch(f"{REPO}/git/refs/heads/main").mock(
        side_effect=[
            httpx.Response(422, json={"message": "not a fast forward"}),
            httpx.Response(200, json={}),
        ]
    )
    await store.commit([Put("a.md", b"x")], "m")
    assert patch.call_count == 2


@respx.mock
async def test_gives_up_after_three_non_fast_forwards(store):
    mock_commit_flow(respx, patch_status=422)
    with pytest.raises(StoreError):
        await store.commit([Put("a.md", b"x")], "m")


@respx.mock
async def test_error_message_has_status_and_body(store):
    respx.get(f"{REPO}/git/ref/heads/main").respond(
        403, json={"message": "Resource not accessible by personal access token"}
    )
    with pytest.raises(StoreError) as e:
        await store.commit([Put("a.md", b"x")], "m")
    assert e.value.status == 403
    assert "403" in str(e.value)
    assert "Resource not accessible" in str(e.value)
    assert "Contents" in str(e.value)  # points at the permission to fix


@respx.mock
async def test_exists(store):
    respx.get(f"{REPO}/contents/a.md", params={"ref": "main"}).respond(200, json={"type": "file"})
    respx.get(f"{REPO}/contents/b.md", params={"ref": "main"}).respond(
        404, json={"message": "Not Found"}
    )
    assert await store.exists("a.md")
    assert not await store.exists("b.md")


@respx.mock
async def test_read(store):
    respx.get(f"{REPO}/contents/a.md", params={"ref": "main"}).respond(
        200, json={"content": base64.b64encode(b"data").decode()}
    )
    assert await store.read("a.md") == b"data"


@respx.mock
async def test_check_ok(store):
    respx.get(REPO).respond(200, json={"permissions": {"push": True}})
    assert await store.check() is None


@respx.mock
async def test_check_read_only_token(store):
    respx.get(REPO).respond(200, json={"permissions": {"push": False, "pull": True}})
    problem = await store.check()
    assert "Contents" in problem and "write" in problem


@respx.mock
async def test_check_bad_token(store):
    respx.get(REPO).respond(401, json={"message": "Bad credentials"})
    assert "401" in await store.check()


@respx.mock
async def test_check_unknown_repo(store):
    respx.get(REPO).respond(404, json={"message": "Not Found"})
    assert "404" in await store.check()
