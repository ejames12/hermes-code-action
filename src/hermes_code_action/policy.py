from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Inputs
from .plan import is_plan_request, is_review_request

_VALID_MODES = {"plan", "implement", "review", "adjudicate"}


@dataclass
class StagePolicy:
    name: str
    mode: str
    provider: str = ""
    model: str = ""
    toolsets: str = ""
    max_turns: str = ""
    extra_args: str = ""
    must_consider: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"Unknown stage mode {self.mode!r}. Must be one of: {sorted(_VALID_MODES)}")


@dataclass
class OrchestrationPolicy:
    stages: list[StagePolicy]


_DEFAULT_STAGES: list[StagePolicy] = [
    StagePolicy(name="planner", mode="plan"),
    StagePolicy(name="implementer", mode="implement"),
    StagePolicy(name="reviewer", mode="review"),
    StagePolicy(name="adjudicator", mode="adjudicate", must_consider=["reviewer"]),
]


def _parse_stage(raw: dict[str, Any]) -> StagePolicy:
    return StagePolicy(
        name=str(raw.get("name", "")),
        mode=str(raw.get("mode", "")),
        provider=str(raw.get("provider", "")),
        model=str(raw.get("model", "")),
        toolsets=str(raw.get("toolsets", "")),
        max_turns=str(raw.get("max_turns", "")),
        extra_args=str(raw.get("extra_args", "")),
        must_consider=list(raw.get("must_consider") or []),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except ImportError as exc:
        raise RuntimeError(
            f"Policy file {path} is a YAML file but PyYAML is not installed. "
            "Install PyYAML (`pip install pyyaml`) or convert the policy to JSON."
        ) from exc


def _load_policy_file(path: Path, workflow: str) -> OrchestrationPolicy:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = _load_yaml(path)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    version = data.get("version")
    if version != 1:
        raise ValueError(f"Unsupported policy version: {version!r}. Only version 1 is supported.")

    workflows: dict[str, Any] = data.get("workflows") or {}
    wf = workflows.get(workflow)
    if wf is None:
        raise ValueError(f"Workflow {workflow!r} not found in policy file {path}. Available: {list(workflows)}")

    raw_stages = wf.get("stages") or []
    stages = [_parse_stage(s) for s in raw_stages]
    return OrchestrationPolicy(stages=stages)


def _first_stage_with_mode(policy: OrchestrationPolicy, mode: str) -> OrchestrationPolicy:
    for stage in policy.stages:
        if stage.mode == mode:
            return OrchestrationPolicy(stages=[stage])
    # Fall back to the built-in stage for the requested mode if the policy omitted it.
    for stage in _DEFAULT_STAGES:
        if stage.mode == mode:
            return OrchestrationPolicy(stages=[stage])
    return OrchestrationPolicy(stages=[])


def _route_policy_for_request(policy: OrchestrationPolicy, user_request: str) -> OrchestrationPolicy | None:
    """Route natural @mrl-hermes commands to the smallest useful execution shape."""
    if is_plan_request(user_request):
        return _first_stage_with_mode(policy, "plan")
    if is_review_request(user_request):
        # Review requests should use one local/default Hermes invocation rather than the
        # full plan -> implement -> review -> adjudicate pipeline.
        return None
    return policy


def load_orchestration_policy(inputs: Inputs, user_request: str = "") -> OrchestrationPolicy | None:
    """Return an OrchestrationPolicy for staged mode, or None for single mode."""
    if inputs.orchestration_mode != "staged":
        return None

    policy_path_str = (inputs.orchestration_policy or "").strip()
    if not policy_path_str:
        policy = OrchestrationPolicy(stages=list(_DEFAULT_STAGES))
        return _route_policy_for_request(policy, user_request)

    policy_path = Path(policy_path_str)
    if not policy_path.exists():
        policy = OrchestrationPolicy(stages=list(_DEFAULT_STAGES))
        return _route_policy_for_request(policy, user_request)

    policy = _load_policy_file(policy_path, inputs.workflow or "default")
    return _route_policy_for_request(policy, user_request)
