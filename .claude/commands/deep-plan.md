---
description: Run a multi-round deep planning pass (analyze → self-challenge → definitive plan) for complex tasks
argument-hint: "<feature / task to plan> (e.g. migrate the auth system to OAuth2)"
model: claude-opus-4-8
allowed-tools: Bash(git branch --show-current), Bash(git status:*), Bash(git log:*), Bash(date:*), Bash(mkdir:*), Bash(ls:*), Read, Grep, Glob, Write, Task
---

You are running a **deep, multi-round planning pass** for a complex task. The goal is a rigorous, self-critiqued plan — not a first-draft outline. Use the most capable Claude model (Opus); this command is already pinned to it.

The task to plan is: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user what they want planned and stop.

**The core logic is an escalating loop — reasoning effort rises each round:**

> **think → plan → think harder → refine plan → think hardest → finalize**

- Round 1: **think** (medium effort) → produce the first plan.
- Round 2: **think harder** (high effort) → challenge that plan, then refine it.
- Round 3: **think hardest** (maximum effort) → finalize the definitive plan.

Each round must reason *more* deeply than the one before it. Do not let later rounds coast on earlier conclusions — re-examine them under harder scrutiny.

Do all three rounds in sequence, in this single invocation. Show each round's output so the user can see the reasoning evolve — do not collapse them into one answer. Before Round 1, gather just enough real context (read the relevant files, `grep`/`glob` for the affected components, check `git status`/branch) so the analysis is grounded in this codebase, not generic.

---

## Round 1 — Initial analysis  *(think → plan; medium effort)*

**Think** at a moderate depth, then **plan**. Produce a first-pass analysis of the task:

- The key components and where they live (real file paths, functions, modules).
- Dependencies — what touches what, internal and external.
- The shape of the change and rough sequencing.
- Potential risks, called out plainly.

Label this section `## Round 1 — Initial Analysis`.

---

## Round 2 — Deep challenge  *(think harder → refine plan; high effort)*

**Think harder.** Adversarially critique your *own* Round 1 output. Do not defend it — try to break it:

- What assumptions did I make that might be wrong?
- What failure modes, edge cases, or dependencies did I miss?
- What would a senior engineer (and a senior security engineer) flag here?
- Where is the sequencing fragile or the risk underestimated?

Then **refine the plan** — write a deeper analysis with self-correction that incorporates what the critique surfaced.

Label this section `## Round 2 — Deep Challenge & Self-Correction`.

---

## Round 3 — Final plan  *(think hardest → finalize; maximum effort)*

**Think hardest.** Based on **both** prior rounds, **finalize** the **definitive plan**. It must be concrete, executable, and **human-readable**. Write it for a person skimming, not just for an implementer.

Label this section `## Round 3 — Final Plan` and structure it in this exact order:

**1. High-level summary & logic**
- Open with a short plain-language summary of *what* changes and *why* (the logic/motivation behind it). A few sentences — no jargon dumps.

**2. Files changed / added / deleted**
- List the files touched, grouped by **Changed**, **Added**, and **Deleted**.
- One file per bullet, with a few words on what happens to it.

**3. Codebase impact (1–5)**
- Give a single overall impact score from **1 (trivial, isolated)** to **5 (sweeping, cross-cutting)**, with one line justifying the rating.

**4. Step-by-step changes**
- Break the work into the **smallest understandable pieces**. **Only ONE change per bullet point** — never bundle two changes in one bullet. Use sub-bullets to add detail (the real file/component touched, rollback/mitigation, and how to verify that one piece).
- Keep the steps ordered so they can be executed top to bottom.

**5. Open questions / decisions for the user** (if any).

Keep prose tight and scannable — prefer short bullets over dense paragraphs throughout.

---

## Save the plan

Derive a short `<featurename>` slug from the task (kebab-case). Get the timestamp with `date "+%Y-%m-%d-%H%M"`.

Write the **Round 3 final plan** (with a short header linking back to the task, branch, and date) to:

```
Deep-Plan-<featurename>-<YYYY-MM-DD-HHMM>.md
```

in the repository root, unless the user specified a path. Create directories if needed.

After saving, print the file path and a 3-4 line summary of the plan's first concrete step and its biggest risk.
