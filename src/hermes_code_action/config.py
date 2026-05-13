from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
from typing import Iterable


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_extra_args(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value)


@dataclass(frozen=True)
class Inputs:
    trigger_phrase: str = "@hermes"
    assignee_trigger: str = ""
    label_trigger: str = "hermes"
    base_branch: str = ""
    branch_prefix: str = "hermes/"
    branch_name_template: str = ""
    allowed_bots: str = ""
    allowed_non_write_users: str = ""
    include_comments_by_actor: str = ""
    exclude_comments_by_actor: str = ""
    prompt: str = ""
    github_token: str = ""
    use_sticky_comment: bool = False
    track_progress: bool = False
    bot_id: str = "41898282"
    bot_name: str = "github-actions[bot]"
    display_report: bool = True
    show_full_output: bool = False
    path_to_hermes_executable: str = ""
    hermes_args: str = ""
    hermes_toolsets: str = "file,terminal,web"
    hermes_model: str = ""
    hermes_provider: str = ""
    hermes_fallback_model: str = ""
    hermes_fallback_provider: str = ""
    hermes_fallback_args: str = ""
    hermes_max_turns: str = "90"
    hermes_yolo: bool = True
    hermes_source: str = "github-action"
    timeout_seconds: int = 1800
    max_prompt_chars: int = 180_000
    dry_run: bool = False
    orchestration_mode: str = "single"
    orchestration_policy: str = ""
    workflow: str = "default"

    @property
    def allowed_bot_list(self) -> list[str]:
        return parse_csv(self.allowed_bots)

    @property
    def allowed_non_write_user_list(self) -> list[str]:
        return parse_csv(self.allowed_non_write_users)

    @property
    def include_actor_patterns(self) -> list[str]:
        return parse_csv(self.include_comments_by_actor)

    @property
    def exclude_actor_patterns(self) -> list[str]:
        return parse_csv(self.exclude_comments_by_actor)

    @property
    def hermes_extra_args(self) -> list[str]:
        return parse_extra_args(self.hermes_args)


def load_inputs() -> Inputs:
    token = env("INPUT_GITHUB_TOKEN") or env("GITHUB_TOKEN") or env("GH_TOKEN")
    timeout_raw = env("INPUT_TIMEOUT_SECONDS", "1800")
    max_prompt_raw = env("INPUT_MAX_PROMPT_CHARS", "180000")
    try:
        timeout_seconds = int(timeout_raw)
    except ValueError:
        timeout_seconds = 1800
    try:
        max_prompt_chars = int(max_prompt_raw)
    except ValueError:
        max_prompt_chars = 180_000

    return Inputs(
        trigger_phrase=env("INPUT_TRIGGER_PHRASE", "@hermes") or "@hermes",
        assignee_trigger=env("INPUT_ASSIGNEE_TRIGGER"),
        label_trigger=env("INPUT_LABEL_TRIGGER", "hermes") or "hermes",
        base_branch=env("INPUT_BASE_BRANCH"),
        branch_prefix=env("INPUT_BRANCH_PREFIX", "hermes/") or "hermes/",
        branch_name_template=env("INPUT_BRANCH_NAME_TEMPLATE"),
        allowed_bots=env("INPUT_ALLOWED_BOTS"),
        allowed_non_write_users=env("INPUT_ALLOWED_NON_WRITE_USERS"),
        include_comments_by_actor=env("INPUT_INCLUDE_COMMENTS_BY_ACTOR"),
        exclude_comments_by_actor=env("INPUT_EXCLUDE_COMMENTS_BY_ACTOR"),
        prompt=env("INPUT_PROMPT"),
        github_token=token,
        use_sticky_comment=parse_bool(env("INPUT_USE_STICKY_COMMENT"), False),
        track_progress=parse_bool(env("INPUT_TRACK_PROGRESS"), False),
        bot_id=env("INPUT_BOT_ID", "41898282") or "41898282",
        bot_name=env("INPUT_BOT_NAME", "github-actions[bot]") or "github-actions[bot]",
        display_report=parse_bool(env("INPUT_DISPLAY_REPORT"), True),
        show_full_output=parse_bool(env("INPUT_SHOW_FULL_OUTPUT"), False),
        path_to_hermes_executable=env("INPUT_PATH_TO_HERMES_EXECUTABLE"),
        hermes_args=env("INPUT_HERMES_ARGS"),
        hermes_toolsets=env("INPUT_HERMES_TOOLSETS", "file,terminal,web") or "file,terminal,web",
        hermes_model=env("INPUT_HERMES_MODEL"),
        hermes_provider=env("INPUT_HERMES_PROVIDER"),
        hermes_fallback_model=env("INPUT_HERMES_FALLBACK_MODEL"),
        hermes_fallback_provider=env("INPUT_HERMES_FALLBACK_PROVIDER"),
        hermes_fallback_args=env("INPUT_HERMES_FALLBACK_ARGS"),
        hermes_max_turns=env("INPUT_HERMES_MAX_TURNS", "90") or "90",
        hermes_yolo=parse_bool(env("INPUT_HERMES_YOLO"), True),
        hermes_source=env("INPUT_HERMES_SOURCE", "github-action") or "github-action",
        timeout_seconds=timeout_seconds,
        max_prompt_chars=max_prompt_chars,
        dry_run=parse_bool(env("INPUT_DRY_RUN"), False),
        orchestration_mode=env("INPUT_ORCHESTRATION_MODE", "single") or "single",
        orchestration_policy=env("INPUT_ORCHESTRATION_POLICY"),
        workflow=env("INPUT_WORKFLOW", "default") or "default",
    )


def csv_contains(value: str, candidates: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(item == "*" or item.lower() == lowered for item in candidates)
