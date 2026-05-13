from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

from tests import _paths  # noqa: F401
from hermes_code_action.comments import TrackingComment
from hermes_code_action.tracking import TrackingCommentServer, start_tracking_tool


class FakeGitHubApi:
    def __init__(self) -> None:
        self.updated: list[tuple[int, str]] = []
        self.raise_on_update: Exception | None = None

    def update_issue_comment(self, comment_id: int, body: str) -> dict:
        if self.raise_on_update is not None:
            raise self.raise_on_update
        self.updated.append((comment_id, body))
        return {"id": comment_id, "body": body}


class TrackingTests(unittest.TestCase):
    def test_authenticated_update_changes_existing_tracking_comment(self) -> None:
        api = FakeGitHubApi()
        server = TrackingCommentServer(("127.0.0.1", 0), api, TrackingComment(123, None), "secret", min_interval_seconds=0)
        server.start()
        try:
            status, payload = self._post(server, "secret", {"body": "## Working\n\n- [x] inspected"})
        finally:
            server.stop()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(api.updated, [(123, "## Working\n\n- [x] inspected")])

    def test_rejects_bad_token_and_empty_body(self) -> None:
        api = FakeGitHubApi()
        server = TrackingCommentServer(("127.0.0.1", 0), api, TrackingComment(123, None), "secret", min_interval_seconds=0)
        server.start()
        try:
            status, payload = self._post(server, "wrong", {"body": "hi"})
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"], "unauthorized")
            status, payload = self._post(server, "secret", {"body": ""})
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"], "empty body")
        finally:
            server.stop()
        self.assertEqual(api.updated, [])

    def test_rate_limits_updates(self) -> None:
        api = FakeGitHubApi()
        server = TrackingCommentServer(("127.0.0.1", 0), api, TrackingComment(123, None), "secret", min_interval_seconds=60)
        server.start()
        try:
            first_status, _ = self._post(server, "secret", {"body": "first"})
            second_status, second_payload = self._post(server, "secret", {"body": "second"})
        finally:
            server.stop()
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 429)
        self.assertIn("rate limited", second_payload["error"])
        self.assertEqual(api.updated, [(123, "first")])

    def test_api_failure_returns_bad_gateway(self) -> None:
        api = FakeGitHubApi()
        api.raise_on_update = RuntimeError("boom")
        server = TrackingCommentServer(("127.0.0.1", 0), api, TrackingComment(123, None), "secret", min_interval_seconds=0)
        server.start()
        try:
            status, payload = self._post(server, "secret", {"body": "hi"})
        finally:
            server.stop()
        self.assertEqual(status, 502)
        self.assertIn("boom", payload["error"])

    def test_start_tracking_tool_writes_helper_script(self) -> None:
        api = FakeGitHubApi()
        with tempfile.TemporaryDirectory() as tmp:
            server, tool = start_tracking_tool(api, TrackingComment(123, None), runner_temp=tmp)
            self.assertIsNotNone(server)
            self.assertIsNotNone(tool)
            assert server is not None and tool is not None
            try:
                completed = subprocess.run(
                    [sys.executable, tool.script_path],
                    input="helper update",
                    text=True,
                    capture_output=True,
                    env={**tool.env},
                    check=False,
                )
            finally:
                server.stop()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Tracking comment updated", completed.stdout)
        self.assertEqual(api.updated, [(123, "helper update")])

    def test_start_tracking_tool_noops_without_comment(self) -> None:
        api = FakeGitHubApi()
        server, tool = start_tracking_tool(api, TrackingComment(None, None))
        self.assertIsNone(server)
        self.assertIsNone(tool)

    def _post(self, server: TrackingCommentServer, token: str, payload: dict) -> tuple[int, dict]:
        host, port = server.server_address
        req = urllib.request.Request(
            f"http://{host}:{port}/update",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
