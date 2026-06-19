---
description: Write, resume, or update a session handoff document so the next session can resume seamlessly
argument-hint: "[create|resume|update] [optional path or short note]"
allowed-tools: Bash(git branch --show-current), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(date:*), Bash(mkdir:*), Bash(ls:*), Read, Write
---

You are managing a **session handoff document**. Its purpose is to let a future session — possibly a different person, possibly you after `/clear` or a break — pick up exactly where this one left off with zero re-discovery.

## Dispatch on the subcommand

Read the first word of `$ARGUMENTS` to pick the mode. Anything after it is the path/note argument.

| Command | Job |
|---------|-----|
| `create` | Generates the structured document from current session context |
| `resume` | Loads a handoff document, confirms understanding, and waits for approval before starting |
| `update` | Updates an existing handoff with section-specific merge rules (see below) |

If no subcommand is given, default to **`create`**.

---

## `create` — write a fresh handoff

### When this gets used (tailor the emphasis accordingly)

| Scenario | What matters most in the doc |
|----------|------------------------------|
| End of work day | Clear next steps so tomorrow starts fast |
| Before context limit / `/clear` | Preserve full state — nothing lives only in the soon-to-be-wiped context |
| Switching focus areas | Enough context to cold-start the *other* task later |
| Interruption expected | Capture the in-flight thought before it's lost |
| Complex debugging | Hypotheses tried, what was ruled out, what's still suspected |

If the note in `$ARGUMENTS` hints at the scenario (e.g. "debugging", "eod", "context limit"), weight the document toward that column. Otherwise infer it from the session.

### Step 1 — Gather real state (don't guess)

Run these and use the actual output:

- `git branch --show-current` — the branch name
- `git status --short` — modified / staged / untracked files
- `git diff --stat` — scope of uncommitted changes
- `git log --oneline -5` — recent commits for context
- `date "+%Y-%m-%d %H:%M"` — timestamp

Pull "Work Done", "Discoveries", and "Status" from **this conversation's actual history** — the tasks you worked on, choices made and why, commands that failed, things left half-done. Be concrete: name real files, functions, error messages, and test names. A handoff full of `[placeholder]` text is useless.

### Step 2 — Write the document

Fill in this template. Drop any section that genuinely has nothing to say rather than padding it. For a debugging handoff, expand "Status" into hypotheses tried / ruled out / still suspected.

```markdown
# Session Handoff - <YYYY-MM-DD HH:MM>

## Task
- <the overarching task being worked on>

## Scope
- <what is in scope / out of scope for this work>

## Files
- <actual paths touched, from git status>

## Discoveries
- <non-obvious findings, gotchas, things learned this session>

## Work Done
- <key task completed — be specific, include commit hashes where relevant>

## Status
- Working: <what is verified working>
- Partial: <what is half-done and where it stands>
- Blockers / known issues: <crashes, failing tests, open questions>

## Next Steps
1. <immediate next task — the very first thing to do>
2. <dependent task>
3. <follow-up / validation, e.g. run pytest>

## Context for Next Session
- Branch: <git branch>
- Uncommitted: <summary of git status — or "clean">
- How to verify: <command to run to confirm state, e.g. `pytest src/`>
- Dependencies / external factors: <services, credentials, pending reviews>
```

### Step 3 — Save it

- If the argument is a path (ends in `.md` or contains `/`), write there.
- Otherwise write to `docs/handoffs/handoff-<YYYY-MM-DD-HHMM>.md`, creating the directory if needed.

After writing, print the file path and a 2-3 line summary of the most important next step so the user sees it without opening the file.

---

## `resume` — load a handoff and stand ready

1. Locate the handoff document:
   - If the argument is a path, read that file.
   - Otherwise `ls -t docs/handoffs/*.md` and read the most recent one.
2. Read it in full, then re-ground against reality: run `git branch --show-current` and `git status --short` to confirm the branch and working tree match what the doc expects. Flag any drift (wrong branch, files already changed, commits since the handoff).
3. Reply with a short confirmation of understanding:
   - The task and current status, in your own words
   - The concrete **Next Steps** you're about to take
   - Any drift or open questions you noticed
4. **Stop and wait for approval.** Do not start the work until the user confirms.

---

## `update` — merge new progress into an existing handoff

1. Locate the existing handoff the same way `resume` does (argument path, else most recent in `docs/handoffs/`). If none exists, fall back to `create`.
2. Gather fresh state exactly as in `create` Step 1 (git status, diff, log, date).
3. Rewrite the document, applying these **per-section merge rules** — do not blindly overwrite:

| Section | Merge Rule |
|---------|-----------|
| Task, Scope | Keep or refine |
| Files | Merge — combine original with new files touched |
| Discoveries | Append — add new findings, never remove prior ones |
| Work Done | **Append only** — add new entries, never delete history, include commit hashes |
| Status | Replace — write current state |
| Next Steps | Replace — write updated checklist |

4. Write back to the **same file** (preserve its path). Update the timestamp in the title.

After writing, print the file path and a short summary of what changed (which sections were appended vs. replaced).
