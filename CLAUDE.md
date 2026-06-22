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
inclusive min/max bound on one numeric property), `BooleanConstraint` (the source-neutral
hard filter for yes/no facts like `is_stable`/`is_metal`, asserting one property's
`required` truth value), `ElementPredicate` (a hard composition filter unifying the old
require/exclude into one OPTIMADE-shaped quantified membership test — `all`/`any`/`none`
over a symbol-validated member set, adding the `any` = has-any operator require/exclude
could not express), `CountConstraint` (an inclusive `min`/`max` bound on the number of
distinct elements in a composition, replacing the scalar `max_nelements` with a typed
slot so the "cannot require more distinct elements than the cap" cross-check stays a
robust typed invariant), `RankingTarget` (a soft scoring preference
whose `weight` is a proportional share in `(0, 1]`), and `TriageSpec` (the fully-resolved
request bundling numeric/boolean constraints, ranking targets, element predicates, and
an optional `count` cardinality bound) models in
`src/materials_triage/core/schema.py` exist so far, alongside the canonical 118-symbol
`ELEMENT_SYMBOLS` frozenset in `src/materials_triage/core/elements.py`. The
hypothesis layer in `src/materials_triage/core/hypothesis.py` also exists — the
frozen `Citation` (the untrusted-DATA analog of `Provenance`), `Proposal` (one cited
bridge, a `kind`-discriminated union of `ConstraintProposal`/`BooleanConstraintProposal`/
`CountConstraintProposal`/`RankingProposal`/`ElementPredicateProposal`
subclasses with `extra="forbid"`, so the kind→payload requirement lives in the JSON
schema the LLM is handed rather than a hidden validator and structured output emits
the right payload — each proposal carrying the matching core predicate as its payload,
so the hypothesis layer no longer defines its own `ElementRule`),
and `Hypothesis` (the LLM's whole emission: `proposals` + `mechanism`) models,
plus the pure `compile_spec(proposals) -> TriageSpec` seam that dispatches on
`kind`, collecting numeric/boolean constraints and element predicates, taking the first
`count_constraint` as the spec's cardinality bound, and normalizing ranking
weights to sum to 1. The literature RAG in `src/materials_triage/retrieval/rag.py`
also exists — the frozen `LiteraturePassage` model (an OpenAlex abstract bound to
its `Provenance`, kept and flagged `missing` rather than dropped when absent),
`_reconstruct_abstract` (rebuilds OpenAlex's `abstract_inverted_index` into ordered
text), `_parse_work` (one OpenAlex work → passage), a formula-aware `_tokenize`
that keeps chemical formulas/decimals intact, BM25 `_rank` over title+abstract
(ties keep input order), and the public `LiteratureRAG.search(query, k)` wiring
fetch→parse→rank→top-k behind an injected `AbstractFetcher` Protocol whose live
transport is `OpenAlexFetcher` (lazy `requests` import, `live` pytest marker). Its
abstract-only, no-full-text grounding is recorded in
[`docs/design/0002-literature-abstracts-only.md`](docs/design/0002-literature-abstracts-only.md),
and it adds `rank-bm25` as a runtime dependency. The Bedrock-backed hypothesis
provider in `src/materials_triage/agent/llm.py` also exists — `HypothesisProvider`
turns a rendered prompt into a validated `Hypothesis` via an injected `complete`
seam (`Complete = Callable[[str], Hypothesis]`), mirroring the Materials Project
adapter's injected-transport pattern: tests pass a fake so the provider is fully
offline-testable, while the lazy default `_bedrock_complete` wraps
`langchain_aws` `ChatBedrockConverse.with_structured_output(Hypothesis)` and
imports `langchain_aws` only on invocation, so construction needs neither the
dependency nor AWS credentials. `propose(prompt)` forwards the prompt verbatim
(no wrapping/mutation, preserving the untrusted-data boundary); malformed
structured output is correctly rejected by the schema, with reliable conformance
left to the orchestrator's retry loop. It adds an optional `llm` extra
(`langchain-aws`); the live Bedrock smoke test is `live`-marked (deselected from
CI) and gated on botocore-resolvable AWS credentials. A `tests/conftest.py`
`load_dotenv()`s at collection time (defensive optional import) so live tests read
credentials from `.env` before the `skipif` gates evaluate, with `python-dotenv`
added to the dev extra. The orchestrator skeleton in
`src/materials_triage/agent/orchestrator.py` also exists — per ADR 0003
([`docs/design/0003-orchestrator-on-langgraph.md`](docs/design/0003-orchestrator-on-langgraph.md))
the nine `WORKFLOW_STEPS` (`gate`, `spec_build`, `hypothesis`, `retrieve`,
`filter`, `rank`, `synthesis`, `output_validate`, `render`) are compiled by
`build_orchestrator(adapter=None, checkpointer=None)` into a LangGraph
`StateGraph` wired in a fixed linear edge order (START → gate → … → render →
END — a static pipeline, not an autonomous tool-calling loop) and backed by an
in-process `MemorySaver` checkpointer (the substrate for the #9 trace export and
`resume --from`). `OrchestratorState` is a `total=False` `TypedDict` with one
typed channel per step output (`goal`, `run_id`, `spec`, `hypothesis`,
`candidates`, `survivors`, `filter_excluded`, `rank_excluded`, `result`), holding
the rich domain objects (provenance, missing-data flags, exclusion reasons,
citations) so the checkpointer round-trips them losslessly for the audit export;
validation stays in the domain models, not on every channel write. Exclusions are
split by stage into two single-writer channels — the `filter` node writes
`filter_excluded` (hard-filter drops) and the `rank` node writes `rank_excluded`
(the ranker's `on_missing="exclude"` missing-policy drops) — so neither channel
undercounts and no node reads-then-writes the same channel (avoiding a resume
double-fold); the slice-6 audit exporter derives from these per-stage channels per
ADR 0003, which lets the audit view label drops by stage. Only the `retrieve` →
`filter` → `rank` nodes carry real logic — they wrap the existing pure
`adapter.retrieve` / `apply_hard_filters` / `rank` behind the injected
`SourceAdapter` seam (a fake makes the whole graph offline-testable, no LLM), and
the `rank` node sets `result.excluded` to the union of both stages — the complete
presentation set the renderers read, every exclusion carrying its reason. The other six steps
are intentional pass-throughs in this slice (real logic — the retry node and the
`interrupt()` spec gate — lands in later slices/tasks). It adds `langgraph>=0.2`
as a runtime dependency. It proceeds as single-function
TDD increments (see the build order in the deep plan), and only on an explicit
go-ahead.

The **public-web-app hosting layer** has also begun in a sibling `server/`
package (monorepo layout: the core stays pure under `src/materials_triage` and
`server/` imports it, never the reverse). Its first increment is the
model-selection policy in `server/mt_server/policy.py` — the pure, deterministic
`resolve_model(tier, requested, *, default, allowed)` (no FastAPI, no AWS, so the
web layer can lean on it and it is offline-testable): an `anon` visitor is pinned
to `default` (any `requested` model is silently ignored — not an error — because
the UI greys the selector out, keeping the shared Bedrock account bill
predictable); a signed-in `user` gets their `requested` model when it is in
`allowed`, the `default` when none is requested, and a `ValueError` when the
model is not offered; an unknown `tier` raises `ValueError`. `allowed`/`default`
are parameters (the server config owns the model list), and `TIERS` is the
recognized-tier frozenset. `server/` is wired into pytest discovery without a
separate install — `pyproject.toml` adds `server/tests` to `testpaths` and
`server` to `pythonpath` while the core stays installed editable from `src`.
FastAPI routes, auth/tier resolution, rate limiting, and metering are separate
follow-up increments.

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
