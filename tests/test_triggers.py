from __future__ import annotations

import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.github_context import parse_context
from hermes_code_action.triggers import contains_trigger, detect_trigger, normalize_for_trigger


class TriggerTests(unittest.TestCase):
    def test_contains_trigger_boundaries(self) -> None:
        self.assertTrue(contains_trigger("@hermes fix this", "@hermes"))
        self.assertTrue(contains_trigger("> @Hermes, please review", "@hermes"))
        self.assertFalse(contains_trigger("@hermesbot fix this", "@hermes"))
        self.assertFalse(contains_trigger("email me at test@hermes.dev", "@hermes"))

    def test_normalize_strips_hidden_comments_and_invisible_chars(self) -> None:
        normalized = normalize_for_trigger("<!-- hidden @hermes -->\u200b@hermes fix")
        self.assertNotIn("hidden", normalized)
        self.assertIn("@hermes fix", normalized)

    def test_issue_comment_tag_mode(self) -> None:
        payload = {
            "event_name": "issue_comment",
            "action": "created",
            "sender": {"login": "maintainer"},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 7, "title": "Bug", "body": "body"},
            "comment": {"id": 10, "body": "@hermes fix the bug"},
        }
        ctx = parse_context(payload)
        decision = detect_trigger(ctx, Inputs())
        self.assertTrue(decision.should_run)
        self.assertEqual(decision.mode, "tag")
        self.assertIn("fix the bug", decision.user_request)

    def test_prompt_forces_agent_mode(self) -> None:
        payload = {
            "sender": {"login": "maintainer"},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
        }
        ctx = parse_context(payload)
        ctx.event_name = "workflow_dispatch"
        decision = detect_trigger(ctx, Inputs(prompt="do maintenance"))
        self.assertTrue(decision.should_run)
        self.assertEqual(decision.mode, "agent")
        self.assertEqual(decision.user_request, "do maintenance")


if __name__ == "__main__":
    unittest.main()
