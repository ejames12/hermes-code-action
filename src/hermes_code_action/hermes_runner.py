from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from .config import Inputs
from .session_renamer import rename_session
from .util import notice, truncate, workspace


@dataclass
class HermesResult:
    conclusion: str
    stdout: str
    stderr: str
    returncode: int
    execution_file: str
    duration_seconds: float
    session_id: str | None = None
    provider: str = ""
    model: str = ""
    fallback_used: bool = False
    primary_provider: str = ""
    primary_model: str = ""
    hermes_args: str = ""

    @property
    def success(self) -> bool:
        return self.conclusion == "success"


def _arg_value(args: list[str], flag: str) -> str:
    for index, arg in enumerate(args):
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
        prefix = f"{flag}="
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return ""


def effective_model_info(inputs: Inputs) -> tuple[str, str]:
    """Return the provider/model requested for a Hermes invocation."""
    extra_args = inputs.hermes_extra_args
    provider = inputs.hermes_provider or _arg_value(extra_args, "--provider")
    model = inputs.hermes_model or _arg_value(extra_args, "--model")
    return provider, model


def find_hermes_executable(inputs: Inputs) -> str:
    if inputs.path_to_hermes_executable:
        return inputs.path_to_hermes_executable
    found = shutil.which("hermes")
    if found:
        return found
    home_candidate = Path.home() / ".local" / "bin" / "hermes"
    if home_candidate.exists():
        return str(home_candidate)
    raise RuntimeError("Could not find Hermes executable. Install Hermes or set path_to_hermes_executable.")


def build_hermes_command(executable: str, prompt: str, inputs: Inputs, session_title: str = "") -> list[str]:
    # session_title is applied after the run using the explicit session_id that
    # Hermes prints to stderr. Do not inject /title into the non-interactive
    # prompt: `hermes chat -q ... -Q` sends the prompt straight to the model and
    # does not process slash commands first.
    _ = session_title
    args = [executable, "chat", "-q", prompt, "-Q", "--source", inputs.hermes_source]
    if inputs.hermes_yolo:
        args.append("--yolo")
    if inputs.hermes_toolsets:
        args.extend(["-t", inputs.hermes_toolsets])
    if inputs.hermes_provider:
        args.extend(["--provider", inputs.hermes_provider])
    if inputs.hermes_model:
        args.extend(["--model", inputs.hermes_model])
    if inputs.hermes_max_turns:
        args.extend(["--max-turns", inputs.hermes_max_turns])
    args.extend(inputs.hermes_extra_args)
    return args


def _scrub_env_for_log(args: list[str]) -> list[str]:
    scrubbed = list(args)
    if "-q" in scrubbed:
        i = scrubbed.index("-q")
        if i + 1 < len(scrubbed):
            scrubbed[i + 1] = f"<prompt:{len(scrubbed[i + 1])} chars>"
    return scrubbed


def _parse_session_id(output: str) -> str | None:
    for line in output.splitlines():
        lower = line.lower()
        if "session" in lower and ":" in line:
            maybe = line.split(":", 1)[1].strip()
            if 8 <= len(maybe) <= 128 and " " not in maybe:
                return maybe
    return None


def run_hermes(
    prompt: str,
    inputs: Inputs,
    extra_env: dict[str, str] | None = None,
    session_title: str = "",
) -> HermesResult:
    if inputs.dry_run:
        executable = inputs.path_to_hermes_executable or shutil.which("hermes") or "hermes"
    else:
        executable = find_hermes_executable(inputs)
    command = build_hermes_command(executable, prompt, inputs, session_title=session_title)
    notice("Running Hermes: " + " ".join(_scrub_env_for_log(command)))

    env = os.environ.copy()
    env["HERMES_ACCEPT_HOOKS"] = "1"
    if extra_env:
        env.update(extra_env)
    if inputs.hermes_yolo:
        env["HERMES_YOLO_MODE"] = "1"
    # OIDC request env vars let a subprocess mint cloud/GitHub tokens. Do not pass them to Hermes.
    env.pop("ACTIONS_ID_TOKEN_REQUEST_URL", None)
    env.pop("ACTIONS_ID_TOKEN_REQUEST_TOKEN", None)
    # Git credentials are configured by the action wrapper; avoid handing raw tokens
    # to arbitrary terminal commands the model may run.
    env.pop("INPUT_GITHUB_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    # Do not let Hermes read or write the action output file; it may contain privileged wrapper outputs.
    env.pop("GITHUB_OUTPUT", None)

    started = time.time()
    if inputs.dry_run:
        stdout = "Dry run: Hermes execution skipped."
        stderr = ""
        returncode = 0
    else:
        completed = subprocess.run(
            command,
            cwd=workspace(),
            env=env,
            text=True,
            capture_output=True,
            timeout=inputs.timeout_seconds,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = completed.returncode

    duration = time.time() - started
    conclusion = "success" if returncode == 0 else "failure"
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or "/tmp")
    runner_temp.mkdir(parents=True, exist_ok=True)
    execution_file = runner_temp / "hermes-execution-output.json"
    payload = {
        "command": _scrub_env_for_log(command),
        "conclusion": conclusion,
        "returncode": returncode,
        "duration_seconds": duration,
        "provider": effective_model_info(inputs)[0],
        "model": effective_model_info(inputs)[1],
        "stdout": stdout if inputs.show_full_output else truncate(stdout, 80_000),
        "stderr": stderr if inputs.show_full_output else truncate(stderr, 40_000),
    }
    execution_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if stdout:
        notice("Hermes stdout:\n" + (stdout if inputs.show_full_output else truncate(stdout, 8000)))
    if stderr:
        notice("Hermes stderr:\n" + (stderr if inputs.show_full_output else truncate(stderr, 8000)))

    session_id = _parse_session_id(stdout + "\n" + stderr)
    rename_session(executable, inputs, session_id, session_title)

    provider, model = effective_model_info(inputs)
    return HermesResult(
        conclusion=conclusion,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        execution_file=str(execution_file),
        duration_seconds=duration,
        session_id=session_id,
        provider=provider,
        model=model,
        hermes_args=inputs.hermes_args,
    )
