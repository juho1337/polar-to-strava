# GitHub settings and review enforcement

What is enforced by files in this repository, what GitHub settings can add, and what needs
a manual change by the repository owner. Nothing here has been changed through the API;
settings changes are made by the owner.

## State when this was written (2026-11-24)

| Item | Finding |
| --- | --- |
| Visibility | Public. Branch protection and rulesets are available. |
| `main` | Not protected (the branch protection API reports "Branch not protected"); no rulesets. |
| Merge methods | Merge commit, squash and rebase are all allowed; head branches are not deleted automatically. |
| CI | None existed before this change. `CI` (`.github/workflows/ci.yml`) now runs pytest, ruff, black and mypy on Windows for pull requests and `main`. |
| Codex review | Triggered by opening a pull request, marking a draft ready, or commenting `@codex review` / `@codex security review`. Configured in Codex settings, not in this repository. |

## Recommended ruleset for `main`

Create a repository ruleset (or classic branch protection) on `main`:

1. Require a pull request before merging; block direct pushes.
2. Require the `checks` status check (the `CI` workflow) to pass, with branches up to date.
3. Block force pushes and branch deletion.
4. Optionally require conversation resolution, so Codex review threads must be resolved or
   answered before merge.
5. Enable "Automatically delete head branches" and prefer squash or merge commits per taste.

### Limits worth knowing

- **Approvals cannot prove independence here.** Claude Code and the owner act through the
  same GitHub account, and GitHub does not let an author approve their own pull request.
  A "required approvals" rule would therefore either block the owner or need a bypass.
  The "never self-merge" rule is enforced by [AGENTS.md](../AGENTS.md) and by the owner
  being the only one who presses Merge, not by GitHub.
- **Codex review is advisory.** GitHub cannot require "a Codex review happened". The pull
  request template carries a Codex review checklist, and Codex posts a summary comment
  with the reviewed commit. Check that the summary refers to the latest commit before
  merging. Requiring conversation resolution is the closest enforceable proxy.
- **Actions budget.** A run on a private repository of this owner was refused with "an
  Actions budget is preventing further use". If `CI` is refused for the same reason here,
  fix the spending limit under account billing; no repository change helps.

## Manual actions for the owner

- [ ] Create the `main` ruleset above.
- [ ] Check Codex settings: automatic reviews enabled for this repository.
- [ ] Check the Actions spending limit if a workflow is not started.
