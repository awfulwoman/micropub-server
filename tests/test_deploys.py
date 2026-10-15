"""Tracking site builds through GitHub's workflow_run webhook."""

import hashlib
import hmac
import itertools
import json

import pytest
import respx

from micropub_server.stores import Put
from micropub_server.stores.github import GitHubStore

from .conftest import sign_in
from .test_store_github import mock_commit_flow

SECRET = "hook-secret"
WORKFLOW = "Build site and push to server"


@pytest.fixture
def shas(state):
    """Make the store hand back a commit id for every commit, like GitHub does."""
    counter = itertools.count(1)
    original = state.store.commit

    async def commit(changes, message):
        await original(changes, message)
        return f"sha{next(counter)}"

    state.store.commit = commit
    return state


@pytest.fixture
def hooked(state, shas):
    state.settings.webhook_secret = SECRET
    state.config.deploy.workflow = WORKFLOW
    state.config.store.owner, state.config.store.repo = "awfulwoman", "awfulwoman.com"
    return state


@pytest.fixture
def full(client):
    return {
        "Authorization": f"Bearer {sign_in(client, scope='create update delete')['access_token']}"
    }


def make(client, headers, **data):
    r = client.post("/micropub", data={"content": "hello", **data}, headers=headers)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def uid_of(state, url):
    return state.db.query("SELECT uid FROM posts WHERE url = ?", (url,))[0]["uid"]


def deploy_state(state, url):
    rows = state.db.query(
        "SELECT d.state FROM deploys d JOIN posts p ON p.uid = d.post_uid"
        " WHERE p.url = ? ORDER BY d.id DESC LIMIT 1",
        (url,),
    )
    return rows[0]["state"] if rows else None


def run_payload(sha, conclusion="success", **over):
    run = {
        "head_sha": sha,
        "head_branch": "main",
        "name": WORKFLOW,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://github.com/awfulwoman/awfulwoman.com/actions/runs/{sha}",
    }
    run.update(over.pop("run", {}))
    body = {
        "action": "completed",
        "workflow_run": run,
        "repository": {"full_name": "awfulwoman/awfulwoman.com"},
    }
    body.update(over)
    return body


def deliver(client, payload, *, event="workflow_run", secret=SECRET, signature=None):
    body = json.dumps(payload).encode()
    sig = signature or "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-event": event,
            "x-hub-signature-256": sig,
        },
    )


# --- recording ------------------------------------------------------------------------------
def test_a_new_post_is_pending_until_its_build_finishes(client, hooked, full):
    url = make(client, full)
    assert deploy_state(hooked, url) == "pending"


def test_edits_and_deletes_are_tracked_too(client, hooked, full):
    url = make(client, full)
    client.post(
        "/micropub", json={"action": "update", "url": url, "add": {"category": ["x"]}}, headers=full
    )
    assert len(hooked.db.query("SELECT 1 FROM deploys")) == 2
    client.post("/micropub", data={"action": "delete", "url": url}, headers=full)
    assert len(hooked.db.query("SELECT 1 FROM deploys")) == 3


def test_a_store_without_commit_ids_records_nothing(client, state, full):
    make(client, full)  # the filesystem store returns no id
    assert state.db.query("SELECT 1 FROM deploys") == []


# --- the endpoint ---------------------------------------------------------------------------
def test_endpoint_is_off_without_a_secret(client, state):
    assert deliver(client, run_payload("sha1")).status_code == 404


def test_a_bad_signature_is_refused_and_changes_nothing(client, hooked, full):
    url = make(client, full)
    r = deliver(client, run_payload("sha1"), signature="sha256=" + "0" * 64)
    assert r.status_code == 401
    assert deliver(client, run_payload("sha1"), secret="other").status_code == 401
    assert deploy_state(hooked, url) == "pending"


def test_ping_is_answered(client, hooked):
    assert deliver(client, {"zen": "x"}, event="ping").status_code == 200


def test_other_events_are_ignored(client, hooked):
    assert deliver(client, {"x": 1}, event="push").status_code == 202


def test_success_marks_the_post_live_and_keeps_the_run_link(client, hooked, full):
    url = make(client, full)
    assert deliver(client, run_payload("sha1")).status_code == 202
    assert deploy_state(hooked, url) == "live"
    row = hooked.db.query("SELECT run_url FROM deploys WHERE sha = 'sha1'")[0]
    assert row["run_url"].endswith("/runs/sha1")


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_a_failed_build_is_recorded(client, hooked, full, conclusion):
    url = make(client, full)
    deliver(client, run_payload("sha1", conclusion))
    assert deploy_state(hooked, url) == "failed"


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", None])
def test_cancelled_runs_change_nothing(client, hooked, full, conclusion):
    url = make(client, full)
    deliver(client, run_payload("sha1", conclusion))
    assert deploy_state(hooked, url) == "pending"


def test_unfinished_runs_are_ignored(client, hooked, full):
    url = make(client, full)
    deliver(client, run_payload("sha1", "success") | {"action": "in_progress"})
    assert deploy_state(hooked, url) == "pending"


@pytest.mark.parametrize(
    "over",
    [
        {"run": {"name": "Some other workflow"}},
        {"run": {"head_branch": "feature"}},
        {"repository": {"full_name": "someone/else"}},
    ],
)
def test_runs_for_other_workflows_branches_or_repos_are_ignored(client, hooked, full, over):
    url = make(client, full)
    deliver(client, run_payload("sha1", **over))
    assert deploy_state(hooked, url) == "pending"


def test_a_later_success_covers_earlier_commits(client, hooked, full):
    one, two = make(client, full), make(client, full, content="second")
    deliver(client, run_payload("sha2"))  # the first run was cancelled by the second
    assert deploy_state(hooked, one) == deploy_state(hooked, two) == "live"


def test_a_failure_does_not_undo_a_later_success_or_touch_newer_commits(client, hooked, full):
    one, two = make(client, full), make(client, full, content="second")
    deliver(client, run_payload("sha1", "failure"))
    assert (deploy_state(hooked, one), deploy_state(hooked, two)) == ("failed", "pending")
    deliver(client, run_payload("sha2"))  # the later build includes the earlier change
    assert (deploy_state(hooked, one), deploy_state(hooked, two)) == ("live", "live")
    deliver(client, run_payload("sha1", "failure"))  # a late, stale failure
    assert deploy_state(hooked, one) == "live"


def test_an_unknown_commit_changes_nothing(client, hooked, full):
    url = make(client, full)
    assert deliver(client, run_payload("somebody-elses")).status_code == 202
    assert deploy_state(hooked, url) == "pending"


def test_an_empty_workflow_setting_accepts_any_workflow(client, hooked, full):
    hooked.config.deploy.workflow = None
    url = make(client, full)
    deliver(client, run_payload("sha1", run={"name": "Anything"}))
    assert deploy_state(hooked, url) == "live"


# --- sharing after a build ------------------------------------------------------------------
def test_a_successful_build_shares_waiting_posts(client, hooked, full, monkeypatch):
    calls = []

    async def fake(s, source_url=None):
        from micropub_server.syndication.service import Result

        calls.append(source_url)
        return Result("No posts awaiting syndication")

    monkeypatch.setattr("micropub_server.routes_webhook.run_syndication", fake)
    make(client, full)
    deliver(client, run_payload("sha1", "failure"))
    assert calls == []
    deliver(client, run_payload("sha1"))
    assert calls == [None]


def test_sharing_goes_through_every_waiting_post_once(client, hooked, full, monkeypatch):
    from micropub_server.syndication.service import Result

    urls = iter(["https://a/", "https://b/", "https://b/"])

    async def fake(s, source_url=None):
        return Result("done", url=next(urls))

    monkeypatch.setattr("micropub_server.routes_webhook.run_syndication", fake)
    make(client, full)
    deliver(client, run_payload("sha1"))
    assert next(urls, None) is None  # a, b, then b again (no progress) stopped the loop


def test_a_sharing_error_does_not_fail_the_webhook(client, hooked, full, monkeypatch):
    async def boom(s, source_url=None):
        raise RuntimeError("mastodon is down")

    monkeypatch.setattr("micropub_server.routes_webhook.run_syndication", boom)
    url = make(client, full)
    assert deliver(client, run_payload("sha1")).status_code == 202
    assert deploy_state(hooked, url) == "live"


# --- what you see ---------------------------------------------------------------------------
def signed_in_ui(client):
    from .test_ui_session import login  # the same helper the UI tests use

    return login(client)


def test_the_post_page_shows_the_build_state(client, hooked, full):
    url = make(client, full)
    signed_in_ui(client)
    uid = uid_of(hooked, url)
    assert "building" in client.get(f"/posts/{uid}").text
    deliver(client, run_payload("sha1", "failure"))
    page = client.get(f"/posts/{uid}").text
    assert "build failed" in page and "/runs/sha1" in page
    deliver(client, run_payload("sha1"))
    assert "build failed" not in client.get(f"/posts/{uid}").text
    assert "live" in client.get(f"/posts/{uid}").text


def test_the_list_flags_building_and_failed_posts_only(client, hooked, full):
    make(client, full)
    make(client, full, content="second")
    signed_in_ui(client)
    deliver(client, run_payload("sha1", "failure"))
    page = client.get("/posts").text
    assert "build failed" in page and "building" in page
    deliver(client, run_payload("sha2"))
    page = client.get("/posts").text
    assert "build failed" not in page and "building" not in page


@respx.mock
async def test_github_store_returns_the_commit_id():
    store = GitHubStore(owner="awfulwoman", repo="awfulwoman.com", branch="main", token="t")
    mock_commit_flow(respx)
    assert await store.commit([Put("a.md", b"x")], "m") == "commit2"


def test_real_payloads_are_bigger_than_a_form_but_still_accepted(client, hooked, full):
    url = make(client, full)
    big = run_payload("sha1") | {"padding": "x" * 200_000}
    assert deliver(client, big).status_code == 202
    assert deploy_state(hooked, url) == "live"
    too_big = run_payload("sha1") | {"padding": "x" * 2_000_000}
    assert deliver(client, too_big).status_code == 413
