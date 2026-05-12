from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.branch import generate_branch_name, validate_branch_name


class BranchTests(unittest.TestCase):
    def test_validate_branch_name_accepts_safe_names(self) -> None:
        validate_branch_name("hermes/issue-123-20260101-1200")
        validate_branch_name("feat/foo_bar.1+two#3")

    def test_validate_branch_name_rejects_unsafe_names(self) -> None:
        for branch in ["", "-bad", "bad name", "bad..name", "bad.lock", "bad@{x", "bad//name"]:
            with self.subTest(branch=branch):
                with self.assertRaises(ValueError):
                    validate_branch_name(branch)

    def test_generate_branch_default(self) -> None:
        branch = generate_branch_name(
            "",
            "hermes/",
            "issue",
            42,
            "abcdef123456",
            description="Fix the docs please",
            now=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(branch, "hermes/issue-42-20260102-0304")

    def test_generate_branch_template(self) -> None:
        branch = generate_branch_name(
            "{{prefix}}{{entityType}}-{{entityNumber}}-{{description}}-{{sha}}",
            "hermes/",
            "pr",
            9,
            "abcdef123456",
            description="Add OAuth login flow",
            now=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(branch, "hermes/pr-9-add-oauth-login-flow-abcdef12")


if __name__ == "__main__":
    unittest.main()
