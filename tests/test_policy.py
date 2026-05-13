from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.policy import StagePolicy, load_orchestration_policy


class PolicyTests(unittest.TestCase):
    def test_loads_staged_policy_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp, "code-action.json")
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workflows": {
                            "default": {
                                "stages": [
                                    {
                                        "name": "planner",
                                        "mode": "plan",
                                        "provider": "anthropic",
                                        "model": "claude-opus-4.7",
                                        "toolsets": "file,terminal,web",
                                    },
                                    {
                                        "name": "implementer",
                                        "mode": "implement",
                                        "provider": "anthropic",
                                        "model": "claude-sonnet-4.5",
                                        "toolsets": "file,terminal",
                                    },
                                    {
                                        "name": "reviewer",
                                        "mode": "review",
                                        "provider": "openai",
                                        "model": "gpt-5.1",
                                        "toolsets": "file,terminal",
                                    },
                                    {
                                        "name": "adjudicator",
                                        "mode": "adjudicate",
                                        "provider": "anthropic",
                                        "model": "claude-sonnet-4.5",
                                        "must_consider": ["reviewer"],
                                    },
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            policy = load_orchestration_policy(
                Inputs(orchestration_mode="staged", orchestration_policy=str(policy_path), workflow="default")
            )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual([stage.name for stage in policy.stages], ["planner", "implementer", "reviewer", "adjudicator"])
        self.assertEqual(policy.stages[0].model, "claude-opus-4.7")
        self.assertEqual(policy.stages[2].provider, "openai")
        self.assertEqual(policy.stages[3].must_consider, ["reviewer"])

    def test_staged_mode_without_policy_uses_safe_default(self) -> None:
        policy = load_orchestration_policy(Inputs(orchestration_mode="staged"))
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual([stage.name for stage in policy.stages], ["planner", "implementer", "reviewer", "adjudicator"])
        self.assertEqual(policy.stages[0].mode, "plan")
        self.assertEqual(policy.stages[-1].mode, "adjudicate")

    def test_single_mode_disables_policy(self) -> None:
        self.assertIsNone(load_orchestration_policy(Inputs(orchestration_mode="single")))

    def test_plan_request_routes_to_plan_stage_only(self) -> None:
        policy = load_orchestration_policy(Inputs(orchestration_mode="staged"), "plan the refactor")
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertEqual([stage.mode for stage in policy.stages], ["plan"])

    def test_review_request_routes_to_single_hermes_invocation(self) -> None:
        policy = load_orchestration_policy(Inputs(orchestration_mode="staged"), "review this PR")
        self.assertIsNone(policy)

    def test_stage_policy_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            StagePolicy(name="x", mode="merge")


if __name__ == "__main__":
    unittest.main()
