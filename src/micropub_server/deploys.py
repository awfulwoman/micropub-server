"""Match finished site builds to the posts that caused them (spec 08)."""

from __future__ import annotations

from typing import Any

from .app import AppState

FAILED = {"failure", "timed_out", "startup_failure"}


def record(s: AppState, sha: str | None, post_uid: str, action: str) -> None:
    """Note a commit made for a post. Stores with no commit ids (a plain directory) record nothing."""
    if sha:
        s.db.execute(
            "INSERT OR IGNORE INTO deploys (sha, post_uid, action) VALUES (?, ?, ?)",
            (sha, post_uid, action),
        )


def state_for(s: AppState, post_uid: str) -> dict[str, str] | None:
    """The build state of the post's latest commit, or None if it is not tracked."""
    rows = s.db.query(
        "SELECT state, run_url FROM deploys WHERE post_uid = ? ORDER BY id DESC LIMIT 1",
        (post_uid,),
    )
    return dict(rows[0]) if rows else None


LABELS = {"pending": "building", "live": "live", "failed": "build failed"}


def build_label(s: AppState, post_uid: str) -> dict[str, str | None] | None:
    """What the UI shows for a post's latest build, or None if the post is not tracked."""
    found = state_for(s, post_uid)
    if found is None:
        return None
    return {"state": found["state"], "text": LABELS[found["state"]], "run_url": found["run_url"]}


def wanted(s: AppState, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The workflow run in a webhook payload, if it is a finished run of the deploy workflow."""
    run = payload.get("workflow_run")
    if payload.get("action") != "completed" or not isinstance(run, dict):
        return None
    store = s.config.store
    repo = (payload.get("repository") or {}).get("full_name")
    if store.owner and store.repo and repo != f"{store.owner}/{store.repo}":
        return None
    if run.get("head_branch") != store.branch:
        return None
    workflow = s.config.deploy.workflow
    if workflow and run.get("name") != workflow:
        return None
    return run


def apply_run(s: AppState, run: dict[str, Any]) -> bool:
    """Update build states for a finished run. True when the site was deployed successfully."""
    sha, conclusion, link = run.get("head_sha"), run.get("conclusion"), run.get("html_url")
    row = s.db.query("SELECT id FROM deploys WHERE sha = ?", (sha,))
    if conclusion == "success":
        if row:
            # Earlier commits are in this build too (their own runs may have been cancelled).
            s.db.execute(
                "UPDATE deploys SET state = 'live', run_url = coalesce(?, run_url),"
                " finished_at = CURRENT_TIMESTAMP"
                " WHERE id <= ? AND state IN ('pending', 'failed')",
                (link, row[0]["id"]),
            )
        return True
    if conclusion in FAILED and row:
        s.db.execute(
            "UPDATE deploys SET state = 'failed', run_url = ?, finished_at = CURRENT_TIMESTAMP"
            " WHERE id = ? AND state = 'pending'",
            (link, row[0]["id"]),
        )
    return False
