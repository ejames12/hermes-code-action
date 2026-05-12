from __future__ import annotations

import time
import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.comments import final_comment_body, initial_comment_body
from hermes_code_action.github_context import parse_context


class CommentTests(unittest.TestCase):
    def test_initial_and_final_comments(self) -> None:
        payload = {
            "event_name": "issue_comment",
            "sender": {"login": "alice"},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 1, "title": "T", "body": "B"},
            "comment": {"id": 2, "body": "@hermes"},
        }
        ctx = parse_context(payload)
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
        )
        self.assertIn("Hermes finished", final)
        self.assertIn("Create PR", final)
        self.assertIn("done", final)


if __name__ == "__main__":
    unittest.main()
