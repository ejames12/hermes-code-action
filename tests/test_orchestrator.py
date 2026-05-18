from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.hermes_runner import HermesResult
from hermes_code_action.orchestrator import (
    _build_stage_prompt,
    _compact_stage_summary,
    _fallback_stage_inputs,
    _looks_like_claude_throttle,
    _stage_inputs,
    run_staged,
)
from hermes_code_action.policy import OrchestrationPolicy, StagePolicy


def _success_result(stdout: str = "ok", **kwargs) -> HermesResult:
    return HermesResult(
        conclusion="success",
        stdout=stdout,
        stderr="",
        returncode=0,
        execution_file="/tmp/exec.json",
        duration_seconds=0.1,
        **kwargs,
    )


def _failure_result(stdout: str = "", stderr: str = "boom") -> HermesResult:
    return HermesResult(
        conclusion="failure",
        stdout=stdout,
        stderr=stderr,
        returncode=1,
        execution_file="/tmp/exec.json",
        duration_seconds=0.1,
    )


class StagedPromptTests(unittest.TestCase):
    def test_preamble_injected_per_mode(self) -> None:
        stage = StagePolicy(name="planner", mode="plan")
        prompt = _build_stage_prompt("do the thing", stage, {})
        self.assertIn("planner", prompt.lower())
        self.assertIn("do the thing", prompt)

    def test_review_prompt_contains_no_edit_notice(self) -> None:
        stage = StagePolicy(name="reviewer", mode="review")
        prompt = _build_stage_prompt("review this", stage, {})
        self.assertIn("REVIEWER CONSTRAINT", prompt)
        self.assertIn("Do NOT make any file edits", prompt)

    def test_adjudicate_prompt_contains_no_edit_notice(self) -> None:
        stage = StagePolicy(name="adjudicator", mode="adjudicate")
        prompt = _build_stage_prompt("adjudicate", stage, {})
        self.assertIn("ADJUDICATOR CONSTRAINT", prompt)
        self.assertIn("Do NOT make any file edits", prompt)

    def test_must_consider_injects_prior_output(self) -> None:
        stage = StagePolicy(name="adjudicator", mode="adjudicate", must_consider=["reviewer"])
        prior = {"reviewer": "LGTM with concerns", "implementer": "done"}
        prompt = _build_stage_prompt("decide", stage, prior)
        self.assertIn("reviewer", prompt)
        self.assertIn("LGTM with concerns", prompt)
        # implementer not in must_consider but still injected because prior_outputs present
        self.assertIn("implementer", prompt)

    def test_no_prior_outputs_not_injected_for_plan(self) -> None:
        stage = StagePolicy(name="planner", mode="plan")
        prompt = _build_stage_prompt("plan something", stage, {})
        self.assertNotIn("Prior stage outputs", prompt)

    def test_plan_stage_requires_claude_code_opus_without_pasting_transcripts(self) -> None:
        stage = StagePolicy(name="planner", mode="plan")
        prompt = _build_stage_prompt("plan something", stage, {})
        self.assertIn("Required stage executor: Claude Code CLI", prompt)
        self.assertIn("--model opus", prompt)
        self.assertIn("--allowedTools Read,Bash", prompt)
        self.assertIn("Do NOT paste raw Claude Code transcripts", prompt)


class StageInputsTests(unittest.TestCase):
    def test_stage_overrides_model_and_provider(self) -> None:
        base = Inputs(hermes_model="base-model", hermes_provider="anthropic")
        stage = StagePolicy(name="s", mode="implement", model="override-model", provider="openai")
        result = _stage_inputs(base, stage)
        self.assertEqual(result.hermes_model, "override-model")
        self.assertEqual(result.hermes_provider, "openai")
        # Other fields unchanged
        self.assertEqual(result.dry_run, base.dry_run)

    def test_stage_empty_overrides_leave_base_unchanged(self) -> None:
        base = Inputs(hermes_model="base-model")
        stage = StagePolicy(name="s", mode="plan")
        result = _stage_inputs(base, stage)
        self.assertIs(result, base)

    def test_stage_overrides_toolsets_and_max_turns(self) -> None:
        base = Inputs(hermes_toolsets="file,terminal,web", hermes_max_turns="90")
        stage = StagePolicy(name="s", mode="review", toolsets="file", max_turns="10")
        result = _stage_inputs(base, stage)
        self.assertEqual(result.hermes_toolsets, "file")
        self.assertEqual(result.hermes_max_turns, "10")

    def test_stage_extra_args_extend_global_args_and_override_same_flags(self) -> None:
        base = Inputs(hermes_args="--profile coding -s claude-code --color never")
        stage = StagePolicy(name="s", mode="plan", extra_args="-s custom-skill --debug")
        result = _stage_inputs(base, stage)
        self.assertEqual(result.hermes_extra_args, ["--profile", "coding", "--color", "never", "-s", "custom-skill", "--debug"])

    def test_staged_run_preserves_global_hermes_profile_for_every_stage(self) -> None:
        policy = OrchestrationPolicy(stages=[
            StagePolicy(name="planner", mode="plan", max_turns="60", extra_args="-s claude-code"),
            StagePolicy(name="implementer", mode="implement", max_turns="90", extra_args="-s claude-code"),
            StagePolicy(name="reviewer", mode="review", max_turns="40"),
            StagePolicy(name="adjudicator", mode="adjudicate", max_turns="40", extra_args="-s claude-code"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=_success_result("done")) as mocked:
                    run_staged("base prompt", Inputs(dry_run=True, hermes_args="--profile coding -s claude-code"), policy)

        self.assertEqual(mocked.call_count, 4)
        for call in mocked.call_args_list:
            stage_inputs = call.args[1]
            self.assertEqual(stage_inputs.hermes_extra_args.count("--profile"), 1)
            self.assertIn("coding", stage_inputs.hermes_extra_args)

    def test_fallback_inputs_use_secondary_model_and_drop_claude_skill(self) -> None:
        base = Inputs(
            hermes_provider="openai-codex",
            hermes_model="primary",
            hermes_args="-s claude-code",
            hermes_fallback_provider="openrouter",
            hermes_fallback_model="deepseek-ai/DeepSeek-V4-Pro",
        )
        stage_inputs = dataclasses.replace(base, hermes_provider="anthropic", hermes_model="sonnet")
        fallback = _fallback_stage_inputs(base, stage_inputs)
        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(fallback.hermes_provider, "openrouter")
        self.assertEqual(fallback.hermes_model, "deepseek-ai/DeepSeek-V4-Pro")
        self.assertEqual(fallback.hermes_args, "")

    def test_claude_throttle_detection(self) -> None:
        self.assertTrue(_looks_like_claude_throttle(_failure_result(stderr="Claude Code API rate limit 429")))
        self.assertFalse(_looks_like_claude_throttle(_failure_result(stderr="unit tests failed")))


class RunStagedTests(unittest.TestCase):
    def _policy(self, *modes: str) -> OrchestrationPolicy:
        stages = [StagePolicy(name=m, mode=m) for m in modes]
        return OrchestrationPolicy(stages=stages)

    def test_all_stages_run_on_success(self) -> None:
        policy = self._policy("plan", "implement", "review", "adjudicate")
        results = [_success_result(f"stage {m}") for m in ["plan", "implement", "review", "adjudicate"]]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", side_effect=results) as mocked:
                    final = run_staged("base prompt", Inputs(dry_run=True), policy)
        self.assertEqual(mocked.call_count, 4)
        self.assertTrue(final.success)
        self.assertIn("### Stage summaries", final.stdout)
        self.assertIn("**plan**", final.stdout)
        self.assertIn("**adjudicate**", final.stdout)
        self.assertNotIn("## Stage:", final.stdout)

    def test_stops_on_first_failure(self) -> None:
        policy = self._policy("plan", "implement", "review", "adjudicate")
        side_effects = [
            _success_result("plan done"),
            _failure_result(stderr="impl error"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", side_effect=side_effects) as mocked:
                    final = run_staged("base prompt", Inputs(dry_run=True), policy)
        self.assertEqual(mocked.call_count, 2)
        self.assertFalse(final.success)
        self.assertEqual(final.conclusion, "failure")
        self.assertIn("implement", final.stdout)

    def test_stage_completion_callback_runs_after_each_completed_stage(self) -> None:
        policy = self._policy("plan", "implement")
        events: list[tuple[str, str, list[str]]] = []

        def on_stage_complete(stage: StagePolicy, result: HermesResult, completed: list[tuple[str, HermesResult]]) -> None:
            events.append((stage.name, result.conclusion, [name for name, _ in completed]))

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch(
                    "hermes_code_action.orchestrator.run_hermes",
                    side_effect=[_success_result("plan done"), _success_result("impl done")],
                ):
                    final = run_staged("base prompt", Inputs(dry_run=True), policy, on_stage_complete=on_stage_complete)

        self.assertTrue(final.success)
        self.assertEqual(events, [
            ("plan", "success", ["plan"]),
            ("implement", "success", ["plan", "implement"]),
        ])

    def test_stage_results_record_servicing_model(self) -> None:
        policy = OrchestrationPolicy(stages=[
            StagePolicy(name="planner", mode="plan", provider="anthropic", model="claude-opus-4.7"),
        ])
        events: list[tuple[str, str, bool]] = []

        def on_stage_complete(stage: StagePolicy, result: HermesResult, completed: list[tuple[str, HermesResult]]) -> None:
            events.append((result.provider, result.model, result.fallback_used))

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=_success_result("plan done")):
                    final = run_staged("base prompt", Inputs(dry_run=True), policy, on_stage_complete=on_stage_complete)

        self.assertTrue(final.success)
        self.assertEqual(events, [("anthropic", "claude-opus-4.7", False)])
        self.assertIn("anthropic / claude-opus-4.7", final.stdout)

    def test_default_planner_reports_claude_code_opus_service(self) -> None:
        policy = OrchestrationPolicy(stages=[StagePolicy(name="planner", mode="plan")])
        events: list[tuple[str, str]] = []

        def on_stage_complete(stage: StagePolicy, result: HermesResult, completed: list[tuple[str, HermesResult]]) -> None:
            events.append((result.provider, result.model))

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=_success_result("plan done")):
                    final = run_staged("base prompt", Inputs(dry_run=True), policy, on_stage_complete=on_stage_complete)

        self.assertTrue(final.success)
        self.assertEqual(events, [("Claude Code CLI", "opus")])
        self.assertIn("Claude Code CLI / opus", final.stdout)

    def test_retries_claude_throttle_with_secondary_hermes_model(self) -> None:
        policy = self._policy("plan", "implement")
        side_effects = [
            _success_result("plan done"),
            _failure_result(stderr="Claude Code failed: rate limit 429"),
            _success_result("fallback implement done"),
        ]
        inputs = Inputs(
            dry_run=True,
            hermes_args="-s claude-code",
            hermes_fallback_provider="openrouter",
            hermes_fallback_model="deepseek-ai/DeepSeek-V4-Pro",
        )
        fallback_events: list[tuple[str, str, bool, str, str]] = []

        def on_stage_complete(stage: StagePolicy, result: HermesResult, completed: list[tuple[str, HermesResult]]) -> None:
            if stage.name == "implement":
                fallback_events.append((
                    result.provider,
                    result.model,
                    result.fallback_used,
                    result.primary_provider,
                    result.primary_model,
                ))

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", side_effect=side_effects) as mocked:
                    final = run_staged("base prompt", inputs, policy, on_stage_complete=on_stage_complete)
        self.assertEqual(mocked.call_count, 3)
        fallback_inputs = mocked.call_args_list[2].args[1]
        self.assertEqual(fallback_inputs.hermes_provider, "openrouter")
        self.assertEqual(fallback_inputs.hermes_model, "deepseek-ai/DeepSeek-V4-Pro")
        self.assertEqual(fallback_inputs.hermes_args, "")
        self.assertTrue(final.success)
        self.assertIn("Retried with secondary Hermes model", final.stdout)
        self.assertIn("fallback implement done", final.stdout)
        self.assertIn("openrouter / deepseek-ai/DeepSeek-V4-Pro", final.stdout)
        self.assertEqual(fallback_events, [("openrouter", "deepseek-ai/DeepSeek-V4-Pro", True, "Claude Code CLI", "sonnet")])

    def test_review_stage_fails_if_it_changes_git_state(self) -> None:
        policy = OrchestrationPolicy(stages=[StagePolicy(name="reviewer", mode="review")])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=_success_result("review done")):
                    with mock.patch(
                        "hermes_code_action.orchestrator._git_state",
                        side_effect=[("sha1", ""), ("sha2", " M src/file.py\n")],
                    ):
                        final = run_staged("base prompt", Inputs(dry_run=True), policy)
        self.assertFalse(final.success)
        self.assertIn("Read-only stage `reviewer`", final.stdout)

    def test_review_stage_allows_unchanged_git_state(self) -> None:
        policy = OrchestrationPolicy(stages=[StagePolicy(name="reviewer", mode="review")])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=_success_result("review done")):
                    with mock.patch(
                        "hermes_code_action.orchestrator._git_state",
                        side_effect=[("sha1", ""), ("sha1", "")],
                    ):
                        final = run_staged("base prompt", Inputs(dry_run=True), policy)
        self.assertTrue(final.success)
        self.assertIn("review done", final.stdout)

    def test_prior_stage_output_passed_to_later_stages(self) -> None:
        policy = OrchestrationPolicy(stages=[
            StagePolicy(name="planner", mode="plan"),
            StagePolicy(name="implementer", mode="implement"),
        ])
        captured_prompts: list[str] = []

        def fake_run(prompt: str, inp: Inputs, **kw) -> HermesResult:
            captured_prompts.append(prompt)
            return _success_result(f"output for {inp.hermes_model or 'default'}")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", side_effect=fake_run):
                    run_staged("do work", Inputs(dry_run=True), policy)

        # Second stage prompt should contain prior stage output
        self.assertIn("planner", captured_prompts[1])

    def test_writes_execution_json(self) -> None:
        policy = self._policy("plan", "implement")
        side_effects = [_success_result("p"), _success_result("i")]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", side_effect=side_effects):
                    final = run_staged("base", Inputs(dry_run=True), policy)
            with open(final.execution_file, encoding="utf-8") as fh:
                data = json.loads(fh.read())
        self.assertEqual(data["orchestration_mode"], "staged")
        self.assertEqual(data["conclusion"], "success")
        self.assertEqual([s["stage"] for s in data["stages"]], ["plan", "implement"])

    def test_single_stage_preserves_session_id(self) -> None:
        policy = self._policy("plan")
        result = _success_result("done", session_id="abc123")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                with mock.patch("hermes_code_action.orchestrator.run_hermes", return_value=result):
                    final = run_staged("prompt", Inputs(dry_run=True), policy)
        self.assertEqual(final.session_id, "abc123")

    def test_stage_comment_summary_is_compact_and_single_line(self) -> None:
        verbose = "# Result\n\n" + "details with | pipes\n" * 200
        summary = _compact_stage_summary(_success_result(verbose))
        self.assertLessEqual(len(summary), 720)
        self.assertNotIn("\n", summary)
        self.assertIn("\\|", summary)


if __name__ == "__main__":
    unittest.main()
