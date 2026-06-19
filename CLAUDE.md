# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

**Materials-Triage** — an agent for triaging materials-research inputs. The triage
logic and domain specifics will be added here later; until then, treat this section
as a placeholder and ask before assuming domain behavior.

> _TODO (owner to fill in): what gets triaged, inputs/outputs, scoring/routing rules._

The repo's agent-coding setup (commands, skills, settings) is documented in
[`.claude/README.md`](.claude/README.md).

## How to work here (collaboration rules)

These override default behavior — follow them exactly.

1. **Ask before choosing between approaches.** When there's more than one reasonable
   way to implement something (design, library, data structure, API shape), stop and
   ask which direction I want — don't pick one and run with it. Present the options
   with a short recommendation.

2. **Implement one function at a time.** Write a single function, then **stop and get
   my approval before moving to the next one.** Do not batch multiple functions or
   build out a whole module in one pass.

3. **TDD preferred.** Default to test-driven development: write the test(s) for a
   function before (or alongside) its implementation, and confirm they pass before
   asking to proceed. The `tdd-test-writer` agent is available for this.

## Git workflow

- **`main` is protected.** It requires **signed commits** (SSH signing is configured
  local to this repo) and passing branch-protection checks. Never push directly to
  `main`.
- **Commit signing is mandatory.** This repo signs commits with **SSH** (key
  `~/.ssh/id_ed25519.pub`), configured local to the repo: `gpg.format=ssh` selects
  the SSH method, and `commit.gpgsign=true` turns on auto-signing. (`commit.gpgsign`
  is a legacy name meaning "sign commits" — it does **not** imply GPG; `gpg.format`
  is what picks SSH vs GPG. GPG is not used or installed here.) Commits are signed
  automatically; `git commit -S` also works. An unsigned commit is rejected by
  branch protection.
- **To ship changes, use `/commit-commands:commit-push-pr`.** It branches off `main`,
  creates a signed commit, pushes, and opens a PR. Do not hand-roll the
  commit/push/PR sequence.
- Pushing triggers the `ask`-gated `git push` permission rule — expect a confirmation
  prompt; that's intended.
- **Merge PRs via the GitHub web UI, using squash — never merge locally.** A merge done
  in the web UI is created and signed by GitHub (shows "Verified"), which satisfies the
  `main` signed-commit requirement. Merging locally and pushing would require re-signing
  and is disallowed by branch protection anyway. Squash keeps `main` linear (1 PR = 1
  commit).
- **CI is a required check.** The `test` job (ruff + pytest) must pass before a PR can
  merge.
- **Merge manually after review — auto-merge is intentionally off.** Review the PR and
  its squash commit message in the web UI before clicking merge; CI passing is necessary
  but not sufficient. (Auto-merge is disabled on purpose so the final commit message gets
  a human check.)
- **After a PR merges, run `/sync-main`.** It fetches with prune, checks out `main`,
  fast-forward pulls, and safely deletes the merged branch (`-d` only; stops on
  uncommitted changes or a non-fast-forward pull). This keeps local `main` in sync and
  the branch list clean. Note: this is guidance I follow within a session when I know a
  PR merged — it is not an automatic trigger; nothing runs on the merge event itself.
