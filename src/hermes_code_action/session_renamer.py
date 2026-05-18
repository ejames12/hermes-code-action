from __future__ import annotations

import os
import subprocess

from .config import Inputs
from .util import notice, warning


def _profile_args(inputs: Inputs) -> list[str]:
    """Return the Hermes global profile args that select the same session DB."""
    extra_args = list(getattr(inputs, "hermes_extra_args", []) or [])
    for i, token in enumerate(extra_args):
        if token in {"--profile", "-p"} and i + 1 < len(extra_args):
            return [token, extra_args[i + 1]]
        for flag in ("--profile=", "-p="):
            if token.startswith(flag):
                value = token[len(flag):]
                if value:
                    return ["--profile", value]
    return []


def _rename_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ACTIONS_ID_TOKEN_REQUEST_URL", None)
    env.pop("ACTIONS_ID_TOKEN_REQUEST_TOKEN", None)
    env.pop("INPUT_GITHUB_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_OUTPUT", None)
    return env


def rename_session(
    executable: str,
    inputs: Inputs,
    session_id: str | None,
    session_title: str = "",
) -> bool:
    """Best-effort title update for the exact Hermes session created by a run."""
    title = " ".join(session_title.split())
    if not session_id or not title:
        return False

    command = [executable, *_profile_args(inputs), "sessions", "rename", session_id, title]
    try:
        renamed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env=_rename_env(),
        )
    except Exception as exc:
        warning(f"Could not set Hermes session title for {session_id}: {exc}")
        return False

    if renamed.returncode == 0:
        notice(f"Hermes session {session_id} titled: {title}")
        return True

    warning(
        "Could not set Hermes session title for "
        f"{session_id}: {renamed.stderr.strip() or renamed.stdout.strip()}"
    )
    return False
