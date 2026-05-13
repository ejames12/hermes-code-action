from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
import os
import re
import subprocess

from .branch import BranchInfo, slugify
from .github_context import GitHubContext
from .util import workspace


@dataclass(frozen=True)
class PlanInfo:
    requested: bool
    file_path: str
    web_url: str | None


def is_plan_request(user_request: str) -> bool:
    """Return True for `@hermes plan`-style requests."""
    request = (user_request or "").strip().lower()
    return bool(re.match(r"^plan(?=$|\s|:|\.)", request))


def is_review_request(user_request: str) -> bool:
    """Return True for `@hermes review ...`-style requests."""
    request = (user_request or "").strip().lower()
    return bool(re.match(r"^review(?=$|\s|:|\.)", request))


def plan_file_path(ctx: GitHubContext) -> str:
    entity_type = "pr" if ctx.is_pr else "issue" if ctx.entity_number else "run"
    entity_number = str(ctx.entity_number or "manual")
    title_slug = slugify(ctx.title or "plan", words=8)
    return f"docs/hermes-plans/{entity_type}-{entity_number}-{title_slug}.md"


def blob_url(ctx: GitHubContext, branch: str | None, file_path: str) -> str | None:
    if not branch or branch == "HEAD":
        return None
    quoted_branch = quote(branch, safe="/")
    quoted_path = quote(file_path, safe="/")
    return f"{ctx.repository.html_url.rstrip('/')}/blob/{quoted_branch}/{quoted_path}"


def build_plan_info(ctx: GitHubContext, branch: BranchInfo) -> PlanInfo:
    file_path = plan_file_path(ctx)
    branch_name = branch.hermes_branch or branch.current_branch
    return PlanInfo(requested=True, file_path=file_path, web_url=blob_url(ctx, branch_name, file_path))


def _git_output(args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=workspace(), text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def current_head_sha() -> str:
    if not os.path.isdir(os.path.join(workspace(), ".git")):
        return ""
    return _git_output(["rev-parse", "HEAD"]).strip()


def changed_files_since(ref: str) -> list[str]:
    if not ref or not os.path.isdir(os.path.join(workspace(), ".git")):
        return []
    committed = _git_output(["diff", "--name-only", f"{ref}..HEAD"]).splitlines()
    unstaged = _git_output(["diff", "--name-only"]).splitlines()
    staged = _git_output(["diff", "--cached", "--name-only"]).splitlines()
    untracked = _git_output(["ls-files", "--others", "--exclude-standard"]).splitlines()
    seen: set[str] = set()
    ordered: list[str] = []
    for path in [*committed, *unstaged, *staged, *untracked]:
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def assert_plan_only_changes(plan_info: PlanInfo, start_ref: str) -> None:
    allowed_prefix = "docs/hermes-plans/"
    changed = changed_files_since(start_ref)
    disallowed = [path for path in changed if path != plan_info.file_path and not path.startswith(allowed_prefix)]
    if disallowed:
        formatted = "\n".join(f"- {path}" for path in disallowed)
        raise RuntimeError(
            "Plan-only request modified files outside docs/hermes-plans/. "
            "Refusing to publish the branch. Disallowed files:\n" + formatted
        )


def assert_review_only_changes(start_ref: str) -> None:
    changed = changed_files_since(start_ref)
    if changed:
        formatted = "\n".join(f"- {path}" for path in changed)
        raise RuntimeError(
            "Review-only request modified repository files. Refusing to publish changes. "
            "Disallowed files:\n" + formatted
        )
