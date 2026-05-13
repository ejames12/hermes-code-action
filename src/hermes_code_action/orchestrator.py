from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import subprocess
import time

from .config import Inputs
from .hermes_runner import HermesResult, run_hermes
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


def _build_stage_prompt(base_prompt: str, stage: StagePolicy, prior_outputs: dict[str, str]) -> str:
    preamble = _STAGE_PREAMBLES.get(stage.mode, "")
    parts = [preamble, base_prompt]

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


def _stage_inputs(base_inputs: Inputs, stage: StagePolicy) -> Inputs:
    """Build an Inputs copy with stage-specific overrides."""
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
        overrides["hermes_args"] = stage.extra_args
    if not overrides:
        return base_inputs
    return dataclasses.replace(base_inputs, **overrides)


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
    )


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
        result = run_hermes(stage_prompt, stage_inputs, extra_env=extra_env)
        if read_only_before is not None and result.success:
            read_only_after = _git_state()
            if read_only_after is not None and read_only_after != read_only_before:
                result = _fail_read_only_stage(stage, result)
        stage_results.append((stage.name, result))

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

    # Build aggregated stdout/stderr for comment rendering
    summaries: list[str] = []
    for stage_name, r in stage_results:
        out = (r.stdout or r.stderr).strip()
        summaries.append(f"## Stage: {stage_name}\n\n{truncate(out, 4_000)}")
    aggregated_stdout = "\n\n".join(summaries)

    if failed:
        aggregated_stdout += f"\n\n**Staged orchestration stopped at stage `{failed_stage}`.**"

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
