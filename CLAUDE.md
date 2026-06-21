# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

**Materials-Triage** — a public-data-only agent that turns a scientist's natural-language
request into a **ranked, fully-cited shortlist of candidate materials**, with caveats and
clearly-marked missing/uncertain data, in two views: a concise **PI summary** and a detailed
technical **audit** view. It never triggers wet-lab actions, reads private lab data, or scrapes
paywalled sources, and resists basic prompt-injection by construction.

**Load-bearing decision:** the LLM never invents scientific facts. Public databases supply
every number (tagged with provenance), deterministic code filters and ranks, and the LLM only
builds the spec, proposes hypotheses, and writes grounded, cited narrative. This yields three
properties: **traceable** (every run is a recorded, replayable `TriageRun`), **configurable**
(tweak a knob, resume from that step), and **generalizable with zero setup** (no mandatory
profile — the spec is LLM-built and remembered in lab memory).

**Workflow (traced state machine, not an autonomous tool-calling loop):**

1. **Input policy gate** — allowlist; forbidden/out-of-scope → logged refusal (not recorded).
2. **Spec building (LLM + human)** — Convert language → Filling Spec (`TriageSpec`); on missing
   fields, Recommend LLM (seeded by built-in defaults + lab memory) → user accepts or fills in
   (manual fills pass a user-input gate) → Final Spec.
3. **Hypothesis (LLM + RAG)** — propose candidates / property ranges from literature; hypotheses, not facts.
4. **Retrieve (code, not LLM)** — deterministic API calls; **v1: Materials Project only**. Returns
   candidates + `PropertyValue`s, each carrying `Provenance`.
5. **Hard filters** — drop any candidate violating any hard constraint; each drop records a reason.
6. **Ranking** — **v1: weighted average** of target properties; applies `on_missing` and flags missing data.
7. **Synthesis (LLM + RAG)** — grounded narrative + mechanistic "why," each claim cited; no invented numbers.
8. **Output validator** — every referenced ID + citation must resolve to retrieved provenance; ungrounded → reject & retry.
9. **Render** — `view=pi` concise summary · `view=audit` renders the full trace.

Cross-cutting: a **literature RAG** (BM25 over OpenAlex/Crossref abstracts, treated as untrusted
DATA) feeds steps 3 & 7; two persistence buckets — **lab memory** (saved specs) and **`TriageRun`**
(per-run trace) — and the trace saves back to memory.

**Locked decisions:** vertical slice (one real source + stubs + design note) · first source
Materials Project · LLM = Claude on **AWS Bedrock** (IAM creds, no Anthropic key; mockable for
offline eval) · installable package + CLI · literature = metadata/abstracts only (no full text) ·
RAG = lexical BM25, keyless, per-query in-memory · execution = traced state machine · re-runs via
automatic step-cache + `resume --from`. No mandatory profile; cross-source merge deferred;
standalone caveats stage deleted (missing flags are a byproduct of ranking).

**Why it's safe & honest:** facts come from tools not the LLM (hallucination is structurally
impossible in the numeric layer) · capability-by-construction (no wet-lab/private-DB/scraper tool
exists) · retrieved text is untrusted DATA never instructions · output validator enforces
resolvable IDs/citations · missing data is first-class (ranked-but-flagged, never silently
dropped or guessed) · no DB to host (HTTP client over public APIs; only local state is run traces
+ memory).

Full design lives in `Deep-Plan-materials-triage-agent-2026-06-19-1429.md` (§0 has the workflow
diagram). Implementation is **underway on the core data-model layer** — the frozen
`Provenance`, `PropertyValue`, `Candidate`, `Constraint` (a hard filter holding an
inclusive min/max bound on one property), `RankingTarget` (a soft scoring preference
whose `weight` is a proportional share in `(0, 1]`), and `TriageSpec` (the fully-resolved
request bundling constraints, ranking targets, and composition rules) models in
`src/materials_triage/core/schema.py` exist so far, alongside the canonical 118-symbol
`ELEMENT_SYMBOLS` frozenset in `src/materials_triage/core/elements.py`. The
hypothesis layer in `src/materials_triage/core/hypothesis.py` also exists — the
frozen `Citation` (the untrusted-DATA analog of `Provenance`), `ElementRule` (a
symbol-validated require/exclude composition rule), `Proposal` (one cited bridge
whose `kind` discriminates a `Constraint`/`RankingTarget`/`ElementRule` payload),
and `Hypothesis` (the LLM's whole emission: `proposals` + `mechanism`) models,
plus the pure `compile_spec(proposals) -> TriageSpec` seam that dispatches on
`kind`, unions element rules into required/excluded sets, and normalizes ranking
weights to sum to 1. It proceeds as single-function TDD increments (see the build
order in the deep plan), and only on an explicit go-ahead.

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

3. **TDD preferred — use the `tdd` skill.** Default to test-driven development via the
   `tdd` skill (red-green-refactor). Work in **vertical slices**: one failing test →
   minimal code to pass → repeat — never write all tests up front (that produces tests
   of imagined behavior). Test observable behavior through public interfaces, not
   implementation details. Confirm tests pass before asking to proceed. (The
   `tdd-test-writer` agent can also help author tests.)

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
- **Lint locally before pushing — install the pre-commit hook.** `.pre-commit-config.yaml`
  runs the same ruff format + lint checks CI enforces, at commit time. It is **not** active
  until you run `pre-commit install` once per clone (git hooks aren't checked in). Without
  it, a misformatted commit only fails on CI after push. Run `pre-commit run --all-files`
  to check the whole tree manually; bump the pinned `rev` in lockstep with the ruff version.
- **Merge manually after review — auto-merge is intentionally off.** Review the PR and
  its squash commit message in the web UI before clicking merge; CI passing is necessary
  but not sufficient. (Auto-merge is disabled on purpose so the final commit message gets
  a human check.)
- **After a PR merges, run `/sync-main`.** It fetches with prune, checks out `main`,
  fast-forward pulls, and safely deletes the merged branch (`-d` only; stops on
  uncommitted changes or a non-fast-forward pull). This keeps local `main` in sync and
  the branch list clean. Note: this is guidance I follow within a session when I know a
  PR merged — it is not an automatic trigger; nothing runs on the merge event itself.
