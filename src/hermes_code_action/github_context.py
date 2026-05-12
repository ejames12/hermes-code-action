from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


@dataclass
class Repository:
    owner: str
    repo: str
    full_name: str
    default_branch: str
    html_url: str


@dataclass
class GitHubContext:
    event_name: str
    event_action: str
    actor: str
    payload: dict[str, Any]
    repository: Repository
    entity_number: int | None
    is_pr: bool
    title: str
    body: str
    comment_body: str
    comment_id: int | None

    @property
    def has_entity(self) -> bool:
        return self.entity_number is not None


def _repo_from_payload(payload: dict[str, Any]) -> Repository:
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name") or os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in full_name:
        owner, name = full_name.split("/", 1)
    else:
        owner = (repo.get("owner") or {}).get("login") or ""
        name = repo.get("name") or ""
    return Repository(
        owner=owner,
        repo=name,
        full_name=full_name or f"{owner}/{name}",
        default_branch=repo.get("default_branch") or "main",
        html_url=repo.get("html_url") or f"https://github.com/{owner}/{name}",
    )


def load_event_payload(path: str | None = None) -> dict[str, Any]:
    event_path = path or os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    p = Path(event_path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def parse_context(payload: dict[str, Any] | None = None) -> GitHubContext:
    payload = payload if payload is not None else load_event_payload()
    event_name = os.environ.get("GITHUB_EVENT_NAME", payload.get("event_name", ""))
    event_action = payload.get("action", "")
    actor = ((payload.get("sender") or {}).get("login") or os.environ.get("GITHUB_ACTOR") or "")
    repository = _repo_from_payload(payload)

    number: int | None = None
    is_pr = False
    title = ""
    body = ""
    comment_body = ""
    comment_id: int | None = None

    if event_name == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        number = issue.get("number")
        is_pr = bool(issue.get("pull_request"))
        title = issue.get("title") or ""
        body = issue.get("body") or ""
        comment_body = comment.get("body") or ""
        comment_id = comment.get("id")
    elif event_name == "pull_request_review_comment":
        pr = payload.get("pull_request") or {}
        comment = payload.get("comment") or {}
        number = pr.get("number")
        is_pr = True
        title = pr.get("title") or ""
        body = pr.get("body") or ""
        comment_body = comment.get("body") or ""
        comment_id = comment.get("id")
    elif event_name == "pull_request_review":
        pr = payload.get("pull_request") or {}
        review = payload.get("review") or {}
        number = pr.get("number")
        is_pr = True
        title = pr.get("title") or ""
        body = pr.get("body") or ""
        comment_body = review.get("body") or ""
        comment_id = review.get("id")
    elif event_name == "issues":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        is_pr = False
        title = issue.get("title") or ""
        body = issue.get("body") or ""
    elif event_name == "pull_request":
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        is_pr = True
        title = pr.get("title") or ""
        body = pr.get("body") or ""
    else:
        # Agent mode events still need repository metadata.
        number = None

    return GitHubContext(
        event_name=event_name,
        event_action=event_action,
        actor=actor,
        payload=payload,
        repository=repository,
        entity_number=number,
        is_pr=is_pr,
        title=title,
        body=body,
        comment_body=comment_body,
        comment_id=comment_id,
    )
