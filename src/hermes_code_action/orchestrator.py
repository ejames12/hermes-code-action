from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Callable

from .config import Inputs
from .hermes_runner import HermesResult, effective_model_info, run_hermes
from .policy import OrchestrationPolicy, StagePolicy
from .util import notice, truncate, workspace

_REVIEW_NO_EDIT_NOTICE = """
---
**REVIEWER CONSTRAINT**: You are in review-only mode. Do NOT make any file edits, git commits, git pushes, or branch operations. Your only output is a written review/assessment.
---

"""

_ADJUDICATE_NO_EDIT_NOTICE = """
---
**ADJUDICATOR CONSTRAINT**: You are in adjudication-only mode. Do NOT make any file edits, git commits, git pushes, or branch operations. Your only output is a final decision and written assessment.
---

"""

_STAGE_PREAMBLES = {
    "plan": "You are the **planner** for this task. Produce a clear implementation plan. Do not write final code yet unless explicitly instructed.\n\n",
    "implement": "You are the **implementer** for this task. If the base prompt says this is plan-only, improve/validate the plan without implementing application code. Otherwise execute the implementation faithfully, edit files, run tests, and commit changes as needed.\n\n",
    "review": _REVIEW_NO_EDIT_NOTICE + "You are the **reviewer** for this task. Critically assess the work done so far and identify issues.\n\n",
    "adjudicate": _ADJUDICATE_NO_EDIT_NOTICE + "You are the **adjudicator**. Review all prior stage outputs and reach a final verdict.\n\n",
}

_PRIOR_OUTPUT_HEADER = "\n\n---\n## Prior stage outputs\n\n"
_PRIOR_STAGE_LIMIT = 8_000
_STAGE_COMMENT_SUMMARY_LIMIT = 700
_CLAUDE_THROTTLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "rate limited",
    "too many requests",
    "429",
    "throttl",
    "overloaded",
    "capacity",
    "api_retry",
    "quota exceeded",
)
StageCompleteCallback = Callable[[StagePolicy, HermesResult, list[tuple[str, HermesResult]]], None]


def _stage_executor_notice(stage: StagePolicy) -> str:
    """Return stage-specific executor instructions for Hermes's inner run."""
    if not stage.claude_code_model:
        if stage.mode == "review":
            return (
                "## Stage executor\n"
                "Use Hermes's configured default model and tool harness for this review stage. "
                "Do NOT invoke Claude Code CLI for review-only requests.\n\n"
            )
        return ""

    allowed_tools = stage.claude_code_allowed_tools or "Read,Bash"
    max_turns = stage.max_turns or "20"
    mode_note = ""
    if stage.mode == "plan":
        mode_note = (
            "For plan-only requests, use Claude Code to inspect the repository and draft the plan with read-only tools. "
            "If the base prompt requires a plan file, synthesize Claude's plan into that file yourself and commit only the allowed plan artifacts.\n"
        )
    elif stage.mode == "implement":
        mode_note = "Use Claude Code for the implementation work, local checks, and any local commits the base prompt permits.\n"
    elif stage.mode == "adjudicate":
        mode_note = "Use Claude Code for read-only adjudication of prior stage output; do not edit files or commit.\n"

    return f"""## Required stage executor: Claude Code CLI
You MUST delegate the substantive `{stage.mode}` stage work to Claude Code CLI in print mode before producing this stage's result. Use this command shape and preserve the explicit model flag:

`claude -p "<stage task>" --model {stage.claude_code_model} --max-turns {max_turns} --allowedTools {allowed_tools}`

Do not use `claude --bare`; OAuth auth is provided through Claude Code CLI and bare mode may require `ANTHROPIC_API_KEY`. Do not let Claude Code push, merge, approve, or create PRs.
{mode_note}If Claude Code CLI is unavailable or fails, report that blocker instead of silently completing the stage with Hermes's default model.

Do NOT paste raw Claude Code transcripts, diffs, full prompts, or code blocks into your final answer. Return only a concise human summary; full details belong in GitHub Actions logs or artifacts.

"""


def _build_stage_prompt(base_prompt: str, stage: StagePolicy, prior_outputs: dict[str, str]) -> str:
    preamble = _STAGE_PREAMBLES.get(stage.mode, "")
    parts = [preamble, _stage_executor_notice(stage), base_prompt]

    if prior_outputs:
        parts.append(_PRIOR_OUTPUT_HEADER)
        if stage.must_consider:
            required = ", ".join(f"`{name}`" for name in stage.must_consider)
            parts.append(f"You must explicitly consider and triage findings from: {required}.\n\n")
        for name, output in prior_outputs.items():
            summary = truncate(output, _PRIOR_STAGE_LIMIT)
            marker = " (must consider)" if name in stage.must_consider else ""
            parts.append(f"### Stage: {name}{marker}\n\n{summary}\n\n")

    return "".join(parts)


def _args_contain_flag(args: list[str], flag: str) -> bool:
    """Return True when args contain `flag` as `flag value` or `flag=value`."""
    prefix = f"{flag}="
    return any(arg == flag or arg.startswith(prefix) for arg in args)


def _merge_stage_extra_args(global_args: str, stage_args: str) -> str:
    """Merge global and stage-specific Hermes args with stage flags taking precedence."""
    if not stage_args.strip():
        return global_args
    global_tokens = shlex.split(global_args) if global_args.strip() else []
    stage_tokens = shlex.split(stage_args)
    filtered_global: list[str] = []
    index = 0
    while index < len(global_tokens):
        token = global_tokens[index]
        if token.startswith("-") and _args_contain_flag(stage_tokens, token):
            if "=" not in token and index + 1 < len(global_tokens) and not global_tokens[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
            continue
        filtered_global.append(token)
        index += 1
    return " ".join(shlex.quote(arg) for arg in [*filtered_global, *stage_tokens])


def _stage_inputs(base_inputs: Inputs, stage: StagePolicy) -> Inputs:
    """Build an Inputs copy with stage-specific overrides.

    Stage-specific `extra_args` extend global `hermes_args`. If both set the same flag
    (for example `-s` or `--profile`), the stage-specific flag wins to avoid duplicate
    skill/profile args in the final Hermes command.
    """
    overrides: dict[str, object] = {}
    if stage.provider:
        overrides["hermes_provider"] = stage.provider
    if stage.model:
        overrides["hermes_model"] = stage.model
    if stage.toolsets:
        overrides["hermes_toolsets"] = stage.toolsets
    if stage.max_turns:
        overrides["hermes_max_turns"] = stage.max_turns
    if stage.extra_args:
        overrides["hermes_args"] = _merge_stage_extra_args(base_inputs.hermes_args, stage.extra_args)
    if not overrides:
        return base_inputs
    return dataclasses.replace(base_inputs, **overrides)


def _fallback_stage_inputs(base_inputs: Inputs, stage_inputs: Inputs) -> Inputs:
    """Return Inputs for a Hermes retry after Claude throttling.

    If no explicit fallback provider/model is configured, clear the stage's
    provider/model so Hermes uses the profile's configured default model.
    """
    return dataclasses.replace(
        stage_inputs,
        hermes_provider=base_inputs.hermes_fallback_provider,
        hermes_model=base_inputs.hermes_fallback_model,
        # Intentionally do not carry `-s claude-code` into fallback by default.
        hermes_args=base_inputs.hermes_fallback_args,
    )


def _looks_like_claude_throttle(result: HermesResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    if "claude" not in text and "anthropic" not in text:
        return False
    return any(marker in text for marker in _CLAUDE_THROTTLE_MARKERS)


def _annotate_model_info(
    result: HermesResult,
    inputs: Inputs,
    *,
    stage: StagePolicy | None = None,
    fallback_used: bool = False,
    primary: HermesResult | None = None,
) -> HermesResult:
    if stage is not None and stage.claude_code_model:
        provider, model = "Claude Code CLI", stage.claude_code_model
    else:
        provider, model = effective_model_info(inputs)
    return dataclasses.replace(
        result,
        provider=result.provider or provider,
        model=result.model or model,
        fallback_used=fallback_used or result.fallback_used,
        primary_provider=(primary.provider if primary else result.primary_provider),
        primary_model=(primary.model if primary else result.primary_model),
    )


def _model_summary(result: HermesResult) -> str:
    if result.provider and result.model:
        label = f"{result.provider} / {result.model}"
    elif result.model:
        label = result.model
    elif result.provider:
        label = f"{result.provider} / configured default model"
    else:
        label = "Hermes configured default model"
    if result.fallback_used:
        primary = ""
        if result.primary_provider and result.primary_model:
            primary = f"; primary attempt: {result.primary_provider} / {result.primary_model}"
        elif result.primary_model:
            primary = f"; primary attempt: {result.primary_model}"
        elif result.primary_provider:
            primary = f"; primary attempt: {result.primary_provider} / configured default model"
        return f"{label} (fallback after Claude throttling{primary})"
    return label


def _build_fallback_prompt(stage_prompt: str, failed_result: HermesResult, fallback_inputs: Inputs) -> str:
    failure_summary = truncate((failed_result.stderr or failed_result.stdout or "").strip(), 4_000)
    if fallback_inputs.hermes_provider or fallback_inputs.hermes_model:
        retry_target = "Hermes's configured secondary provider/model"
    else:
        retry_target = "Hermes's configured default model"
    return f"""{stage_prompt}

---
## Secondary Hermes fallback

The previous attempt for this stage appears to have failed because Claude Code CLI was throttled/rate-limited. Retry this stage using {retry_target}.

Do NOT invoke Claude Code CLI, `claude`, or the `claude-code` skill during this fallback attempt. Use Hermes's own model and available tools directly. Preserve the same stage role, safety constraints, git restrictions, and output expectations.

Previous failure summary:
{failure_summary or '(no failure details captured)'}
"""


def _fallback_result(primary: HermesResult, fallback: HermesResult) -> HermesResult:
    retry_label = "secondary Hermes model" if (fallback.provider or fallback.model) else "Hermes default model"
    stdout = (
        (primary.stdout or primary.stderr).strip()
        + f"\n\n---\nRetried with {retry_label} after Claude Code throttling.\n\n"
        + fallback.stdout.strip()
    ).strip()
    stderr = fallback.stderr
    return HermesResult(
        conclusion=fallback.conclusion,
        stdout=stdout,
        stderr=stderr,
        returncode=fallback.returncode,
        execution_file=fallback.execution_file,
        duration_seconds=primary.duration_seconds + fallback.duration_seconds,
        session_id=fallback.session_id or primary.session_id,
        provider=fallback.provider,
        model=fallback.model,
        fallback_used=True,
        primary_provider=primary.provider,
        primary_model=primary.model,
        hermes_args=fallback.hermes_args,
    )


def _git_state() -> tuple[str, str] | None:
    """Return (HEAD sha, porcelain status) for read-only stage enforcement."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace(),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace(),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        return None
    if head.returncode != 0 or status.returncode != 0:
        return None
    return (head.stdout.strip(), status.stdout)


def _fail_read_only_stage(stage: StagePolicy, result: HermesResult) -> HermesResult:
    message = (
        f"Read-only stage `{stage.name}` (mode={stage.mode}) changed the git state. "
        "Review and adjudication stages must not edit files, commit, or change branches."
    )
    stderr = (result.stderr + "\n" + message).strip()
    stdout = (result.stdout + "\n\n" + message).strip()
    return HermesResult(
        conclusion="failure",
        stdout=stdout,
        stderr=stderr,
        returncode=1,
        execution_file=result.execution_file,
        duration_seconds=result.duration_seconds,
        session_id=result.session_id,
        provider=result.provider,
        model=result.model,
        fallback_used=result.fallback_used,
        primary_provider=result.primary_provider,
        primary_model=result.primary_model,
        hermes_args=result.hermes_args,
    )


def _compact_stage_summary(result: HermesResult) -> str:
    """Return a short, GitHub-comment-friendly summary for one stage."""
    text = (result.stdout or result.stderr or "").strip()
    if not text:
        return "No stage output."
    # Collapse markdown/code-heavy agent output into one readable sentence so the final
    # GitHub issue comment stays compact and does not break formatting.
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    text = text.replace("|", "\\|")
    return truncate(text, _STAGE_COMMENT_SUMMARY_LIMIT, marker=" …[truncated]")


def _write_staged_execution_file(stage_results: list[tuple[str, HermesResult]], overall_conclusion: str) -> str:
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or "/tmp")
    runner_temp.mkdir(parents=True, exist_ok=True)
    execution_file = runner_temp / "hermes-execution-output.json"
    stages_payload = []
    for stage_name, r in stage_results:
        stages_payload.append({
            "stage": stage_name,
            "conclusion": r.conclusion,
            "returncode": r.returncode,
            "duration_seconds": r.duration_seconds,
            "provider": r.provider,
            "model": r.model,
            "fallback_used": r.fallback_used,
            "primary_provider": r.primary_provider,
            "primary_model": r.primary_model,
            "stdout_summary": truncate(r.stdout, 4_000),
            "stderr_summary": truncate(r.stderr, 2_000),
        })
    payload = {
        "orchestration_mode": "staged",
        "conclusion": overall_conclusion,
        "stages": stages_payload,
    }
    execution_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(execution_file)


def run_staged(
    base_prompt: str,
    inputs: Inputs,
    policy: OrchestrationPolicy,
    *,
    extra_env: dict[str, str] | None = None,
    on_stage_complete: StageCompleteCallback | None = None,
    session_title: str = "",
) -> HermesResult:
    """Run multiple Hermes invocations in stage order per the policy."""
    stage_results: list[tuple[str, HermesResult]] = []
    prior_outputs: dict[str, str] = {}
    overall_start = time.time()
    failed = False
    failed_stage = ""

    for stage in policy.stages:
        notice(f"[orchestrator] Starting stage: {stage.name} (mode={stage.mode})")
        stage_prompt = _build_stage_prompt(base_prompt, stage, prior_outputs)
        stage_inputs = _stage_inputs(inputs, stage)
        read_only_before = _git_state() if stage.mode in {"review", "adjudicate"} else None
        result = _annotate_model_info(
            run_hermes(stage_prompt, stage_inputs, extra_env=extra_env, session_title=session_title),
            stage_inputs,
            stage=stage,
        )
        fallback_inputs = _fallback_stage_inputs(inputs, stage_inputs)
        if stage.claude_code_model and _looks_like_claude_throttle(result):
            notice(
                f"[orchestrator] Stage {stage.name!r} appears Claude-throttled; "
                "retrying with Hermes fallback/default model."
            )
            fallback_prompt = _build_fallback_prompt(stage_prompt, result, fallback_inputs)
            fallback = _annotate_model_info(
                run_hermes(fallback_prompt, fallback_inputs, extra_env=extra_env, session_title=session_title),
                fallback_inputs,
                fallback_used=True,
                primary=result,
            )
            result = _fallback_result(result, fallback)
        if read_only_before is not None and result.success:
            read_only_after = _git_state()
            if read_only_after is not None and read_only_after != read_only_before:
                result = _fail_read_only_stage(stage, result)
        stage_results.append((stage.name, result))
        if on_stage_complete is not None:
            on_stage_complete(stage, result, list(stage_results))

        # Keep a summary of this stage's output for subsequent stages
        prior_outputs[stage.name] = (result.stdout or result.stderr).strip()

        if not result.success:
            notice(f"[orchestrator] Stage {stage.name!r} failed (rc={result.returncode}); stopping.")
            failed = True
            failed_stage = stage.name
            break

    total_duration = time.time() - overall_start
    overall_conclusion = "failure" if failed else "success"
    execution_file = _write_staged_execution_file(stage_results, overall_conclusion)

    # Build a compact, comment-friendly summary. Full per-stage stdout/stderr is still
    # preserved in the execution JSON and GitHub Actions logs.
    lines = ["### Stage summaries", ""]
    for stage_name, r in stage_results:
        icon = "✅" if r.success else "❌"
        duration = f"{r.duration_seconds:.1f}s"
        summary = _compact_stage_summary(r)
        model = _model_summary(r)
        lines.append(f"- **{stage_name}** {icon} `{r.conclusion}` ({duration}) — {model}: {summary}")

    if failed:
        lines.append("")
        lines.append(f"**Staged orchestration stopped at stage `{failed_stage}`.**")

    aggregated_stdout = "\n".join(lines)

    last_session_id = None
    for _, r in reversed(stage_results):
        if r.session_id:
            last_session_id = r.session_id
            break

    return HermesResult(
        conclusion=overall_conclusion,
        stdout=aggregated_stdout,
        stderr="",
        returncode=0 if not failed else 1,
        execution_file=execution_file,
        duration_seconds=total_duration,
        session_id=last_session_id,
    )
