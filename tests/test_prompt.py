from __future__ import annotations

import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.branch import BranchInfo
from hermes_code_action.config import Inputs
from hermes_code_action.github_context import parse_context
from hermes_code_action.plan import PlanInfo
from hermes_code_action.prompt import GitHubData, build_prompt


class PromptTests(unittest.TestCase):
    def test_build_prompt_includes_github_context_and_user_request(self) -> None:
        payload = {
            "event_name": "issue_comment",
            "action": "created",
            "sender": {"login": "alice"},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 5, "title": "Broken", "body": "The app crashes"},
            "comment": {"id": 99, "body": "@hermes fix it"},
        }
        ctx = parse_context(payload)
        prompt = build_prompt(
            ctx,
            Inputs(),
            "mention",
            "fix it",
            GitHubData(issue_comments=[{"user": {"login": "alice"}, "created_at": "now", "body": "@hermes fix it"}]),
            BranchInfo(base_branch="main", current_branch="hermes/issue-5", hermes_branch="hermes/issue-5", is_new_branch=True),
            123,
            "https://github.com/acme/repo/actions/runs/1",
            PlanInfo(True, "docs/hermes-plans/issue-5-broken.md", "https://github.com/acme/repo/blob/hermes/issue-5/docs/hermes-plans/issue-5-broken.md"),
            'python3 "$HERMES_TRACKING_COMMENT_TOOL"',
        )
        self.assertIn("Repository: acme/repo", prompt)
        self.assertIn("fix it", prompt)
        self.assertIn("hermes/issue-5", prompt)
        self.assertIn("Tracking comment id: 123", prompt)
        self.assertIn("Do NOT run `git push`", prompt)
        self.assertIn("NEVER push directly to `main`, `master`", prompt)
        self.assertIn("HERMES_TRACKING_COMMENT_TOOL", prompt)
        self.assertIn("docs/hermes-plans/issue-5-broken.md", prompt)


if __name__ == "__main__":
    unittest.main()
