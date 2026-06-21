# Session Handoff - 2026-06-21 13:24

> Single living handoff, git-tracked at `docs/handoff.md` (PR #35). Do NOT recreate dated copies or
> a `docs/handoffs/` subdir — untracked handoffs get wiped by parallel sessions' `git clean`. Keep
> this file committed.

## Task
Build **Materials-Triage** (public-data-only materials-research triage agent) as single-function
TDD increments, per `Deep-Plan-materials-triage-agent-2026-06-19-1429.md`. Core data models,
deterministic logic (scoring + ranking), retrieval (MP adapter), the literature RAG (#17), the
hypothesis layer, **and the first real LLM code — the Bedrock provider (#21) — are all complete and
merged to `main`.** The project is mid-way through the **LLM layer** (#21 done · #22 prompts · #23
orchestrator next) with **guardrails** (#18 input gate / #19 trust boundary / #20 output validator)
still ahead.

## Scope
- **DONE + merged:** data models (schema.py), logic (scoring.py + ranking.py), retrieval
  (SourceAdapter + Materials Project adapter), **literature RAG (#17)**, **hypothesis layer
  (models + `compile_spec`, now a `kind`-discriminated union)**, **Bedrock LLM provider (#21)**,
  demo notebooks + ADRs 0001/0002.
- **Next up:** prompts (#22) → orchestrator (#23, LangGraph); guardrails (#18–#20); then
  renderers (#25/#26), CLI (#27), eval (#28), design note (#29-doc), README (#30-doc).
- **PARKED behind a LangGraph decision:** #9 TriageRun/Step trace + #10 lab memory store —
  revisit before #23 (LangGraph checkpointer ⊇ #9 trace+resume; BaseStore ↦ #10 lab memory).
- **Collaboration rules (CLAUDE.md — follow exactly):** ask before choosing between approaches;
  implement ONE function at a time then stop for approval; TDD via the `tdd` skill (one red→green
  at a time, never batch); discuss behavior before coding; **don't start coding until told**.

## Files
- `src/materials_triage/core/schema.py` — all frozen data models (merged).
- `src/materials_triage/core/elements.py` — 118 IUPAC element symbols (merged).
- `src/materials_triage/core/scoring.py` — `normalize` + `apply_hard_filters` + `on_missing` (merged).
- `src/materials_triage/core/ranking.py` — weighted-average ranker (merged).
- `src/materials_triage/core/hypothesis.py` — hypothesis models + `compile_spec` seam (merged #30),
  **now a `kind`-discriminated union** (`ConstraintProposal`/`RankingProposal`/`ElementRuleProposal`
  behind `Field(discriminator="kind")`, `extra="forbid"`; merged #34). `Proposal` is a type alias —
  construct the concrete subclass. `compile_spec` unchanged (dispatches on `.kind`).
- `src/materials_triage/sources/base.py` / `stubs.py` / `materials_project.py` — retrieval (merged).
- `src/materials_triage/retrieval/rag.py` — **literature RAG (#17), MERGED (#31)**. Surface:
  `LiteraturePassage`, `LiteratureRAG.search(query, k=10)`, `AbstractFetcher` (Protocol),
  `OpenAlexFetcher` (live transport). Internals: `_reconstruct_abstract`, `_parse_work`,
  `_tokenize`, `_rank`.
- `src/materials_triage/agent/llm.py` — **Bedrock `HypothesisProvider` (#21), MERGED (#36).**
  `propose(prompt) -> Hypothesis` via an injected `complete` seam (offline-testable like the MP
  adapter's `http_get`); lazy default wraps `ChatBedrockConverse.with_structured_output(Hypothesis)`,
  importing `langchain_aws` only on invocation. `DEFAULT_MODEL_ID` is the `us.*` inference-profile id
  (on-demand requires it).
- `tests/test_llm.py` — provider tests B1–B4 (merged #36): tracer, prompt-verbatim spy, lazy-construct
  guard, and a `live` Bedrock smoke test gated on botocore-resolvable creds, **documented ~15% flaky**.
- `tests/conftest.py` — `load_dotenv()` at collection time (defensive import) so live tests read
  creds from `.env` before `skipif` gates evaluate (merged #36).
- `tests/test_hypothesis.py` — migrated to subclass construction + schema regression test (merged #34).
- `tests/test_rag.py` — RAG tests (merged #31); `omits mailto when unset` made hermetic via
  `monkeypatch.delenv` (merged #36).
- `pyproject.toml` — runtime deps `pydantic>=2`, `requests>=2`, `rank-bm25>=0.2`; extras `dev`
  (+`python-dotenv`), `notebook`, **`llm` (`langchain-aws`)**; `live` pytest marker (deselected).
- `docs/design/0001-retrieval-rest-adapters.md` (#29) · `0002-literature-abstracts-only.md` (#31).
- `notebooks/deterministic_pipeline_demo.ipynb` (#29) · `literature_rag_demo.ipynb` (#32/#33).
- `docs/handoff.md` — this file (tracked, #35).

## Discoveries (gotchas — most also in auto-memory; see MEMORY.md)
*(Append-only. Prior findings preserved; newest at the end.)*
- **TDD discipline:** thin frozen models; behavior lives in functions. Some happy-path tests are
  characterization (green-on-arrival) — flag them honestly.
- **Design split ("Way B"):** `Criterion` → `Constraint` (hard min/max) + `RankingTarget` (soft
  direction/weight/on_missing). Weights are **proportional, sum to 1**.
- **`identifier` vs `record_id`:** Candidate.identifier = source-returned identity; Provenance.record_id
  = per-value receipt. Coincide in single-source v1.
- **Materials Project API** is a sandboxed mirror: `X_API_KEY` header; query-id ≠ returned-id; units
  not in payload (pinned per-field in the adapter); `origins[]` = per-property provenance. [memory]
- **Retrieval = thin REST adapters** (ADR 0001), injected `http_get`, lazy `requests`, `live` marker.
- **Cloudflare bans `Python-urllib` UA (403);** `requests`/browser UA pass. Change UA, don't retry.
- **Hypothesis-layer decision RESOLVED → MEDIUM** (cited spec-deltas bridging fuzzy goal → DB proxies);
  deterministic core ≈ MP-API by design; Rich is v2. [memory: hypothesis-layer-open-decision]
- **LangGraph note:** checkpointer ⊇ #9 trace + resume; BaseStore ↦ #10 lab memory. Decide before #23.
- **pre-commit `ruff format` can ABORT a commit** by reformatting staged files — re-`git add`, re-commit.
- **Literature RAG (#17, PR #31):** abstract-only (ADR 0002); MP = fact layer, RAG grounds *direction*
  + *cited claims*. Keep+flag missing abstracts (rank on title), drop only works empty in BOTH title
  and abstract, keep zero-relevance hits stable, score stamped by `_rank` on frozen copies.
- **OpenAlex specifics:** abstracts as `abstract_inverted_index`; ~20–40% null abstracts; `id`/`doi`
  are URLs (strip prefixes); keyless, set `mailto` for the polite pool. Endpoint `/works?search=&…`.
- **Formula-aware tokenizer + v2 debt** (`_tokenize` keeps TiO2 / La0.6Sr0.4CoO3 intact). v2: case-fold,
  synonymy, sub-formula, stemming; unicode-minus formulas don't survive. [memory: rag-tokenizer-v2-todo]
- **BM25 small-corpus gotcha:** N=2 docs → IDF=0 → all-zero scores; ranking tests need ≥3 docs.
- **DFT/XC-functional comparability (V2):** MP values are functional-dependent, not cross-functional
  rankable; v2 tag functional in provenance + flag. [memory: dft-xc-functional-comparability-v2]
- **no-mistakes bootstrap:** `axi run` can't START a fresh run (maps to `rerun`). Recovery that WORKED
  from the **main repo**: `git push no-mistakes <branch>` fires the post-receive hook → creates a run
  (auto-started, no intent) → `axi abort` → `axi run --intent "…"`. From a worktree the hook misfires
  (`invalid gate path: .`); run from the main repo. The gate refuses ANY uncommitted/untracked file —
  stash untracked `docs/` first. [memory: no-mistakes-run-bootstrap]
- **— Session 6 (2026-06-21, this session): LLM provider #21 —**
- **Structured output ≠ truth, only form.** Pydantic validation conforms the LLM's *shape*; truth is
  enforced by citations + the human gate + the deterministic pipeline + output validator (#20).
- **The "Medium" payoff is unproven until #22/#23/#17 produce genuinely-cited proxy-bridges.** The
  deterministic core ≈ an MP-API query+sort by design, so the agent's value lives in the hypothesis
  layer; clean plumbing alone risks "Medium collapsing back toward Thin."
- **model_validator rules are INVISIBLE to the JSON schema the LLM receives.** The original
  flat-`Proposal` + `_payload_matches_kind` validator let Bedrock emit `kind="ranking_target"` with no
  payload (legal per schema). Fix = **discriminated union** (#34): the `oneOf` per-branch-required
  payload is now IN the schema, so structured output conforms. **Lesson: encode LLM contracts in the
  schema, not in hidden validators.**
- **LLM structured-output flakiness MEASURED (#21 stress test).** 20 live Bedrock calls across 10
  subagents: **17/20 pass, 3 fail — all SCHEMA-class, 0 infra/throttling.** Three *distinct*
  malformations each caught by a *different* validator: (a) `proposals` returned as a JSON **string**,
  (b) ElementRule payload **flattened** to the proposal top level, (c) Constraint with **both bounds
  null**. ⇒ a generic **retry-on-ValidationError loop (#23) is load-bearing, not optional** (~15%
  trigger rate); per-field coercion can't cover all modes. The gate correctly rejecting these IS the
  safety property. [memory: llm-structured-output-flakiness]
- **Bedrock model id needs the `us.*` inference-profile form** — the bare `anthropic.claude-…` id
  raises `ValidationException: on-demand throughput isn't supported`.
- **Credential detection for the live test:** gate on `botocore.session.Session().get_credentials()`
  (defensive import), which resolves env vars / a profile / `~/.aws/credentials` without reading the
  secret — better than checking env-var names (creds from `~/.aws/credentials` never hit `os.environ`).
- **`load_dotenv()` does NOT load `~/.aws/credentials`** (INI read by boto3, not a `.env`). conftest
  loads `.env` at collection time so `skipif` sees vars; AWS keys in `.env` must use UPPERCASE names.

## Work Done (all merged to `main` unless noted)
*(Append-only history.)*
- **Sessions 1–4 (≤2026-06-20):** Provenance/PropertyValue/Candidate/Constraint/RankingTarget/
  TriageSpec/TriageResult models; `normalize`; `apply_hard_filters`+`missing_data`; `on_missing`;
  weighted-average ranker; SourceAdapter+stubs; Materials Project adapter + composition scoping;
  pre-commit ruff hook. Literature RAG (#17) DESIGNED. MP demo notebook + ADR 0001 built.
- **Session 5 (2026-06-21) — literature RAG #17 built, shipped, demoed:** TDD in a worktree
  (`_reconstruct_abstract` → `LiteraturePassage`+`_parse_work` → `_rank`+tokenizer →
  `LiteratureRAG.search`+`AbstractFetcher` → live `OpenAlexFetcher`); added `rank-bm25`; ADR 0002.
  Validated via no-mistakes (review caught the null-title batch-crash bug → fixed). Merged: **#31**
  (RAG), **#29** (MP demo + ADR 0001), **#32** (RAG demo notebook), **#33** (notebook cleanup).
  Memories saved: rag-tokenizer-v2-todo, dft-xc-functional-comparability-v2.
- **Session 6 (2026-06-21) — hypothesis layer + LLM provider #21:**
  - Settled hypothesis-layer richness = **MEDIUM** (Rich = v2); built the hypothesis models +
    `compile_spec` (merged earlier as **#30**).
  - Chose the LLM-provider design: injected `complete` seam (Option A, mirrors `http_get`); lazy
    `ChatBedrockConverse.with_structured_output`. Built `agent/llm.py` via TDD (B1 tracer → B2 spy →
    B3 lazy guard → B4 live).
  - Live Bedrock run exposed that `model_validator` rules don't reach the LLM schema → refactored
    `Proposal` into a **discriminated union** (`extra="forbid"`); migrated tests + added a schema
    regression test.
  - Added `tests/conftest.py` (`load_dotenv`), the `llm` extra (`langchain-aws`), `python-dotenv` in
    `dev`, a botocore credential gate, and a hermetic `test_rag.py` fix.
  - **20-run robustness stress test** (10 subagents) → ~15% schema-flakiness finding; documented the
    live test as flaky and recorded [memory: llm-structured-output-flakiness].
  - Shipped **split** PRs via no-mistakes: **#34** (discriminated union) and **#36** (provider #21) —
    both 0 findings, CI green, merged. Pruned `docs/handoffs/` (kept this tracked `docs/handoff.md`).

## Status
- **Working / merged:** `main` @ **`cb26345`**. Full deterministic vertical slice + literature RAG +
  hypothesis layer + **the Bedrock LLM provider (#21)** are live. Offline suite green
  (`python -m pytest -q` → 138 passed, 3 live deselected); `ruff check .` clean. Live edges all green
  by hand (`pytest -m live`): Bedrock, OpenAlex, Materials Project. Tree clean, no branches/worktrees.
- **Known issues:** none for merged work. The **live Bedrock smoke test is ~15% flaky by design**
  (single-shot; gate rejects malformed LLM output) — deselected from CI and no-mistakes, so it gates
  nothing; reliability awaits the #23 retry loop.
- **Open threads:** LangGraph decision (gates #9/#10/#23); two #23 carry-forward debts
  (weight-normalization confirmation gate; wrap `compile_spec` ValidationError for retry/human)
  [memory: orchestrator-23-carryforward]; v2 debts (tokenizer, DFT/XC); #31 ExcludedCandidate reasons.

## Next Steps
1. **Decide what to pick up next** (don't start coding until told). Natural path: **#22 prompts**
   (convert/recommend/hypothesis/synthesis templates feeding the provider) → **#23 orchestrator**.
   Alternative: guardrails **#18 input gate → #19 trust boundary → #20 output validator**.
2. **Before #23:** settle the **LangGraph** decision (checkpointer ↦ #9 trace+resume; BaseStore ↦ #10
   lab memory) and build the **structured-output retry loop** — load-bearing per the ~15% finding —
   plus the two carry-forward gate/error-wrap debts.
3. **When coding:** branch off `main` (worktree if parallel sessions), `python -m pytest -q`
   (+ `-m live` with creds for Bedrock/OpenAlex/MP), `ruff check src tests`. Ship via no-mistakes
   (bootstrap: push to `no-mistakes` remote → abort → `axi run --intent`); squash-merge in the GitHub
   UI; then `/sync-main`. Keep this handoff committed.
4. **Eventually:** ExcludedCandidate element/composition reasons (#31); v2 items (tokenizer, DFT/XC);
   renderers (#25/#26), CLI (#27), eval (#28), design note (#29-doc), README (#30-doc).

## Context for Next Session
- **Branch:** `main` @ `cb26345` == `origin/main`. Clean tree, no other local branches or worktrees.
- **How to verify merged state:** `python -m pytest -q` (138 passed, 3 deselected), `ruff check .`.
  Live (needs creds): `pytest -m live` (Bedrock via `~/.aws/credentials`, OpenAlex, MP). RAG quick
  check: `OPENALEX_MAILTO=… python -c "from materials_triage.retrieval.rag import LiteratureRAG,
  OpenAlexFetcher; print(len(LiteratureRAG(OpenAlexFetcher()).search('perovskite oxygen evolution', k=5)))"`.
- **Credentials:** `X_API_KEY` (MP sandbox); `OPENALEX_MAILTO` optional (polite pool); AWS creds for
  Bedrock (#21+) — prefer `~/.aws/credentials` (botocore auto-detects; `load_dotenv` won't load it).
  conftest loads `.env` for live tests; AWS keys in `.env` must be UPPERCASE.
- **Git workflow:** `main` protected, signed commits (`git commit -S`, SSH), squash-merge via GitHub
  UI, then `/sync-main`. pre-commit `ruff format` can abort a commit → re-add + re-commit.
- **Auto-memory (persists):** see `MEMORY.md` — incl. hypothesis-layer (RESOLVED→MEDIUM),
  ranking-weight-normalization, orchestrator-23-carryforward, llm-structured-output-flakiness,
  dft-xc-functional-comparability-v2, rag-tokenizer-v2-todo, adapter-testing-seam, materials-project-api,
  handoff-doc-location, worktree-pythonpath, no-mistakes-run-bootstrap.
- **Task list:** #1–#17 + #21 done (#34/#36 merged this session); #9/#10 parked; #18–#20, #22–#30
  remain; #31 (element/composition drop reasons) deferred.
