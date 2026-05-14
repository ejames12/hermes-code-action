from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import re
import time

from .github_context import GitHubContext
from .hermes_runner import HermesResult
from .util import truncate


_HUMAN_ATTENTION_MARKERS = (
    "needs human",
    "human review",
    "human attention",
    "needs review",
    "take a look",
    "manual review",
    "manual decision",
    "manual action",
    "blocked",
    "blocker",
    "unresolved",
    "requires approval",
    "merge conflict",
    "failing test",
    "failed test",
    "security",
    "critical",
    "high risk",
    "cannot",
    "unable",
)

_NO_HUMAN_ATTENTION_PATTERNS = (
    r"\bno\s+(human|manual)\s+(review|action)\s+(needed|required)\b",
    r"\bno\s+issues?\b",
    r"\bno\s+findings?\b",
    r"\bno\s+blockers?\b",
)


@dataclass
class TrackingComment:
    id: int | None
    html_url: str | None
    kind: str = "issue"


def _model_label(result: HermesResult, *, markdown: bool = True) -> str:
    def fmt(value: str) -> str:
        return f"`{value}`" if markdown else value

    if result.provider and result.model:
        label = f"{fmt(result.provider)} / {fmt(result.model)}"
    elif result.model:
        label = fmt(result.model)
    elif result.provider:
        label = f"{fmt(result.provider)} / configured default model"
    else:
        label = "Hermes configured default model"

    if result.fallback_used:
        primary = ""
        if result.primary_provider and result.primary_model:
            primary = f"; primary attempt: {fmt(result.primary_provider)} / {fmt(result.primary_model)}"
        elif result.primary_model:
            primary = f"; primary attempt: {fmt(result.primary_model)}"
        elif result.primary_provider:
            primary = f"; primary attempt: {fmt(result.primary_provider)} / configured default model"
        return f"{label} (fallback after Claude throttling{primary})"
    return label


def _stage_summary(result: HermesResult, limit: int = 1_200) -> str:
    source = (result.stdout or result.stderr) if result.success else (result.stderr or result.stdout)
    text = _strip_ansi((source or "").strip())
    if not text:
        return "No stage output."
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return truncate(text, limit)


def _stage_line(stage_name: str, result: HermesResult | None = None) -> str:
    if result is None:
        return f"- [ ] {stage_name}"
    icon = "✅" if result.success else "❌"
    return f"- [x] {stage_name} — {icon} `{result.conclusion}` ({result.duration_seconds:.1f}s) — {_model_label(result)}"


def _stage_checklist(stage_names: Sequence[str], stage_results: Sequence[tuple[str, HermesResult]] | None = None) -> str:
    completed = {name: result for name, result in (stage_results or [])}
    lines = []
    for stage_name in stage_names:
        lines.append(_stage_line(stage_name, completed.get(stage_name)))
    for stage_name, result in (stage_results or []):
        if stage_name not in stage_names:
            lines.append(_stage_line(stage_name, result))
    return "\n".join(lines)


def _links_line(
    run_url: str,
    *,
    branch_name: str | None = None,
    branch_url: str | None = None,
    compare_url: str | None = None,
    plan_url: str | None = None,
) -> str:
    links = [f"[View run]({run_url})"]
    if branch_name and branch_url:
        links.append(f"[`{branch_name}`]({branch_url})")
    if compare_url:
        links.append(f"[Create PR ➔]({compare_url})")
    if plan_url:
        links.append(f"[View plan]({plan_url})")
    return " • ".join(links)


def initial_comment_body(ctx: GitHubContext, run_url: str, stage_names: Sequence[str] | None = None) -> str:
    body = f"""## Hermes is working ⏳

@{ctx.actor}, I picked this up and will report back here when the run finishes.

- [x] Trigger received
- [ ] Repository context collected
- [ ] Hermes execution completed
- [ ] Final result posted

[View GitHub Actions run]({run_url})
"""
    if stage_names:
        body += f"""

### Planned stages

{_stage_checklist(stage_names)}

Stage summary comments are posted separately as each stage finishes.
"""
    return body


def _duration(start: float, end: float | None = None) -> str:
    seconds = int((end or time.time()) - start)
    minutes, sec = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _attention_reason(stage_mode: str, result: HermesResult, summary: str) -> str | None:
    text = f"{result.stdout}\n{result.stderr}\n{summary}".lower()
    if not result.success:
        return "this stage did not complete successfully."
    if any(re.search(pattern, text) for pattern in _NO_HUMAN_ATTENTION_PATTERNS):
        return None
    if any(marker in text for marker in _HUMAN_ATTENTION_MARKERS):
        return "the stage output indicates a human may need to review or decide something."
    if stage_mode in {"review", "adjudicate"} and re.search(r"\b(finding|concern|risk|regression)\b", text):
        return "the review/adjudication output contains findings or risks."
    return None


def _assignee_mentions(assignees: Sequence[str]) -> str:
    mentions: list[str] = []
    seen: set[str] = set()
    for assignee in assignees:
        login = assignee.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9-]+", login):
            continue
        key = login.lower()
        if key in seen:
            continue
        seen.add(key)
        mentions.append(f"@{login}")
    return " ".join(mentions)


def staged_tracking_comment_body(
    ctx: GitHubContext,
    *,
    run_url: str,
    stage_names: Sequence[str],
    stage_results: Sequence[tuple[str, HermesResult]],
    started_at: float,
    final: bool = False,
    success: bool | None = None,
    branch_name: str | None = None,
    branch_url: str | None = None,
    compare_url: str | None = None,
    plan_url: str | None = None,
    push_message: str | None = None,
) -> str:
    if final:
        ok = bool(success)
        icon = "✅" if ok else "❌"
        status = "finished" if ok else "stopped with an error"
        header = f"## {icon} Hermes {status} in {_duration(started_at)}"
    else:
        header = "## Hermes is working ⏳"

    hermes_done = final or len(stage_results) >= len(stage_names)
    final_posted = final
    progress = "\n".join([
        "- [x] Trigger received",
        "- [x] Repository context collected",
        f"- [{'x' if hermes_done else ' '}] Hermes execution completed",
        f"- [{'x' if final_posted else ' '}] Final result posted",
    ])
    body = f"""{header}

@{ctx.actor} — staged run progress. {_links_line(run_url, branch_name=branch_name, branch_url=branch_url, compare_url=compare_url, plan_url=plan_url)}

### Progress

{progress}

### Stages

{_stage_checklist(stage_names, stage_results)}

Stage summary comments are posted separately as each stage finishes.
"""
    if push_message:
        body += f"\n{push_message}\n"
    return body


def stage_summary_comment_body(
    ctx: GitHubContext,
    *,
    stage_name: str,
    stage_mode: str,
    result: HermesResult,
    run_url: str,
    assignees: Sequence[str] = (),
    stage_number: int | None = None,
    total_stages: int | None = None,
) -> str:
    icon = "✅" if result.success else "❌"
    ordinal = f"Stage {stage_number}/{total_stages}" if stage_number and total_stages else "Stage complete"
    summary = _stage_summary(result)
    body = f"""## {icon} Hermes stage: {stage_name}

**{ordinal}** • **Mode:** `{stage_mode}` • **Status:** `{result.conclusion}` • **Duration:** `{result.duration_seconds:.1f}s` • [View run]({run_url})

**Serviced by:** {_model_label(result)}

### Summary

{summary}
"""
    reason = _attention_reason(stage_mode, result, summary)
    if reason:
        mentions = _assignee_mentions(assignees)
        body += "\n### Human attention\n\n"
        if mentions:
            body += f"{mentions} — please take a look; {reason}\n"
        else:
            body += f"No issue assignees are set, but a maintainer should review this stage because {reason}\n"
    return body


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
    body = f"""## {icon} Hermes {status} in {_duration(started_at)}

@{ctx.actor} — run complete. {link_line}
"""
    if push_message:
        body += f"\n{push_message}\n"
    body += """
### Summary

"""
    if output:
        body += output
    else:
        body += "Hermes did not return any output. Check the workflow logs for details."
    return body + "\n"
