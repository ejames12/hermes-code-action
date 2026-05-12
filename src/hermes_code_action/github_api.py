from __future__ import annotations

import json
from typing import Any
from urllib import error as urlerror
from urllib import parse, request


class GitHubApiError(RuntimeError):
    pass


class GitHubApi:
    def __init__(self, token: str, owner: str, repo: str, *, api_url: str = "https://api.github.com") -> None:
        self.token = token
        self.owner = owner
        self.repo = repo
        self.api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, data: dict[str, Any] | None = None, *, accept: str = "application/vnd.github+json") -> Any:
        url = f"{self.api_url}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = request.Request(url, data=body, method=method)
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "hermes-code-action")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                if accept.endswith(".diff") or accept == "application/vnd.github.diff":
                    return raw
                return json.loads(raw)
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(f"GitHub API {method} {path} failed: {exc.code} {raw}") from exc
        except urlerror.URLError as exc:
            raise GitHubApiError(f"GitHub API {method} {path} failed: {exc}") from exc

    def repo_path(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    def get_permission(self, username: str) -> str:
        data = self.request("GET", self.repo_path(f"/collaborators/{parse.quote(username, safe='')}/permission"))
        return (data or {}).get("permission", "none")

    def get_repo(self) -> dict[str, Any]:
        return self.request("GET", self.repo_path(""))

    def get_issue(self, number: int) -> dict[str, Any]:
        return self.request("GET", self.repo_path(f"/issues/{number}"))

    def get_pull(self, number: int) -> dict[str, Any]:
        return self.request("GET", self.repo_path(f"/pulls/{number}"))

    def list_issue_comments(self, number: int, per_page: int = 100) -> list[dict[str, Any]]:
        return self.request("GET", self.repo_path(f"/issues/{number}/comments?per_page={per_page}")) or []

    def list_review_comments(self, number: int, per_page: int = 100) -> list[dict[str, Any]]:
        return self.request("GET", self.repo_path(f"/pulls/{number}/comments?per_page={per_page}")) or []

    def get_pull_diff(self, number: int) -> str:
        return self.request("GET", self.repo_path(f"/pulls/{number}"), accept="application/vnd.github.diff") or ""

    def create_issue_comment(self, number: int, body: str) -> dict[str, Any]:
        return self.request("POST", self.repo_path(f"/issues/{number}/comments"), {"body": body})

    def update_issue_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        return self.request("PATCH", self.repo_path(f"/issues/comments/{comment_id}"), {"body": body})

    def update_pull_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        return self.request("PATCH", self.repo_path(f"/pulls/comments/{comment_id}"), {"body": body})

    def list_check_runs(self, ref: str) -> list[dict[str, Any]]:
        data = self.request("GET", self.repo_path(f"/commits/{parse.quote(ref, safe='')}/check-runs")) or {}
        return data.get("check_runs") or []
