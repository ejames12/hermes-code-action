from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.branch import (
    BranchInfo,
    assert_push_allowed,
    generate_branch_name,
    install_no_protected_branch_push_hook,
    is_protected_branch,
    push_working_branch,
    validate_branch_name,
)
from hermes_code_action.github_context import parse_context


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

    def test_protected_branch_policy_rejects_main_master_and_base(self) -> None:
        ctx = parse_context(
            {
                "sender": {"login": "alice"},
                "repository": {"full_name": "acme/repo", "default_branch": "main"},
            }
        )
        info = BranchInfo(base_branch="develop", current_branch="hermes/issue-1")
        self.assertTrue(is_protected_branch("main", ctx, info))
        self.assertTrue(is_protected_branch("master", ctx, info))
        self.assertTrue(is_protected_branch("develop", ctx, info))
        self.assertFalse(is_protected_branch("hermes/issue-1", ctx, info))
        with self.assertRaises(RuntimeError):
            assert_push_allowed("main", ctx, info)
        assert_push_allowed("hermes/issue-1", ctx, info)

    def test_push_working_branch_refuses_protected_current_branch(self) -> None:
        ctx = self._ctx()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".git").mkdir()
            with mock.patch.dict("os.environ", {"GITHUB_WORKSPACE": tmp}, clear=False), mock.patch(
                "hermes_code_action.branch.current_branch", return_value="main"
            ):
                with self.assertRaisesRegex(RuntimeError, "protected branch"):
                    push_working_branch("token", ctx, mock.Mock(), BranchInfo(base_branch="main", current_branch="main"))

    def test_push_working_branch_no_token_does_not_push(self) -> None:
        ctx = self._ctx()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".git").mkdir()
            with mock.patch.dict("os.environ", {"GITHUB_WORKSPACE": tmp}, clear=False), mock.patch(
                "hermes_code_action.branch.current_branch", return_value="hermes/issue-1"
            ):
                info = push_working_branch(
                    "",
                    ctx,
                    mock.Mock(),
                    BranchInfo(base_branch="main", current_branch="hermes/issue-1", hermes_branch="hermes/issue-1"),
                )
        self.assertFalse(info.pushed)
        self.assertEqual(info.branch, "hermes/issue-1")
        self.assertIn("not pushed", info.message)

    def test_pre_push_hook_preserves_existing_hook_and_blocks_main(self) -> None:
        ctx = self._ctx()
        with tempfile.TemporaryDirectory() as tmp:
            hooks = Path(tmp, ".git", "hooks")
            hooks.mkdir(parents=True)
            existing_hook = hooks / "pre-push"
            existing_hook.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            existing_hook.chmod(0o700)
            with mock.patch.dict("os.environ", {"GITHUB_WORKSPACE": tmp}, clear=False):
                install_no_protected_branch_push_hook(ctx, BranchInfo(base_branch="main", current_branch="hermes/issue-1"))
            self.assertTrue((hooks / "pre-push.hermes-code-action-backup").exists())
            completed = subprocess.run(
                ["bash", str(existing_hook)],
                input="refs/heads/hermes/issue-1 abc refs/heads/main def\n",
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refuses to push", completed.stderr)

    def _ctx(self):
        return parse_context(
            {
                "sender": {"login": "alice"},
                "repository": {"full_name": "acme/repo", "default_branch": "main"},
            }
        )


if __name__ == "__main__":
    unittest.main()
