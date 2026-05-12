from __future__ import annotations

import unittest

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.github_context import parse_context
from hermes_code_action.security import validate_actor


class FakeApi:
    def __init__(self, permission: str) -> None:
        self.permission = permission

    def get_permission(self, username: str) -> str:
        return self.permission


class SecurityTests(unittest.TestCase):
    def _ctx(self, actor: str = "alice"):
        payload = {
            "event_name": "issue_comment",
            "action": "created",
            "sender": {"login": actor},
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 1, "title": "T", "body": "B"},
            "comment": {"id": 2, "body": "@hermes"},
        }
        ctx = parse_context(payload)
        return ctx

    def test_requires_write_permission(self) -> None:
        validate_actor(self._ctx(), Inputs(), FakeApi("write"))
        with self.assertRaises(RuntimeError):
            validate_actor(self._ctx(), Inputs(), FakeApi("read"))

    def test_denies_bots_by_default(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_actor(self._ctx("dependabot[bot]"), Inputs(), FakeApi("write"))
        validate_actor(self._ctx("dependabot[bot]"), Inputs(allowed_bots="dependabot[bot]"), FakeApi("write"))


if __name__ == "__main__":
    unittest.main()
