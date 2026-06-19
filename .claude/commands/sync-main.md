---
description: After a PR merges, return to an updated main and prune the merged branch
allowed-tools: Bash(git fetch:*), Bash(git checkout:*), Bash(git pull:*), Bash(git branch:*), Bash(git status:*), Bash(git rev-parse:*), Bash(gh pr view:*)
---

Post-merge sync: bring local `main` up to date with `origin` and clean up the branch
whose PR just merged. Run this after a PR opened via `/commit-commands:commit-push-pr`
is merged.

## Steps

1. Record the current branch: `git rev-parse --abbrev-ref HEAD` → call it `$CUR`.
2. **Safety first** — run `git status --porcelain`. If there are **uncommitted
   changes**, STOP and tell the user. Do not stash, discard, or switch away from their
   work without explicit instruction.
3. `git fetch origin --prune` (updates remote-tracking refs; marks deleted remotes as `[gone]`).
4. `git checkout main`
5. `git pull --ff-only origin main` — fast-forward only. If it can't fast-forward,
   STOP and report (don't merge/rebase silently).
6. **Prune the merged branch.** If `$CUR` was not `main`:
   - Confirm it's safe to delete: either its PR shows `state == MERGED`
     (`gh pr view $CUR --json state -q .state`), OR it appears in
     `git branch --merged main`.
   - If safe: `git branch -d $CUR` (note: `-d` refuses unmerged branches — never use
     `-D`).
   - If NOT confirmed merged: leave it and say so.
   - Also delete any other local branches listed as `[gone]` in `git branch -vv`,
     applying the same `-d` safety.
7. Report: the new `main` HEAD (`git log --oneline -1`), which branches were deleted
   vs. skipped, and confirm the working tree is clean.

## Rules

- Never force-delete (`git branch -D`) or discard uncommitted work.
- Never rebase/merge to force a non-fast-forward pull — report instead.
- This overlaps with the official `/commit-commands:clean_gone`; this command adds the
  "checkout main + fast-forward pull" step and PR-merge verification on top of pruning.
