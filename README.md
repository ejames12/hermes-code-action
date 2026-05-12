# Hermes Code Action

A local prototype of a GitHub Action that mirrors the [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) user experience while using **Hermes Agent** as the AI harness endpoint.

The default trigger is:

```text
@hermes
```

From the GitHub user's perspective, this is intended to feel like Claude Code Action:

1. A maintainer comments `@hermes fix this` on an issue or pull request.
2. The action creates a working/progress comment.
3. Hermes receives the GitHub context, edits the checked-out repository, runs checks, commits, and pushes when appropriate.
4. The action updates the same GitHub comment with the final result plus branch/PR links.

## Current status

This is a v0.1 local implementation. It is functional as a composite action, but it is not yet a byte-for-byte drop-in replacement for Claude Code Action.

Implemented:

- `@hermes` tag mode for issues, issue comments, PR review comments, PR reviews, and selected PR lifecycle events.
- Explicit `prompt` agent mode for `workflow_dispatch`, `schedule`, and other automation workflows.
- GitHub REST context collection: issue/PR metadata, comments, PR diffs, review comments, and check runs.
- Same-repo PR checkout; issue/fork-PR branch creation under `hermes/`.
- Initial and final tracking comments.
- Hermes CLI execution through `hermes chat -q ...`.
- Action outputs compatible with the Claude Code Action shape where practical.
- Python stdlib only: no npm/bun/runtime dependency for the action code itself.

Not yet implemented:

- Streaming progress updates while Hermes is running.
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
| `hermes_yolo` | `true` | Passes `--yolo` for non-interactive CI execution. Use only with trusted triggers. |
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
