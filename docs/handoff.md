# Session Handoff - 2026-06-22 20:51

> Single living handoff, git-tracked at `docs/handoff.md`. Keep it **lean** — deep per-session
> history lives in [`docs/handoff-archive.md`](handoff-archive.md); recurring gotchas live in
> auto-memory (`MEMORY.md`). Do NOT recreate dated copies or a `docs/handoffs/` subdir.

> **Read first if picking up the build:**
> - [`docs/ultimate-design.md`](ultimate-design.md) — target design (closed-loop / multi-objective /
>   multi-source / capability-aware; coverage-gap principle; ranking beyond weighted-sum; system-design prep).
> - [`docs/fast-track-learnings.md`](fast-track-learnings.md) — what the throwaway
>   `feat/fast-track-wire-guardrails` branch proved (vocabulary-binding, the H₂O spec-expressiveness root cause).

## Task
Build **Materials-Triage** (public-data-only materials-research triage agent) as single-function TDD
increments, per `Deep-Plan-materials-triage-agent-2026-06-19-1429.md`. The deterministic core, retrieval,
RAG, hypothesis layer, LLM provider, orchestrator, and guardrails are merged. The remaining v1 work is to
**wire it into a working CLI and beat yesterday's fast-track demo on shortlist quality.**

## Plan — path to a "better-than-yesterday" CLI v1
~10 tasks, each a **port-via-TDD** increment (working reference impl on `feat/fast-track-wire-guardrails`).
One function at a time, stop for approval after each.

**Build order (agreed 2026-06-22).** Each `[ ]` is one TDD increment (one function, stop for approval).
Port every increment from the reference impl on `feat/fast-track-wire-guardrails`.

**1 · #39 — Source vocabulary binding** *(foundational: kills "vocab drift → empty results", expands past
today's 6 fields; owns the `FIELD_UNITS`/`_FIELD_ORIGIN` lockstep invariant)*
- [ ] `property_vocabulary()` on the MP adapter — derive queryable name surface from the published API schema
- [ ] grow `FIELD_UNITS` + `_FIELD_ORIGIN` in lockstep with the new surface
- [ ] bind the vocabulary into the hypothesis prompt ("use ONLY these names")

**2 · #38 — Push #37 predicates server-side** (`_query_params` only) *(execution-location optimization, NOT
the H₂O fix — it changes WHERE a predicate runs, not WHETHER the spec carries one. Real justification: MP
applies `_limit=100` BEFORE returning, so pushing the filters the spec already has keeps the 100-row budget
from being spent on rows that `apply_hard_filters` will drop. That stage stays the authority on what survives.)*
- [ ] BooleanConstraint → `_query_params`
- [ ] ElementPredicate (all / any / none) → `_query_params`
- [ ] CountConstraint (cap element count) → `_query_params`

**#38 mechanics (per-predicate, push only what the schema supports — confirm param names against #39):**
- `ElementPredicate all` → `elements=` (AND-membership) — *already done today.*
- `ElementPredicate none` → `exclude_elements=` — pushable.
- `ElementPredicate any` → **no MP OR-membership param** → **stays local** (the deterministic filter honours it).
- `BooleanConstraint` → per-field params (`is_stable`, `is_metal`, …) — push only the booleans the schema exposes as query params.
- `CountConstraint` → `nelements_min` / `nelements_max`.
- **Two invariants:** (1) **`apply_hard_filters` stays the authority** — server-side params are an optimization, every pushed predicate is *also* enforced locally, so a source that can't express one still gets correct results. (2) **MP silently ignores unknown query params** — a wrong/typo'd name returns *unfiltered* rows with no error, so a `live`-marked test must assert the returned rows actually satisfy the constraint (invariant 1 hides the symptom from offline tests). Drive the pushable-param set off #39's published vocabulary; never hand-type a second param table.

**2b · 🆕 Pagination in `retrieve()`** — *sibling to #38; together they = "retrieve the complete filtered candidate set." Not part of #38 (#38 is the pure `_query_params` transform; this is the I/O loop).*
- [ ] page MP's `_skip`/`_limit` to exhaust the (filtered) result set, accumulating candidates
- **Why it's needed, separately from #38:** today `retrieve()` fetches ONE page (`_limit=100`) and stops, so any query matching >100 materials *silently truncates* and the ranker only ever sees an arbitrary 100. #38 fixes *which* 100 (quality of the page); it does **not** fix the cap. Our composite weighted-average rank can't be pushed server-side (MP sorts by a single field), so the ranker must see the complete filtered set locally.
- **Three things it must get right:** (1) **filter-first** — pagination depends on #38 landing, or you page an unfiltered subset (tens of thousands of rows, infeasible); #38 shrinks N to something pageable. (2) **bounded + loud** — page up to a declared ceiling and, if hit, record a caveat in the trace ("result set capped at N; ranking over a subset"); a silent bigger cap is the same bug with a bigger number. (3) **adapter-owned** — `_skip`/`_limit` are MP I/O detail, stay inside `retrieve()`; the `SourceAdapter` contract (`retrieve(spec) -> complete candidate list`) is unchanged.

> **H₂O is not fixed here.** "Metal oxides" must compile to `all={O}` AND `any={metallic elements}` so water
> (has O, no metal) drops at the hard-filter stage. That needs the *set* of metals — **#37 area B
> (element-class constants), currently deferred** — plus the LLM choosing to emit the predicate (#39 + #22).
> It is a spec-expressiveness + LLM-comprehension problem: not #38, and **not** synthesis (synthesis narrates
> the ranked shortlist and may not silently reorder/drop). See the leverage-order note under Discoveries.

**3 · Synthesis & validation primitives** (port from reference)
- [ ] **#20** output validator — every referenced ID + citation must resolve to retrieved provenance
- [ ] **#35** synthesis — grounded narrative + mechanistic "why," each claim cited, no invented numbers
- [ ] **#22** prompt templates — hypothesis + synthesis

**4 · #34 — Wire the orchestrator** *(replace the four PASS-THROUGH nodes)*
- [ ] gate node · [ ] hypothesis node · [ ] synthesis node · [ ] output_validate node

**5 · Renderers → CLI** *(render BOTH views from ONE `TriageRun`)*
- [ ] **#25** PI renderer (`view=pi`) · [ ] **#26** audit renderer (`view=audit`)
- [ ] **#27** CLI — `triage "<q>" --view pi|audit`; `resume`

**6 · Capstone / polish**
- [ ] **#41** notebooks refresh
- [ ] **#40** RAG→synthesis (citations + caveats + ordering-fidelity) — unblocks after #35 + #20

**Out of scope for the CLI:** #28 (eval), #29/#30/#36 (docs), #31 (drop reasons), all HB* hosting tasks,
#37 area B (element-class constants) + area C (ranking) — both **deferred** (low priority).

## What's merged on `main` (the foundation to build on)
- **Core:** `core/schema.py` (all frozen models; `Provenance` now carries required `method` + optional
  `xc_functional`), `core/elements.py`, `core/scoring.py` (`apply_hard_filters`/`on_missing`),
  `core/ranking.py` (weighted-average), `core/hypothesis.py` (models + `compile_spec`, `kind`-discriminated
  union). #37 spec expressiveness areas **A** (BooleanConstraint / ElementPredicate all·any·none /
  CountConstraint) + **D** (trust metadata) shipped.
- **Retrieval:** `sources/base.py` + `stubs.py` + `materials_project.py` — MP adapter does a **two-call**
  retrieve (summary → batched `/materials/tasks/` for per-property `xc_functional`). `retrieval/rag.py`
  BM25 literature RAG (abstract-only).
- **Agent:** `agent/llm.py` Bedrock `HypothesisProvider` (injected `complete` seam; `us.*` inference-profile
  model id). `agent/prompts.py` `ROLE_SYSTEM_PROMPT` + `build_chat_messages`. `agent/orchestrator.py`
  LangGraph graph with per-stage exclusion channels + retry + spec-build HITL gate; `core/run_trace.py`
  audit export + `memory/store.py` `BaseStore`. **⚠️ gate / hypothesis / synthesis / output_validate nodes
  are still PASS-THROUGHS** — wiring them is #34.
- **Guardrails:** `policy/guardrails.py` `check_input` + `wrap_untrusted` + `_scrub`.
- **Server (net-new, outside v1 deep-plan):** `server/mt_server/policy.py` `resolve_model` (#52); `server/`
  wired into pytest. See the hosting task list below.
- **Docs/ADRs:** 0001 retrieval · 0002 RAG abstracts-only · 0003 orchestrator-on-LangGraph · 0004 guardrail
  threat-model · 0005 hosting & step-cache.
- **Reference impl (NOT on `main`):** `feat/fast-track-wire-guardrails` (local + origin) — full end-to-end
  wiring + `core/synthesis.py`, `agent/validator.py`, `render.py`, `cli.py`, `property_vocabulary()`, VRH
  `_scalar` collapse. Port from it.

## Status
- **`main` @ `1745fe6`**, clean. 207 tests pass (3 `live` deselected).
- **#37 done** (A #51 + D #55; B + C deferred). This unblocked **#38/#39/#41**.
- **No parallel sessions in flight.** `feat/value-trust-metadata` merged (#55) + pruned.
- **Known issues / loose ends:**
  - Live Bedrock smoke test ~15% flaky by design (mitigated by the #45 retry loop).
  - Obsolete `.git/info/exclude` line 9 (ADR-0005 path, now tracked) — safe to delete.
  - `mt-value-trust` worktree + `backup-D-20260622` tag still around — can be removed.
  - `stash@{0}` (readme-kid-flowchart WIP) unmerged — drop if unwanted.

## Hosting build task list (ADR 0005) — net-new, sequence vs. v1 per Kian
Harness tasks #1–#10, labeled **HB1–HB10** to avoid colliding with the v1 numbers.

| HB | Task | Status | Blocked by |
|----|------|--------|-----------|
| HB1 | Scaffold monorepo `server/` + `[server]` extra | partial — pytest wiring done (#52); FastAPI extra pending | — |
| HB2 | Backend run API + SSE step stream | pending | HB1 |
| HB3 | HITL spec gate over HTTP (resume) | pending | HB2 |
| HB4 | Auth tiers + rate limit + model policy + metering | in progress — `resolve_model` done (#52); tier/rate-limit/metering pending | HB1 |
| HB5 | Durable shared checkpointer (replace `MemorySaver`) | pending | — |
| HB6 | `source_version` on source adapters | pending | — |
| HB7 | Content-addressed step cache (key + recursive inputs) — *lives in core* | pending | HB6 |
| HB8 | Force-fresh toggle + idempotency short-circuit | pending | HB7 |
| HB9 | Thread/attempt storage + cross-attempt diff | pending | HB7, HB2 |
| HB10 | Frontend `web/` chat UI + steps banner | pending | HB2 |

Ready now: **HB4** (tier/rate-limit/metering), **HB5**, **HB6**, HB1's remaining `[server]` extra.

## Discoveries that still bite (curated; full list in archive + MEMORY.md)
- **Quality ceiling = spec-schema expressiveness, NOT the LLM/prompt.** Leverage order: schema (#37) >
  server-side filters (#38) > prompt (#22). H₂O survived three prompt revisions because the schema couldn't
  express "must contain a metal" — not because the filter ran in the wrong place. So the fix is **#37 area B
  (element-class constants, deferred)** + the LLM emitting `any={metals}` (#39/#22), NOT #38 and NOT synthesis.
  #38 only changes *where* an existing filter runs (the `_limit=100`-before-return truncation), never *whether*
  one exists. [memory: spec-expressiveness-quality-ceiling]
- **Vocabulary binding (#39):** the *adapter*, not the LLM, owns the queryable name surface; publish it via
  `property_vocabulary()` and feed the prompt "use ONLY these names," or you get silent empty results.
- **Only the live end-to-end run finds integration bugs** unit tests miss (vocab drift, PI/audit view
  mismatch, VRH-dict crash). Render BOTH views from ONE `TriageRun`.
- **Encode LLM contracts in the JSON schema, not hidden `model_validator`s** (discriminated union). LLM
  structured output is ~15% schema-flaky → the orchestrator retry loop is load-bearing. [memory: llm-structured-output-flakiness]
- **MP API:** sandboxed mirror, `X_API_KEY` header; query-id ≠ returned-id; units not in payload (pinned in
  adapter); functional lives in the task doc, varies per-property. [memory: materials-project-api, dft-xc-functional-comparability-v2]
- **no-mistakes:** bootstrap from the **main repo** (not a worktree); `git push no-mistakes <branch>` → `axi
  abort` → `axi run --intent`; verify locally with `PYTHONPATH="$PWD/src" pytest`. [memory: no-mistakes-run-bootstrap]

## Context for Next Session
- **Branch:** `main` @ `1745fe6`, clean. Build the next increment in a worktree (run pytest with
  `PYTHONPATH="$PWD/src" pytest`). Port from `feat/fast-track-wire-guardrails`.
- **Verify merged state:** `python -m pytest -q` (207 pass, 3 deselected); `ruff check .`. Live (needs
  creds): `pytest -m live`.
- **Credentials:** `X_API_KEY` (MP), `OPENALEX_MAILTO` (optional polite pool), AWS creds for Bedrock
  (prefer `~/.aws/credentials`; conftest `load_dotenv`s `.env`; AWS keys in `.env` must be UPPERCASE).
  **Never read/print the AWS creds or `X_API_KEY`.**
- **Git workflow:** `main` protected, signed commits (`git commit -S`), squash-merge via GitHub UI, then
  `/sync-main`. Ship via `/commit-commands:commit-push-pr` or no-mistakes. pre-commit `ruff format` can
  abort a commit → re-add + re-commit.
- **Collaboration rules (CLAUDE.md):** ask before choosing between approaches; one function at a time then
  stop for approval; TDD via the `tdd` skill (never batch tests); **don't start coding until told.**
- **Task tracker:** v1 path = #39, #38, **pagination (untracked sibling of #38)**, #20, #35, #22, #34, #25,
  #26, #27, #41, #40 (see Plan). Completed: #1–#19, #21, #23, #24, #32, #33, #37. Deferred: #37 area B/C
  (note: area B is now on the critical path for the "metal oxides" query). Hosting = HB1–HB10.
