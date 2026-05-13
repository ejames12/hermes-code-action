from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from .comments import TrackingComment
from .github_api import GitHubApi
from .util import mask, truncate, warning


MAX_COMMENT_CHARS = 60_000


@dataclass
class TrackingTool:
    endpoint: str
    token: str
    script_path: str

    @property
    def env(self) -> dict[str, str]:
        return {
            "HERMES_TRACKING_COMMENT_ENDPOINT": self.endpoint,
            "HERMES_TRACKING_COMMENT_TOKEN": self.token,
            "HERMES_TRACKING_COMMENT_TOOL": self.script_path,
        }

    @property
    def command_hint(self) -> str:
        return 'python3 "$HERMES_TRACKING_COMMENT_TOOL"'


def _write_helper_script(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the current Hermes tracking comment.")
    parser.add_argument("message", nargs="*", help="Markdown body. If omitted, stdin is used.")
    args = parser.parse_args()
    body = " ".join(args.message).strip()
    if not body:
        body = sys.stdin.read().strip()
    if not body:
        print("No comment body provided", file=sys.stderr)
        return 2

    endpoint = os.environ.get("HERMES_TRACKING_COMMENT_ENDPOINT", "")
    token = os.environ.get("HERMES_TRACKING_COMMENT_TOKEN", "")
    if not endpoint or not token:
        print("Tracking comment update endpoint is not configured", file=sys.stderr)
        return 2

    payload = json.dumps({"body": body}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.read().decode("utf-8") or "Tracking comment updated")
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not update tracking comment: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


class _CommentUpdateHandler(BaseHTTPRequestHandler):
    server: "TrackingCommentServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/update":
            self._send(404, {"ok": False, "error": "not found"})
            return
        expected = f"Bearer {self.server.auth_token}"
        if self.headers.get("Authorization") != expected:
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(min(length, MAX_COMMENT_CHARS * 2)).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"body": raw}
        body = truncate(str(payload.get("body") or "").strip(), MAX_COMMENT_CHARS)
        if not body:
            self._send(400, {"ok": False, "error": "empty body"})
            return
        now = time.time()
        if now - self.server.last_update_at < self.server.min_interval_seconds:
            self._send(
                429,
                {
                    "ok": False,
                    "error": f"rate limited; wait {self.server.min_interval_seconds:.0f}s between updates",
                },
            )
            return
        try:
            self.server.github.update_issue_comment(self.server.tracking.id or 0, body)
        except Exception as exc:  # noqa: BLE001
            warning(f"Tracking comment update failed: {exc}")
            self._send(502, {"ok": False, "error": str(exc)})
            return
        self.server.last_update_at = now
        self._send(200, {"ok": True, "message": "Tracking comment updated"})


class TrackingCommentServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        github: GitHubApi,
        tracking: TrackingComment,
        auth_token: str,
        *,
        min_interval_seconds: float = 10.0,
    ) -> None:
        super().__init__(server_address, _CommentUpdateHandler)
        self.github = github
        self.tracking = tracking
        self.auth_token = auth_token
        self.min_interval_seconds = min_interval_seconds
        self.last_update_at = 0.0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, name="hermes-tracking-comment", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


def start_tracking_tool(api: GitHubApi | None, tracking: TrackingComment, *, runner_temp: str | None = None) -> tuple[TrackingCommentServer | None, TrackingTool | None]:
    if api is None or not tracking.id:
        return None, None
    token = secrets.token_urlsafe(24)
    mask(token)
    server = TrackingCommentServer(("127.0.0.1", 0), api, tracking, token)
    server.start()
    host, port = server.server_address
    temp_dir = Path(runner_temp or os.environ.get("RUNNER_TEMP") or "/tmp") / "hermes-code-action"
    script_path = temp_dir / "update_tracking_comment.py"
    _write_helper_script(script_path)
    tool = TrackingTool(endpoint=f"http://{host}:{port}/update", token=token, script_path=str(script_path))
    return server, tool
