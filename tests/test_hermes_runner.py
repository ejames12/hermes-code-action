from __future__ import annotations

import os
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.hermes_runner import build_hermes_command, run_hermes


class HermesRunnerTests(unittest.TestCase):
    def test_build_hermes_command(self) -> None:
        cmd = build_hermes_command(
            "/usr/bin/hermes",
            "hello",
            Inputs(hermes_provider="openrouter", hermes_model="anthropic/claude", hermes_args="--ignore-rules"),
        )
        self.assertEqual(cmd[:5], ["/usr/bin/hermes", "chat", "-q", "hello", "-Q"])
        self.assertIn("--source", cmd)
        self.assertIn("--yolo", cmd)
        self.assertIn("openrouter", cmd)
        self.assertIn("anthropic/claude", cmd)
        self.assertIn("--ignore-rules", cmd)

    def test_run_hermes_dry_run_writes_execution_file_and_scrubs_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RUNNER_TEMP": self._tmpdir(),
                "INPUT_GITHUB_TOKEN": "secret",
                "GITHUB_TOKEN": "secret",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token",
            },
            clear=False,
        ):
            result = run_hermes("prompt", Inputs(dry_run=True, path_to_hermes_executable="hermes"))
        self.assertTrue(result.success)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(result.execution_file))

    def _tmpdir(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="hermes-action-test-")


if __name__ == "__main__":
    unittest.main()
