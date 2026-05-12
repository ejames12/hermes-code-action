from __future__ import annotations

from .config import Inputs, csv_contains
from .github_api import GitHubApi, GitHubApiError
from .github_context import GitHubContext
from .util import notice, warning

WRITE_PERMISSIONS = {"admin", "maintain", "write"}


def is_bot(actor: str) -> bool:
    return actor.endswith("[bot]") or actor.lower().endswith("-bot")


def validate_actor(ctx: GitHubContext, inputs: Inputs, api: GitHubApi | None) -> None:
    actor = ctx.actor
    if not actor:
        raise RuntimeError("Cannot determine GitHub actor from event payload")

    if is_bot(actor) and not csv_contains(actor, inputs.allowed_bot_list):
        raise RuntimeError(
            f"Refusing to run for bot actor {actor!r}. Set allowed_bots to this bot name if intentional."
        )

    if not ctx.has_entity:
        # Automation mode with explicit prompt relies on workflow author controls.
        return

    if inputs.allowed_non_write_users == "*" or actor in inputs.allowed_non_write_user_list:
        warning(
            "allowed_non_write_users is enabled. This can expose a write-capable workflow to prompt injection."
        )
        return

    if api is None:
        warning("No GitHub token available; cannot verify actor write permission")
        return

    try:
        permission = api.get_permission(actor)
    except GitHubApiError as exc:
        warning(f"Could not verify actor permission through GitHub API: {exc}")
        return
    notice(f"Actor {actor} repository permission: {permission}")
    if permission not in WRITE_PERMISSIONS:
        raise RuntimeError(
            f"Actor {actor!r} has {permission!r} permission; write/maintain/admin required for @hermes runs."
        )
