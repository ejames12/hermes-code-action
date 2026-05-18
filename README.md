# Hermes Code Action

A local prototype of a GitHub Action that mirrors the [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) user experience while using **Hermes Agent** as the AI harness endpoint.

The default trigger is:

```text
@hermes
```

From the GitHub user's perspective, this is intended to feel like Claude Code Action:

1. A maintainer comments `@hermes fix this` on an issue or pull request.
2. The action creates a working/progress comment.
3. Hermes receives the GitHub context, edits the checked-out repository when appropriate, runs checks, and commits intended changes.
4. The action wrapper publishes only the safe working branch and updates the same GitHub comment with the final result plus branch/PR links.

## Current status

This is a v0.1 local implementation. It is functional as a composite action, but it is not yet a byte-for-byte drop-in replacement for Claude Code Action.

Implemented:

- `@hermes` tag mode for issues, issue comments, PR review comments, PR reviews, and selected PR lifecycle events.
- Explicit `prompt` agent mode for `workflow_dispatch`, `schedule`, and other automation workflows.
- GitHub REST context collection: issue/PR metadata, comments, PR diffs, review comments, and check runs.
- Same-repo PR checkout; issue/fork-PR branch creation under `hermes/`.
- Initial, live milestone, and final tracking-comment updates.
- `@hermes plan ...` mode that writes a Markdown implementation plan under `docs/hermes-plans/` and returns a GitHub link.
- Wrapper-owned branch publishing with guardrails that refuse direct pushes to `main`, `master`, the repository default branch, or the PR base branch.
- Optional staged multi-model orchestration: planner, implementer, reviewer, and adjudicator Hermes runs with per-stage provider/model/toolset overrides plus per-stage summary comments.
- Hermes CLI execution through `hermes chat -q ...`.
- Action outputs compatible with the Claude Code Action shape where practical.
- Python stdlib only: no npm/bun/runtime dependency for the action code itself.

Not yet implemented:

- Claude Code SDK JSON stream compatibility.
- API commit-signing mode.
- Inline PR review comment buffering/classification.
- Sticky comment reuse.
- GitHub App/OIDC token exchange; use `github_token`.

## Basic usage

Create `.github/workflows/hermes.yml` in the repository that should respond to `@hermes`:

```yaml
name: Hermes

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  pull_request_review:
    types: [submitted]
  issues:
    types: [opened, edited, assigned, labeled]

permissions:
  contents: write
  issues: write
  pull-requests: write
  checks: read

jobs:
  hermes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install Hermes
        run: |
          curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - uses: owner/hermes-code-action@v0
        with:
          trigger_phrase: "@hermes"
          github_token: ${{ secrets.GITHUB_TOKEN }}
          hermes_toolsets: "file,terminal,web"
```

For local development in this repo, `examples/hermes.yml` uses `uses: ./`.

## `@hermes plan` mode

Comment with `@hermes plan ...` on an issue or PR when you want a reviewable implementation plan instead of code changes. Hermes may inspect the repository, then creates or updates a Markdown plan under `docs/hermes-plans/` with diagrams when useful. The wrapper pushes the non-protected Hermes branch and the final tracking comment includes a **View plan** link to the rendered Markdown file on GitHub.

Plan-mode runs are validated before publishing: only the plan file and directly related assets under `docs/hermes-plans/` are allowed to change.

## Staged multi-model orchestration

By default the action runs a single Hermes invocation. Set `orchestration_mode: staged` to run a Phase 2 pipeline where each stage can use a different Claude Code model or Hermes provider/model/toolset:

1. **Planner** — creates the plan or design context.
2. **Implementer** — edits code, runs checks, and commits intended changes.
3. **Reviewer** — review-only validation. The wrapper records git state before/after and fails the run if this stage edits files, commits, or changes branches.
4. **Adjudicator** — final decision. It receives prior stage outputs and must explicitly consider configured reviewer findings.

The wrapper still owns publishing: Hermes stages must not push, and the action refuses to publish protected branches.

For GitHub tag-mode runs, staged orchestration uses two comment streams:

- one tracking comment is created up front with the planned stage checklist and is updated as stages complete;
- one new issue/PR comment is posted after each completed stage with a clean status, duration, servicing provider/model, and concise stage summary.

If a stage fails or its output suggests human attention is needed, the stage summary comment mentions the issue/PR assignees so a human can review the blocker or decision point.

Example workflow inputs:

```yaml
- uses: owner/hermes-code-action@v0
  with:
    trigger_phrase: "@hermes"
    github_token: ${{ secrets.GITHUB_TOKEN }}
    orchestration_mode: staged
    orchestration_policy: .hermes/code-action.yml
    workflow: default
```

Example `.hermes/code-action.yml`:

```yaml
version: 1
workflows:
  default:
    stages:
      - name: planner
        mode: plan
        claude_code_model: opus
        claude_code_allowed_tools: Read,Bash
        toolsets: file,terminal,web
        max_turns: 60

      - name: implementer
        mode: implement
        claude_code_model: sonnet
        claude_code_allowed_tools: Read,Edit,Write,Bash
        toolsets: file,terminal
        max_turns: 90

      - name: reviewer
        mode: review
        provider: openai
        model: gpt-5.1
        toolsets: file,terminal
        max_turns: 30

      - name: adjudicator
        mode: adjudicate
        claude_code_model: sonnet
        claude_code_allowed_tools: Read,Bash
        toolsets: file,terminal
        max_turns: 30
        must_consider:
          - reviewer
```

Use `claude_code_model` for stages that should delegate the substantive work through Claude Code CLI (`opus` for planning, `sonnet` for implementation/adjudication). Use `provider`/`model` for stages that should run directly on Hermes's configured harness, such as the reviewer stage above. Policy files may be JSON or YAML. YAML support uses PyYAML when available; JSON works with the Python stdlib. If `orchestration_mode: staged` is set and no policy file is provided/found, the action uses a safe built-in four-stage default: planner via Claude Code `opus`, implementer via Claude Code `sonnet`, reviewer via Hermes's default model/harness, and adjudicator via Claude Code `sonnet`.

Global `hermes_args` are applied to every staged Hermes invocation. Stage `extra_args` are appended to the global args; if both configure the same flag (for example `-s` or `--profile`), the stage-specific flag takes precedence and the duplicate global flag/value is removed for that stage.

For issue or pull request triggers, Hermes sessions are titled from the GitHub entity using `issue #123: Title` or `pr #123: Title`. After each Hermes invocation completes, the action renames the exact `session_id` reported by Hermes, preserving any configured `--profile` so the correct session database is updated. Workflow-dispatch/agent-mode runs without an issue or PR title remain untitled unless Hermes auto-title generation names them.

When a staged phase that normally delegates to Claude Code CLI fails with output that looks like Claude/Anthropic throttling (`rate limit`, `429`, `too many requests`, `overloaded`, etc.), the action can retry that phase once with a secondary Hermes model. Configure `hermes_fallback_provider` and/or `hermes_fallback_model`; fallback retries intentionally do not load `hermes_args: -s claude-code` unless you explicitly set `hermes_fallback_args`.

For the Opus → Sonnet → GPT → Sonnet pattern: put Opus on `planner`, Sonnet on `implementer`, GPT on `reviewer`, and Sonnet on `adjudicator` with `must_consider: [reviewer]`. The adjudicator prompt requires triaging GPT's findings; the wrapper validates tests/branch rules and humans still approve merges.

## Agent mode

Use an explicit prompt when you do not want mention detection:

```yaml
- uses: owner/hermes-code-action@v0
  with:
    prompt: |
      Review the repository for trivial documentation typos.
      If you make changes, commit them to the current branch.
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

## Key inputs

| Input | Default | Description |
|---|---:|---|
| `trigger_phrase` | `@hermes` | Mention phrase for tag mode. |
| `prompt` | empty | Explicit agent-mode prompt. |
| `github_token` | `${{ github.token }}` | Token for GitHub API, comments, and push. |
| `branch_prefix` | `hermes/` | Prefix for branches created from issues/fork PRs. |
| `hermes_toolsets` | `file,terminal,web` | Toolsets passed to `hermes chat`. |
| `hermes_model` | empty | Optional model override. |
| `hermes_provider` | empty | Optional provider override. |
| `hermes_fallback_model` | empty | Secondary Hermes model for staged-phase retries when Claude Code CLI appears throttled. |
| `hermes_fallback_provider` | empty | Secondary Hermes provider for fallback retries. |
| `hermes_fallback_args` | empty | Optional args for fallback retries. Leave empty to avoid loading `claude-code` on fallback. |
| `hermes_yolo` | `true` | Passes `--yolo` for non-interactive CI execution. Use only with trusted triggers. |
| `orchestration_mode` | `single` | Set to `staged` for planner/implementer/reviewer/adjudicator runs. |
| `orchestration_policy` | empty | Optional `.json`/`.yml` policy file path for staged mode. |
| `workflow` | `default` | Workflow name inside the staged policy file. |
| `install_hermes` | `false` | Run `hermes_install_command` if Hermes is missing. Prefer an explicit pinned install step. |
| `dry_run` | `false` | Build prompt/comments but skip Hermes. |

See `action.yml` for the complete input list.

## Security guidance

This action can run an autonomous coding agent with file and terminal tools in a write-token GitHub Actions job. Treat it as privileged automation.

Recommended v0.1 configuration:

- Only allow trusted repository writers to trigger it. The action checks actor permission for entity-triggered runs.
- Keep `allowed_non_write_users` empty for public repositories.
- Deny bots unless a specific trusted bot is listed in `allowed_bots`.
- Avoid `pull_request_target` unless you add stronger checkout/config isolation.
- Install Hermes with a pinned, organization-approved command instead of `curl | bash` in production.
- Do not enable full logs (`show_full_output`) in public repositories unless you are comfortable with all Hermes output being visible.

## How Hermes is invoked

The action constructs a prompt from GitHub context, then runs approximately:

```bash
hermes chat \
  -q "<constructed prompt>" \
  -Q \
  --source github-action \
  --yolo \
  -t file,terminal,web
```

You can customize this with `hermes_provider`, `hermes_model`, `hermes_toolsets`, `hermes_max_turns`, and `hermes_args`.

## Development

Run local checks:

```bash
python3 -m compileall src tests
python3 -m unittest discover -s tests -v
```

A dry-run smoke test can be performed by setting a fixture `GITHUB_EVENT_PATH` and `INPUT_DRY_RUN=true`; see `tests/test_main.py`.

## Project layout

```text
action.yml                  Composite GitHub Action definition
src/hermes_code_action/     Python action implementation
docs/PLAN.md                Implementation plan and known gaps
examples/                   Example workflows
tests/                      stdlib unittest suite
```
