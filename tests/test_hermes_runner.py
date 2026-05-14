from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
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

    def test_run_hermes_records_servicing_model(self) -> None:
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": self._tmpdir()}, clear=False):
            result = run_hermes(
                "prompt",
                Inputs(
                    dry_run=True,
                    path_to_hermes_executable="hermes",
                    hermes_provider="custom:Crusoe-Deepseek-V4",
                    hermes_model="deepseek-ai/DeepSeek-V4-Pro",
                ),
            )
        self.assertEqual(result.provider, "custom:Crusoe-Deepseek-V4")
        self.assertEqual(result.model, "deepseek-ai/DeepSeek-V4-Pro")
        payload = json.loads(Path(result.execution_file).read_text(encoding="utf-8"))
        self.assertEqual(payload["provider"], "custom:Crusoe-Deepseek-V4")
        self.assertEqual(payload["model"], "deepseek-ai/DeepSeek-V4-Pro")

    def test_run_hermes_passes_tracking_env_but_scrubs_tokens_and_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp, "fake-hermes")
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "print('TOKEN=' + str('GITHUB_TOKEN' in os.environ))\n"
                "print('INPUT_TOKEN=' + str('INPUT_GITHUB_TOKEN' in os.environ))\n"
                "print('OUTPUT=' + str('GITHUB_OUTPUT' in os.environ))\n"
                "print('TRACKING=' + os.environ.get('HERMES_TRACKING_COMMENT_ENDPOINT', ''))\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "RUNNER_TEMP": tmp,
                    "INPUT_GITHUB_TOKEN": "secret",
                    "GITHUB_TOKEN": "secret",
                    "GITHUB_OUTPUT": str(Path(tmp, "outputs")),
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token",
                },
                clear=False,
            ):
                result = run_hermes(
                    "prompt",
                    Inputs(dry_run=False, path_to_hermes_executable=str(executable), timeout_seconds=10),
                    extra_env={"HERMES_TRACKING_COMMENT_ENDPOINT": "http://127.0.0.1/update"},
                )
        self.assertTrue(result.success)
        self.assertIn("TOKEN=False", result.stdout)
        self.assertIn("INPUT_TOKEN=False", result.stdout)
        self.assertIn("OUTPUT=False", result.stdout)
        self.assertIn("TRACKING=http://127.0.0.1/update", result.stdout)

    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix="hermes-action-test-")


if __name__ == "__main__":
    unittest.main()
