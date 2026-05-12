# Hermes Code Action Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a GitHub Action that mirrors the Claude Code Action user experience while routing all agent work through `hermes chat` and using `@hermes` as the default trigger.

**Architecture:** The action is a Python-stdlib composite action. A thin `action.yml` maps GitHub Action inputs to environment variables, then `python3 -m hermes_code_action` performs event parsing, trigger detection, GitHub API calls, branch setup, prompt construction, Hermes CLI execution, output publishing, and final GitHub comment updates.

**Tech Stack:** GitHub composite actions, Python 3.11+ stdlib, Hermes CLI, GitHub REST API, git.

---

## Compatibility target

The reference behavior is `anthropics/claude-code-action`, but with these v0.1 substitutions:

- `@claude` → `@hermes`
- `claude_args` → `hermes_args`
- Claude Code SDK execution → `hermes chat -q ...`
- Claude tracking comments → Hermes tracking comments
- Claude-created branches → `hermes/...` branches

## Task 1: Repository scaffold

**Objective:** Create a standalone local action repository.

**Files:**
- Create: `action.yml`
- Create: `src/hermes_code_action/*.py`
- Create: `tests/*.py`
- Create: `README.md`
- Create: `examples/*.yml`

**Verification:** `python3 -m compileall src tests`

## Task 2: Trigger detection

**Objective:** Detect the same high-level modes as Claude Code Action.

**Implementation:**
- Agent mode if `prompt` input is present.
- Tag mode on `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `issues`, and selected `pull_request` events.
- Default trigger phrase is `@hermes` with word-ish boundaries and hidden-control stripping.

**Verification:** `python3 -m unittest tests.test_triggers -v`

## Task 3: GitHub context collection

**Objective:** Collect enough issue/PR context for Hermes to act like an in-thread coding assistant.

**Implementation:**
- Parse `GITHUB_EVENT_PATH`.
- Fetch issue/PR metadata, comments, PR diff, review comments, and check runs through GitHub REST.
- Filter included comments with actor include/exclude inputs.

**Verification:** Unit-test event parsing and prompt rendering with fixture payloads.

## Task 4: Branch and git behavior

**Objective:** Match the familiar branch workflow.

**Implementation:**
- For same-repo open PRs, checkout the PR head branch.
- For issues and fork PRs, create `hermes/<entity>-<number>-<timestamp>` branches from the base/default branch.
- Configure bot git identity and authenticated remote.
- Validate branch names before git calls.

**Verification:** Unit-test branch validation and branch-name template expansion.

## Task 5: Hermes execution

**Objective:** Invoke Hermes as the AI harness endpoint.

**Implementation:**
- Build command: `hermes chat -q <prompt> -Q --source github-action --yolo -t file,terminal,web`.
- Support provider/model/toolset/max-turns overrides.
- Scrub Actions OIDC request env vars.
- Save execution metadata JSON to `$RUNNER_TEMP/hermes-execution-output.json`.

**Verification:** Unit-test command construction and dry-run main flow.

## Task 6: GitHub UX

**Objective:** Provide the same in-GitHub experience: one trigger comment, progress comment, final report, branch/PR links, outputs.

**Implementation:**
- Create initial tracking comment for tag mode.
- Update final tracking comment with success/failure, run link, branch link, PR creation link, and Hermes result.
- Set outputs: `execution_file`, `branch_name`, `github_token`, `structured_output`, `session_id`, `conclusion`, `hermes_comment_id`.

**Verification:** Unit-test comment formatting.

## Task 7: Security baseline

**Objective:** Make v0.1 safe enough for trusted-maintainer usage.

**Implementation:**
- Require write/maintain/admin permission for entity-triggered runs unless explicitly bypassed.
- Deny bot actors by default.
- Mask token in logs.
- Remove OIDC request env from Hermes subprocess.
- Hide full Hermes output unless requested.
- Document public-repo risks and recommended permissions.

**Verification:** Unit-test actor validation branches with mocked API.

## Task 8: Final verification and commit

**Commands:**

```bash
python3 -m compileall src tests
python3 -m unittest discover -s tests -v
git status --short
git add .
git commit -m "feat: add Hermes GitHub code action"
```

## Known v0.1 gaps vs Claude Code Action

- No Claude Code SDK JSON stream compatibility.
- No GitHub App OIDC token exchange; `github_token` is required/defaults to `github.token`.
- No MCP shim for comment updates during execution; comments update at start and finish.
- No inline review comment buffering/classification.
- No API commit-signing mode.
- No sticky-comment reuse yet.
- No automatic config restoration for PR-controlled Hermes config; use trusted actors only.
