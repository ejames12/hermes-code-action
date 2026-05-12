from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
from typing import Any


def notice(message: str) -> None:
    print(message, flush=True)


def warning(message: str) -> None:
    print(f"::warning::{message}", flush=True)


def error(message: str) -> None:
    print(f"::error::{message}", flush=True)


def mask(value: str | None) -> None:
    if value:
        print(f"::add-mask::{value}", flush=True)


def set_output(name: str, value: str | None) -> None:
    if value is None:
        value = ""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            if "\n" in value:
                marker = f"EOF_{name}"
                fh.write(f"{name}<<{marker}\n{value}\n{marker}\n")
            else:
                fh.write(f"{name}={value}\n")
    else:
        print(f"::set-output name={name}::{value}", flush=True)


def append_step_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(markdown)
        if not markdown.endswith("\n"):
            fh.write("\n")


def run(args: list[str], *, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    notice("+ " + " ".join(args))
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="")
    if check and completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(args)}")
    return completed


def truncate(text: str, limit: int, marker: str = "\n\n...[truncated]...") -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def workspace() -> str:
    return os.environ.get("GITHUB_WORKSPACE") or os.getcwd()


def run_url(owner: str, repo: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id:
        return f"{server}/{owner}/{repo}/actions"
    return f"{server}/{owner}/{repo}/actions/runs/{run_id}"


def actor_matches(actor: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    actor_l = actor.lower()
    for pattern in patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        if p == "*":
            return True
        if p.startswith("*") and actor_l.endswith(p[1:]):
            return True
        if p.endswith("*") and actor_l.startswith(p[:-1]):
            return True
        if p == actor_l:
            return True
    return False


def actor_allowed_by_filters(actor: str, include: list[str], exclude: list[str]) -> bool:
    if exclude and actor_matches(actor, exclude):
        return False
    if include:
        return actor_matches(actor, include)
    return True


@dataclass
class GitResult:
    changed: bool
    status: str


def git_status(cwd: str | None = None) -> GitResult:
    if not Path(cwd or os.getcwd(), ".git").exists():
        return GitResult(False, "")
    completed = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, text=True, capture_output=True)
    status = completed.stdout.strip()
    return GitResult(bool(status), status)
