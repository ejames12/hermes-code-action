from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import subprocess
from typing import Any

from .config import Inputs
from .github_api import GitHubApi
from .github_context import GitHubContext
from .util import notice, warning, workspace

BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.#+-]*$")


@dataclass
class BranchInfo:
    base_branch: str
    current_branch: str
    hermes_branch: str | None = None
    is_new_branch: bool = False
    is_fork_pr: bool = False


def validate_branch_name(branch: str) -> None:
    if not branch or not branch.strip():
        raise ValueError("Branch name cannot be empty")
    if branch.startswith("-"):
        raise ValueError("Branch name cannot start with '-'")
    if not BRANCH_RE.match(branch):
        raise ValueError(f"Invalid branch name {branch!r}")
    if branch.startswith(".") or branch.endswith("."):
        raise ValueError("Branch name cannot start or end with '.'")
    if branch.endswith("/") or "//" in branch or ".." in branch or branch.endswith(".lock") or "@{" in branch:
        raise ValueError(f"Invalid git branch name {branch!r}")


def slugify(text: str, words: int = 5) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())[:words]
    return "-".join(tokens) or "task"


def generate_branch_name(
    template: str,
    prefix: str,
    entity_type: str,
    entity_number: int,
    sha: str = "",
    label: str = "",
    description: str = "",
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M")
    values = {
        "prefix": prefix,
        "entityType": entity_type,
        "entityNumber": str(entity_number),
        "timestamp": timestamp,
        "sha": sha[:8],
        "label": slugify(label or entity_type, words=3),
        "description": slugify(description, words=5),
    }
    if not template:
        template = "{{prefix}}{{entityType}}-{{entityNumber}}-{{timestamp}}"
    branch = template
    for key, value in values.items():
        branch = branch.replace("{{" + key + "}}", value)
    validate_branch_name(branch)
    return branch


def _git(args: list[str], *, check: bool = True, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    display_args = [re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", arg) for arg in args]
    notice("git " + " ".join(display_args))
    completed = subprocess.run(["git", *args], cwd=cwd or workspace(), text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(display_args)} failed with exit code {completed.returncode}")
    return completed


def current_branch(cwd: str | None = None) -> str:
    completed = _git(["branch", "--show-current"], check=False, cwd=cwd)
    branch = completed.stdout.strip()
    if branch:
        return branch
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    return ref_name or "HEAD"


def configure_git_auth(token: str, ctx: GitHubContext, inputs: Inputs) -> None:
    if not token:
        warning("No GitHub token; skipping git auth configuration")
        return
    _git(["config", "user.name", inputs.bot_name])
    safe_name = inputs.bot_name.replace("[bot]", "")
    _git(["config", "user.email", f"{inputs.bot_id}+{safe_name}@users.noreply.github.com"])
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    if server == "https://github.com":
        remote = (
            "https://x-access-token:"
            + token
            + f"@github.com/{ctx.repository.owner}/{ctx.repository.repo}.git"
        )
    else:
        remote = f"{server.rstrip('/')}/{ctx.repository.owner}/{ctx.repository.repo}.git"
    _git(["remote", "set-url", "origin", remote], check=False)


def _source_sha(base_branch: str) -> str:
    completed = _git(["rev-parse", f"origin/{base_branch}"], check=False)
    if completed.returncode == 0:
        return completed.stdout.strip()
    completed = _git(["rev-parse", "HEAD"], check=False)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def setup_branch(ctx: GitHubContext, inputs: Inputs, api: GitHubApi | None) -> BranchInfo:
    if not os.path.isdir(os.path.join(workspace(), ".git")):
        warning("GITHUB_WORKSPACE is not a git repository; skipping branch setup")
        return BranchInfo(base_branch=ctx.repository.default_branch, current_branch="", hermes_branch=None)

    default_branch = inputs.base_branch or ctx.repository.default_branch or "main"
    validate_branch_name(default_branch)

    if not ctx.has_entity:
        return BranchInfo(base_branch=default_branch, current_branch=current_branch())

    if ctx.is_pr:
        pr: dict[str, Any] = ctx.payload.get("pull_request") or {}
        if api is not None:
            try:
                pr = api.get_pull(ctx.entity_number or 0)
            except Exception as exc:  # noqa: BLE001 - best-effort branch setup
                warning(f"Could not fetch PR details: {exc}")
        base_branch = ((pr.get("base") or {}).get("ref") or default_branch)
        head = pr.get("head") or {}
        head_branch = head.get("ref") or os.environ.get("GITHUB_HEAD_REF") or current_branch()
        head_repo_full = ((head.get("repo") or {}).get("full_name") or ctx.repository.full_name)
        validate_branch_name(base_branch)
        if head_branch:
            validate_branch_name(head_branch)
        same_repo = head_repo_full.lower() == ctx.repository.full_name.lower()
        if same_repo and head_branch:
            notice(f"Open same-repo PR detected; checking out PR branch {head_branch}")
            _git(["fetch", "origin", head_branch, "--depth=50"])
            _git(["checkout", head_branch, "--"])
            return BranchInfo(base_branch=base_branch, current_branch=head_branch, is_new_branch=False)
        warning("Fork PR detected; creating a base-repo Hermes branch instead of pushing to fork")
        entity_type = "pr"
        sha = _source_sha(base_branch)
        new_branch = generate_branch_name(inputs.branch_name_template, inputs.branch_prefix, entity_type, ctx.entity_number or 0, sha, description=ctx.title)
        _git(["fetch", "origin", base_branch, "--depth=1"])
        _git(["checkout", base_branch, "--"])
        _git(["checkout", "-B", new_branch])
        return BranchInfo(base_branch=base_branch, current_branch=new_branch, hermes_branch=new_branch, is_new_branch=True, is_fork_pr=True)

    # Issue flow: create a new Hermes branch from base/default branch.
    sha = _source_sha(default_branch)
    new_branch = generate_branch_name(inputs.branch_name_template, inputs.branch_prefix, "issue", ctx.entity_number or 0, sha, description=ctx.title)
    _git(["fetch", "origin", default_branch, "--depth=1"])
    _git(["checkout", default_branch, "--"])
    _git(["checkout", "-B", new_branch])
    return BranchInfo(base_branch=default_branch, current_branch=new_branch, hermes_branch=new_branch, is_new_branch=True)


def branch_urls(ctx: GitHubContext, info: BranchInfo) -> tuple[str | None, str | None]:
    branch = info.hermes_branch or info.current_branch
    if not branch or branch == "HEAD":
        return None, None
    base = info.base_branch or ctx.repository.default_branch
    html = ctx.repository.html_url.rstrip("/")
    branch_url = f"{html}/tree/{branch}"
    compare_url = None
    if info.is_new_branch:
        compare_url = f"{html}/compare/{base}...{branch}?quick_pull=1"
    return branch_url, compare_url
