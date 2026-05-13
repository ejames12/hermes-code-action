from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.branch import BranchInfo
from hermes_code_action.github_context import parse_context
from hermes_code_action.plan import assert_plan_only_changes, build_plan_info, is_plan_request, plan_file_path


class PlanTests(unittest.TestCase):
    def test_detects_plan_request(self) -> None:
        self.assertTrue(is_plan_request("plan"))
        self.assertTrue(is_plan_request("plan add OAuth login"))
        self.assertTrue(is_plan_request("plan: add OAuth login"))
        self.assertFalse(is_plan_request("please plan this"))
        self.assertFalse(is_plan_request("fix bug"))

    def test_plan_path_and_url_are_stable(self) -> None:
        ctx = parse_context(
            {
                "event_name": "issue_comment",
                "sender": {"login": "alice"},
                "repository": {
                    "full_name": "acme/repo",
                    "default_branch": "main",
                    "html_url": "https://github.com/acme/repo",
                },
                "issue": {"number": 12, "title": "Add OAuth login flow!", "body": "B"},
                "comment": {"id": 1, "body": "@hermes plan"},
            }
        )
        self.assertEqual(plan_file_path(ctx), "docs/hermes-plans/issue-12-add-oauth-login-flow.md")
        info = build_plan_info(
            ctx,
            BranchInfo(base_branch="main", current_branch="hermes/issue-12", hermes_branch="hermes/issue-12"),
        )
        self.assertEqual(info.file_path, "docs/hermes-plans/issue-12-add-oauth-login-flow.md")
        self.assertEqual(
            info.web_url,
            "https://github.com/acme/repo/blob/hermes/issue-12/docs/hermes-plans/issue-12-add-oauth-login-flow.md",
        )

    def test_plan_only_validation_allows_plan_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._init_git_repo(tmp)
            start = self._git(tmp, "rev-parse", "HEAD")
            plan_path = Path(tmp, "docs/hermes-plans/issue-1-plan.md")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text("# Plan\n", encoding="utf-8")
            Path(tmp, "docs/hermes-plans/diagram.mmd").write_text("graph TD\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}, clear=False):
                assert_plan_only_changes(
                    build_plan_info(
                        self._ctx(),
                        BranchInfo(base_branch="main", current_branch="hermes/issue-1", hermes_branch="hermes/issue-1"),
                    ),
                    start,
                )

    def test_plan_only_validation_rejects_application_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._init_git_repo(tmp)
            start = self._git(tmp, "rev-parse", "HEAD")
            Path(tmp, "src").mkdir()
            Path(tmp, "src/app.py").write_text("print('not a plan')\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "outside docs/hermes-plans"):
                    assert_plan_only_changes(
                        build_plan_info(
                            self._ctx(),
                            BranchInfo(base_branch="main", current_branch="hermes/issue-1", hermes_branch="hermes/issue-1"),
                        ),
                        start,
                    )

    def _ctx(self):
        return parse_context(
            {
                "event_name": "issue_comment",
                "sender": {"login": "alice"},
                "repository": {
                    "full_name": "acme/repo",
                    "default_branch": "main",
                    "html_url": "https://github.com/acme/repo",
                },
                "issue": {"number": 1, "title": "Plan", "body": ""},
                "comment": {"id": 1, "body": "@hermes plan"},
            }
        )

    def _init_git_repo(self, path: str) -> None:
        self._git(path, "init", "-b", "main")
        self._git(path, "config", "user.email", "test@example.com")
        self._git(path, "config", "user.name", "Test")
        Path(path, "README.md").write_text("# Test\n", encoding="utf-8")
        self._git(path, "add", "README.md")
        self._git(path, "commit", "-m", "init")

    def _git(self, cwd: str, *args: str) -> str:
        completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
