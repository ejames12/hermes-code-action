from __future__ import annotations

from .github_context import GitHubContext


def session_title_for_context(ctx: GitHubContext, max_length: int = 96) -> str:
    """Return a deterministic Hermes session title for issue/PR-triggered runs."""
    if not ctx.has_entity or not ctx.title.strip():
        return ""
    entity_type = "pr" if ctx.is_pr else "issue"
    raw_title = " ".join(ctx.title.split())
    title = f"{entity_type} #{ctx.entity_number}: {raw_title}"
    if len(title) > max_length:
        title = title[:max_length].rstrip()
    return title
