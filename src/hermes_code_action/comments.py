from __future__ import annotations

from dataclasses import dataclass
import re
import time

from .github_context import GitHubContext
from .util import truncate


@dataclass
class TrackingComment:
    id: int | None
    html_url: str | None
    kind: str = "issue"


def initial_comment_body(ctx: GitHubContext, run_url: str) -> str:
    return f"""## Hermes is working ⏳

@{ctx.actor}, I picked this up and will report back here when the run finishes.

- [x] Trigger received
- [ ] Repository context collected
- [ ] Hermes execution completed
- [ ] Final result posted

[View GitHub Actions run]({run_url})
"""


def _duration(start: float, end: float | None = None) -> str:
    seconds = int((end or time.time()) - start)
    minutes, sec = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def final_comment_body(
    ctx: GitHubContext,
    *,
    success: bool,
    started_at: float,
    run_url: str,
    branch_name: str | None,
    branch_url: str | None,
    compare_url: str | None,
    output: str,
    show_full_output: bool = False,
    plan_url: str | None = None,
    push_message: str | None = None,
) -> str:
    status = "finished" if success else "encountered an error"
    icon = "✅" if success else "❌"
    output = _strip_ansi(output or "").strip()
    if not show_full_output:
        output = truncate(output, 6000)
    links = [f"[View run]({run_url})"]
    if branch_name and branch_url:
        links.append(f"[`{branch_name}`]({branch_url})")
    if compare_url:
        links.append(f"[Create PR ➔]({compare_url})")
    if plan_url:
        links.append(f"[View plan]({plan_url})")
    link_line = " • ".join(links)
    body = f"""## {icon} Hermes {status} @{ctx.actor}'s task in {_duration(started_at)}

{link_line}

- [x] Trigger received
- [x] Repository context collected
- [x] Hermes execution completed
- [x] Final result posted
"""
    if push_message:
        body += f"- {push_message}\n"
    body += """
### Hermes result

"""
    if output:
        body += output
    else:
        body += "Hermes did not return any output. Check the workflow logs for details."
    return body + "\n"
