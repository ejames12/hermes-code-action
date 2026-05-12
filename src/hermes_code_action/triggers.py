from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .config import Inputs
from .github_context import GitHubContext
from .util import strip_control_chars


INVISIBLE_CHARS = "\u200b\u200c\u200d\ufeff"


def normalize_for_trigger(text: str) -> str:
    text = strip_control_chars(text or "")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = text.translate({ord(ch): None for ch in INVISIBLE_CHARS})
    return text


def contains_trigger(text: str, trigger_phrase: str) -> bool:
    if not text or not trigger_phrase:
        return False
    normalized = normalize_for_trigger(text)
    escaped = re.escape(trigger_phrase)
    # Require a reasonable left/right boundary so @hermesx does not trigger @hermes.
    pattern = re.compile(rf"(^|[\s>]){escaped}(?=$|[\s\.,!?:;\)\]\}}])", re.IGNORECASE)
    return bool(pattern.search(normalized))


def remove_trigger(text: str, trigger_phrase: str) -> str:
    if not text:
        return ""
    escaped = re.escape(trigger_phrase)
    cleaned = re.sub(rf"(^|[\s>]){escaped}(?=$|[\s\.,!?:;\)\]\}}])", " ", text, flags=re.IGNORECASE)
    return cleaned.strip()


@dataclass(frozen=True)
class TriggerDecision:
    should_run: bool
    mode: str
    reason: str
    user_request: str


def _label_names(payload: dict[str, Any]) -> list[str]:
    label = payload.get("label") or {}
    names = []
    if label.get("name"):
        names.append(label["name"])
    issue = payload.get("issue") or payload.get("pull_request") or {}
    for item in issue.get("labels") or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


def _assignee_logins(payload: dict[str, Any]) -> list[str]:
    assignee = payload.get("assignee") or {}
    logins = []
    if assignee.get("login"):
        logins.append(assignee["login"])
    issue = payload.get("issue") or payload.get("pull_request") or {}
    for item in issue.get("assignees") or []:
        if isinstance(item, dict) and item.get("login"):
            logins.append(item["login"])
    return logins


def detect_trigger(ctx: GitHubContext, inputs: Inputs) -> TriggerDecision:
    if inputs.prompt.strip():
        return TriggerDecision(True, "agent", "explicit prompt input", inputs.prompt.strip())

    phrase = inputs.trigger_phrase
    event = ctx.event_name
    action = ctx.event_action

    if event in {"issue_comment", "pull_request_review_comment", "pull_request_review"}:
        if contains_trigger(ctx.comment_body, phrase):
            return TriggerDecision(True, "tag", f"{phrase} mention in comment/review", remove_trigger(ctx.comment_body, phrase) or ctx.comment_body)
        return TriggerDecision(False, "tag", "no trigger phrase in comment/review", "")

    if event == "issues":
        text = f"{ctx.title}\n\n{ctx.body}"
        if action in {"opened", "edited"} and contains_trigger(text, phrase):
            return TriggerDecision(True, "tag", f"{phrase} mention in issue", remove_trigger(ctx.body or ctx.title, phrase) or text)
        if action == "assigned" and inputs.assignee_trigger:
            wanted = inputs.assignee_trigger.lstrip("@").lower()
            if any(login.lower() == wanted for login in _assignee_logins(ctx.payload)):
                return TriggerDecision(True, "tag", f"assigned to {inputs.assignee_trigger}", ctx.body or ctx.title)
        if action == "labeled" and inputs.label_trigger:
            wanted_label = inputs.label_trigger.lower()
            if any(name.lower() == wanted_label for name in _label_names(ctx.payload)):
                return TriggerDecision(True, "tag", f"label {inputs.label_trigger} applied", ctx.body or ctx.title)
        return TriggerDecision(False, "tag", "issue event without matching trigger", "")

    if event == "pull_request":
        supported = {"opened", "synchronize", "ready_for_review", "reopened", "edited"}
        text = f"{ctx.title}\n\n{ctx.body}"
        if inputs.track_progress and action in supported:
            if contains_trigger(text, phrase) or action in {"opened", "synchronize", "ready_for_review", "reopened"}:
                return TriggerDecision(True, "tag", "track_progress pull_request event", remove_trigger(ctx.body or ctx.title, phrase) or text)
        return TriggerDecision(False, "agent", "pull_request event requires prompt or track_progress", "")

    return TriggerDecision(False, "agent", "no prompt input for automation event", "")
