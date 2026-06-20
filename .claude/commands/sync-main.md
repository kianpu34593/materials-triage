---
description: After a PR merges, return to an updated main and prune the merged branch
allowed-tools: Bash(git fetch:*), Bash(git checkout:*), Bash(git pull:*), Bash(git branch:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git log:*), Bash(gh pr view:*)
---

Post-merge sync: bring local `main` up to date with `origin` and prune branches whose
PRs have merged. Run this after a PR (opened via `/commit-commands:commit-push-pr`) is
merged. This repo merges via **squash**, which the prune logic below accounts for.

## Steps

1. Record the current branch: `git rev-parse --abbrev-ref HEAD` → call it `$CUR`.
2. **Safety first** — run `git status --porcelain`. If there are uncommitted changes
   (tracked edits) or untracked WIP, do **not** discard, stash, or switch away from the
   user's work without explicit instruction. If `$CUR` is an unmerged work-in-progress
   branch, prefer the in-place update (4b) so the user stays on it.
3. `git fetch origin --prune` — updates remote-tracking refs; branches deleted on the
   remote show as `[gone]`.
4. Update local `main` (pick one):
   - **4a — land on main (clean tree):** `git checkout main && git pull --ff-only origin main`.
   - **4b — keep current WIP branch:** fast-forward in place without switching:
     `git branch -f main origin/main` (safe: a non-fast-forward would be rejected).
   - If `main` cannot fast-forward, STOP and report (never merge/rebase silently).
5. **Prune merged branches — squash-aware.** A branch is safe to delete when verified
   merged by EITHER signal:
   - it shows `[gone]` in `git branch -vv` (remote deleted after merge), OR
   - `gh pr view <branch> --json state -q .state` returns `MERGED`.

   ⚠️ Under **squash** (and rebase) merges, the branch's commits are NOT ancestors of
   `main` — so `git branch -d` and `git branch --merged main` will **not** recognize
   them as merged. That is expected, not a reason to keep the branch. Therefore delete
   each verified-merged branch with **`git branch -D <branch>`**; the merge verification
   above is what makes the force-delete safe. Apply this to `$CUR` (if it merged) and to
   any other `[gone]` local branches. Never `-D` a branch that fails BOTH checks — leave
   it and say so. Never delete `$CUR` if it is itself unmerged WIP.
6. Report: the new `main` HEAD (`git log --oneline -1 main`), which branches were deleted
   vs. skipped (and why), the current branch, and confirm any WIP is intact.

## Rules

- `git branch -D` is allowed ONLY after a branch is verified merged via `[gone]` or
  PR=MERGED — this is required because squash/rebase merges defeat `-d`'s ancestry
  check. Never `-D` an unverified branch.
- Never discard or stash the user's uncommitted/untracked work without explicit
  instruction.
- Never rebase/merge to force a non-fast-forward `main` update — report instead.
- Overlaps with `/commit-commands:clean_gone`; this command adds the `main` fast-forward
  + PR-merge verification and handles squash-merged branches.
