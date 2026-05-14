from __future__ import annotations

import time
import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.comments import (
    final_comment_body,
    initial_comment_body,
    stage_summary_comment_body,
    staged_tracking_comment_body,
)
from hermes_code_action.github_context import parse_context
from hermes_code_action.hermes_runner import HermesResult


class CommentTests(unittest.TestCase):
    def _ctx(self):
        payload = {
            "event_name": "issue_comment",
            "sender": {"login": "alice"},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 1, "title": "T", "body": "B"},
            "comment": {"id": 2, "body": "@hermes"},
        }
        return parse_context(payload)

    def _result(self, conclusion: str = "success", stdout: str = "done", stderr: str = "", **kwargs) -> HermesResult:
        return HermesResult(
            conclusion=conclusion,
            stdout=stdout,
            stderr=stderr,
            returncode=0 if conclusion == "success" else 1,
            execution_file="/tmp/hermes.json",
            duration_seconds=1.2,
            **kwargs,
        )

    def test_initial_and_final_comments(self) -> None:
        ctx = self._ctx()
        body = initial_comment_body(ctx, "https://run")
        self.assertIn("Hermes is working", body)
        final = final_comment_body(
            ctx,
            success=True,
            started_at=time.time() - 1,
            run_url="https://run",
            branch_name="hermes/issue-1",
            branch_url="https://branch",
            compare_url="https://compare",
            output="done",
            plan_url="https://plan",
            push_message="Pushed branch hermes/issue-1 for PR review.",
        )
        self.assertIn("Hermes finished", final)
        self.assertIn("### Summary", final)
        self.assertNotIn("Final result posted", final)
        self.assertIn("Create PR", final)
        self.assertIn("View plan", final)
        self.assertIn("Pushed branch", final)
        self.assertIn("done", final)

    def test_initial_staged_comment_lists_planned_stages(self) -> None:
        body = initial_comment_body(self._ctx(), "https://run", stage_names=["planner", "implementer"])
        self.assertIn("### Planned stages", body)
        self.assertIn("- [ ] planner", body)
        self.assertIn("- [ ] implementer", body)

    def test_staged_tracking_comment_marks_completed_stages(self) -> None:
        body = staged_tracking_comment_body(
            self._ctx(),
            run_url="https://run",
            stage_names=["planner", "implementer"],
            stage_results=[("planner", self._result(stdout="planned"))],
            started_at=time.time() - 2,
            final=False,
        )
        self.assertIn("Hermes is working", body)
        self.assertIn("- [x] planner — ✅ `success`", body)
        self.assertIn("- [ ] implementer", body)
        self.assertIn("Stage summary comments are posted separately", body)

    def test_stage_summary_comment_mentions_assignee_when_human_attention_needed(self) -> None:
        body = stage_summary_comment_body(
            self._ctx(),
            stage_name="reviewer",
            stage_mode="review",
            result=self._result(conclusion="failure", stderr="Blocked: manual decision needed"),
            run_url="https://run",
            assignees=["bob"],
            stage_number=3,
            total_stages=4,
        )
        self.assertIn("## ❌ Hermes stage: reviewer", body)
        self.assertIn("**Mode:** `review`", body)
        self.assertIn("### Summary", body)
        self.assertIn("Blocked: manual decision needed", body)
        self.assertIn("### Human attention", body)
        self.assertIn("@bob", body)

    def test_stage_summary_comment_does_not_mention_assignee_when_no_attention_needed(self) -> None:
        body = stage_summary_comment_body(
            self._ctx(),
            stage_name="planner",
            stage_mode="plan",
            result=self._result(stdout="Plan created. No blockers."),
            run_url="https://run",
            assignees=["bob"],
            stage_number=1,
            total_stages=4,
        )
        self.assertNotIn("@bob", body)
        self.assertNotIn("### Human attention", body)

    def test_stage_summary_comment_includes_servicing_model(self) -> None:
        body = stage_summary_comment_body(
            self._ctx(),
            stage_name="planner",
            stage_mode="plan",
            result=self._result(stdout="Plan created.", provider="anthropic", model="claude-opus-4.7"),
            run_url="https://run",
            stage_number=1,
            total_stages=4,
        )
        self.assertIn("**Serviced by:** `anthropic` / `claude-opus-4.7`", body)

    def test_stage_summary_comment_marks_fallback_model(self) -> None:
        body = stage_summary_comment_body(
            self._ctx(),
            stage_name="implementer",
            stage_mode="implement",
            result=self._result(
                stdout="Implemented with fallback.",
                provider="custom:Crusoe-Deepseek-V4",
                model="deepseek-ai/DeepSeek-V4-Pro",
                fallback_used=True,
                primary_provider="anthropic",
                primary_model="claude-sonnet-4.5",
            ),
            run_url="https://run",
            stage_number=2,
            total_stages=4,
        )
        self.assertIn("**Serviced by:** `custom:Crusoe-Deepseek-V4` / `deepseek-ai/DeepSeek-V4-Pro`", body)
        self.assertIn("fallback after Claude throttling", body)
        self.assertIn("primary attempt: `anthropic` / `claude-sonnet-4.5`", body)


if __name__ == "__main__":
    unittest.main()
