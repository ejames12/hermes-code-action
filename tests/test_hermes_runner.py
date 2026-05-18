from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests import _paths  # noqa: F401
from hermes_code_action.config import Inputs
from hermes_code_action.github_context import parse_context
from hermes_code_action.hermes_runner import build_hermes_command, run_hermes
from hermes_code_action.session_titles import session_title_for_context


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

    def test_build_hermes_command_leaves_prompt_unchanged_when_session_title_is_set(self) -> None:
        cmd = build_hermes_command(
            "/usr/bin/hermes",
            "hello",
            Inputs(dry_run=True),
            session_title="issue #42: Fix login redirect",
        )
        self.assertEqual(cmd[2:4], ["-q", "hello"])

    def test_session_title_for_context_uses_issue_or_pr_title(self) -> None:
        issue_ctx = parse_context({
            "event_name": "issue_comment",
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 42, "title": "Fix login redirect", "body": "body"},
            "comment": {"id": 99, "body": "@hermes fix it"},
        })
        self.assertEqual(session_title_for_context(issue_ctx), "issue #42: Fix login redirect")

        pr_ctx = parse_context({
            "event_name": "pull_request",
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "pull_request": {"number": 7, "title": "Add OAuth flow", "body": "body"},
        })
        self.assertEqual(session_title_for_context(pr_ctx), "pr #7: Add OAuth flow")

    def test_session_title_for_context_sanitizes_long_or_multiline_titles(self) -> None:
        ctx = parse_context({
            "event_name": "issues",
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
            "issue": {"number": 5, "title": "Fix\nredirect\t" + "x" * 120, "body": "body"},
        })
        title = session_title_for_context(ctx, max_length=40)
        self.assertEqual(title, "issue #5: Fix redirect xxxxxxxxxxxxxxxxx")
        self.assertEqual(len(title), 40)

    def test_session_title_for_context_returns_empty_without_github_entity_title(self) -> None:
        ctx = parse_context({
            "event_name": "workflow_dispatch",
            "repository": {"full_name": "acme/repo", "default_branch": "main"},
        })
        self.assertEqual(session_title_for_context(ctx), "")

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

    def test_run_hermes_records_hermes_args_for_profile_reporting(self) -> None:
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": self._tmpdir()}, clear=False):
            result = run_hermes(
                "prompt",
                Inputs(dry_run=True, path_to_hermes_executable="hermes", hermes_args="--profile coding -s claude-code"),
            )
        self.assertEqual(result.hermes_args, "--profile coding -s claude-code")

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
                "import sys\n"
                "from pathlib import Path\n"
                "if 'sessions' in sys.argv:\n"
                "    Path(os.environ['RUNNER_TEMP'], 'rename-args.txt').write_text('\\n'.join(sys.argv), encoding='utf-8')\n"
                "    raise SystemExit(0)\n"
                "print('PROMPT=' + sys.argv[sys.argv.index('-q') + 1])\n"
                "print('TOKEN=' + str('GITHUB_TOKEN' in os.environ))\n"
                "print('INPUT_TOKEN=' + str('INPUT_GITHUB_TOKEN' in os.environ))\n"
                "print('OUTPUT=' + str('GITHUB_OUTPUT' in os.environ))\n"
                "print('TRACKING=' + os.environ.get('HERMES_TRACKING_COMMENT_ENDPOINT', ''))\n"
                "print('session_id: test-session-123', file=sys.stderr)\n",
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
                    session_title="issue #42: Fix login redirect",
                )
            self.assertTrue(result.success)
            self.assertEqual(result.session_id, "test-session-123")
            self.assertIn("PROMPT=prompt", result.stdout)
            self.assertIn("TOKEN=False", result.stdout)
            self.assertIn("INPUT_TOKEN=False", result.stdout)
            self.assertIn("OUTPUT=False", result.stdout)
            self.assertIn("TRACKING=http://127.0.0.1/update", result.stdout)
            rename_args = Path(tmp, "rename-args.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(rename_args[-4:], ["sessions", "rename", "test-session-123", "issue #42: Fix login redirect"])

    def test_run_hermes_session_rename_preserves_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp, "fake-hermes")
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                "if 'sessions' in sys.argv:\n"
                "    Path(os.environ['RUNNER_TEMP'], 'rename-args.txt').write_text('\\n'.join(sys.argv), encoding='utf-8')\n"
                "    raise SystemExit(0)\n"
                "print('session_id: test-session-456', file=sys.stderr)\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": tmp}, clear=False):
                result = run_hermes(
                    "prompt",
                    Inputs(
                        dry_run=False,
                        path_to_hermes_executable=str(executable),
                        timeout_seconds=10,
                        hermes_args="--profile coding -s claude-code",
                    ),
                    session_title="issue #42: Fix login redirect",
                )
            self.assertTrue(result.success)
            rename_args = Path(tmp, "rename-args.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                rename_args,
                [str(executable), "--profile", "coding", "sessions", "rename", "test-session-456", "issue #42: Fix login redirect"],
            )

    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix="hermes-action-test-")


if __name__ == "__main__":
    unittest.main()
