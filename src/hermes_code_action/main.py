from __future__ import annotations

import os
from pathlib import Path
import time
import traceback

from .branch import (
    BranchInfo,
    PushInfo,
    branch_urls,
    configure_git_auth,
    install_no_protected_branch_push_hook,
    push_working_branch,
    remove_git_push_credentials,
    setup_branch,
)
from .comments import TrackingComment, final_comment_body, initial_comment_body
from .config import load_inputs
from .github_api import GitHubApi
from .github_context import parse_context
from .hermes_runner import HermesResult, run_hermes
from .orchestrator import run_staged
from .plan import PlanInfo, assert_plan_only_changes, build_plan_info, current_head_sha, is_plan_request
from .policy import load_orchestration_policy
from .prompt import build_prompt, collect_github_data
from .security import validate_actor
from .tracking import TrackingCommentServer, TrackingTool, start_tracking_tool
from .triggers import detect_trigger
from .util import append_step_summary, error, mask, notice, run_url, set_output, truncate, warning, workspace


def create_tracking_comment(api: GitHubApi | None, ctx, body: str) -> TrackingComment:
    if api is None or not ctx.has_entity:
        return TrackingComment(None, None)
    created = api.create_issue_comment(ctx.entity_number, body)
    return TrackingComment(id=created.get("id"), html_url=created.get("html_url"), kind="issue")


def update_tracking_comment(api: GitHubApi | None, tracking: TrackingComment, body: str) -> None:
    if api is None or not tracking.id:
        return
    try:
        api.update_issue_comment(tracking.id, body)
    except Exception as exc:  # noqa: BLE001
        warning(f"Could not update tracking comment: {exc}")


def write_prompt_file(prompt: str) -> str:
    prompt_dir = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "hermes-prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / "hermes-prompt.md"
    path.write_text(prompt, encoding="utf-8")
    return str(path)


def summarize_result(result: HermesResult | None, branch: BranchInfo | None) -> str:
    lines = ["# Hermes Code Action Report", ""]
    if result is None:
        lines.append("Hermes did not run.")
    else:
        lines.extend(
            [
                f"- Conclusion: `{result.conclusion}`",
                f"- Exit code: `{result.returncode}`",
                f"- Duration: `{result.duration_seconds:.1f}s`",
                f"- Execution file: `{result.execution_file}`",
            ]
        )
        if result.session_id:
            lines.append(f"- Session ID: `{result.session_id}`")
    if branch is not None:
        lines.append(f"- Base branch: `{branch.base_branch}`")
        lines.append(f"- Working branch: `{branch.hermes_branch or branch.current_branch}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    started_at = time.time()
    inputs = load_inputs()
    if inputs.github_token:
        mask(inputs.github_token)

    ctx = parse_context()
    notice(f"Repository: {ctx.repository.full_name}")
    notice(f"Event: {ctx.event_name}.{ctx.event_action or '(no action)'}")
    notice(f"Actor: {ctx.actor}")

    api = None
    if inputs.github_token and ctx.repository.owner and ctx.repository.repo:
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        api = GitHubApi(inputs.github_token, ctx.repository.owner, ctx.repository.repo, api_url=api_url)

    set_output("structured_output", "")
    set_output("orchestration_summary", "")
    set_output("plan_file", "")
    set_output("plan_url", "")

    decision = detect_trigger(ctx, inputs)
    notice(f"Mode: {decision.mode}; trigger decision: {decision.should_run} ({decision.reason})")
    if not decision.should_run:
        set_output("github_token", inputs.github_token)
        set_output("conclusion", "skipped")
        return 0

    validate_actor(ctx, inputs, api)

    tracking = TrackingComment(None, None)
    run_link = run_url(ctx.repository.owner, ctx.repository.repo)
    branch_info = BranchInfo(base_branch=ctx.repository.default_branch, current_branch="")
    push_info = PushInfo()
    plan_info: PlanInfo | None = None
    tracking_server: TrackingCommentServer | None = None
    tracking_tool: TrackingTool | None = None
    start_head = ""
    result: HermesResult | None = None

    try:
        if decision.mode == "tag" and ctx.has_entity:
            tracking = create_tracking_comment(api, ctx, initial_comment_body(ctx, run_link))
            if tracking.id:
                set_output("hermes_comment_id", str(tracking.id))

        if api is not None:
            configure_git_auth(inputs.github_token, ctx, inputs)
        branch_info = setup_branch(ctx, inputs, api) if decision.mode == "tag" else BranchInfo(ctx.repository.default_branch, os.environ.get("GITHUB_REF_NAME", ""))
        set_output("branch_name", branch_info.hermes_branch or branch_info.current_branch)
        install_no_protected_branch_push_hook(ctx, branch_info)
        remove_git_push_credentials(ctx)
        start_head = current_head_sha()

        plan_requested = decision.mode == "tag" and is_plan_request(decision.user_request)
        if plan_requested:
            plan_info = build_plan_info(ctx, branch_info)
            set_output("plan_file", plan_info.file_path)
            set_output("plan_url", plan_info.web_url or "")

        tracking_server, tracking_tool = start_tracking_tool(api, tracking)

        data = collect_github_data(ctx, inputs, api, branch_info)
        prompt = build_prompt(
            ctx,
            inputs,
            decision.reason,
            decision.user_request,
            data,
            branch_info,
            tracking.id,
            run_link,
            plan_info,
            tracking_tool.command_hint if tracking_tool else None,
        )
        prompt_file = write_prompt_file(prompt)
        set_output("prompt_file", prompt_file)
        notice(f"Prompt written to {prompt_file} ({len(prompt)} chars)")

        orchestration_policy = load_orchestration_policy(inputs)
        if orchestration_policy is not None:
            notice(f"Staged orchestration active: {len(orchestration_policy.stages)} stages")
            result = run_staged(
                prompt,
                inputs,
                orchestration_policy,
                extra_env=tracking_tool.env if tracking_tool else None,
            )
        else:
            result = run_hermes(prompt, inputs, extra_env=tracking_tool.env if tracking_tool else None)
        if tracking_server is not None:
            tracking_server.stop()
            tracking_server = None
        if result.success and plan_info is not None:
            assert_plan_only_changes(plan_info, start_head)
        if result.success and not inputs.dry_run and decision.mode == "tag":
            push_info = push_working_branch(inputs.github_token, ctx, inputs, branch_info)
        set_output("execution_file", result.execution_file)
        set_output("session_id", result.session_id or "")
        set_output("conclusion", result.conclusion)
        if orchestration_policy is not None:
            stage_names = ",".join(s.name for s in orchestration_policy.stages)
            set_output("orchestration_summary", f"staged:{stage_names}:{result.conclusion}")
        set_output("github_token", inputs.github_token)

        branch_url, compare_url = branch_urls(ctx, branch_info)
        final_output = result.stdout if result.stdout.strip() else result.stderr
        update_tracking_comment(
            api,
            tracking,
            final_comment_body(
                ctx,
                success=result.success,
                started_at=started_at,
                run_url=run_link,
                branch_name=branch_info.hermes_branch or branch_info.current_branch,
                branch_url=branch_url,
                compare_url=compare_url,
                output=final_output,
                show_full_output=inputs.show_full_output,
                plan_url=plan_info.web_url if plan_info else None,
                push_message=push_info.message or None,
            ),
        )
        if inputs.display_report:
            append_step_summary(summarize_result(result, branch_info))
        return 0 if result.success else 1
    except Exception as exc:  # noqa: BLE001
        if tracking_server is not None:
            try:
                tracking_server.stop()
            except Exception:  # noqa: BLE001
                pass
        error(str(exc))
        traceback.print_exc()
        set_output("conclusion", "failure")
        set_output("github_token", inputs.github_token)
        branch_url, compare_url = branch_urls(ctx, branch_info)
        update_tracking_comment(
            api,
            tracking,
            final_comment_body(
                ctx,
                success=False,
                started_at=started_at,
                run_url=run_link,
                branch_name=branch_info.hermes_branch or branch_info.current_branch,
                branch_url=branch_url,
                compare_url=compare_url,
                output=f"Action failed before completion:\n\n```text\n{truncate(str(exc), 4000)}\n```",
                show_full_output=True,
                plan_url=plan_info.web_url if plan_info else None,
                push_message=push_info.message or None,
            ),
        )
        if inputs.display_report:
            append_step_summary(summarize_result(result, branch_info))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
