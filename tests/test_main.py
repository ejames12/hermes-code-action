from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.main import main


class MainTests(unittest.TestCase):
    def test_agent_mode_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "sender": {"login": "alice"},
                        "repository": {
                            "full_name": "acme/repo",
                            "default_branch": "main",
                            "html_url": "https://github.com/acme/repo",
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = root / "outputs.txt"
            env = {
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_ACTOR": "alice",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_WORKSPACE": str(root),
                "RUNNER_TEMP": str(root / "tmp"),
                "INPUT_PROMPT": "Say hello",
                "INPUT_DRY_RUN": "true",
                "INPUT_PATH_TO_HERMES_EXECUTABLE": "hermes",
                "INPUT_GITHUB_TOKEN": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                code = main()
            self.assertEqual(code, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("conclusion=success", text)
            self.assertIn("structured_output=", text)
            self.assertIn("execution_file=", text)


if __name__ == "__main__":
    unittest.main()
