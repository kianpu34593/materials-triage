# Session Handoff - 2026-06-21 18:15

> Single living handoff, git-tracked at `docs/handoff.md` (PR #35). Do NOT recreate dated copies or
> a `docs/handoffs/` subdir — untracked handoffs get wiped by parallel sessions' `git clean`. Keep
> this file committed.

> **Design & learnings (read these first if picking up the real build):**
> - [`docs/ultimate-design.md`](ultimate-design.md) — **target design for the real build**: closed-loop /
>   multi-objective / multi-source / capability-aware; the DB-is-not-the-world coverage-gap principle;
>   ranking beyond weighted-sum; cross-source merge (doc-only ladder); system-design prep
>   (cost/latency/caching); and the take-home build-vs-articulate prioritization.
> - [`docs/fast-track-learnings.md`](fast-track-learnings.md) — what the throwaway fast-track branch
>   (`feat/fast-track-wire-guardrails`) proved: vocabulary-binding, prompt-fidelity before/after, the
>   synthesis/RAG gap, and the H₂O spec-expressiveness root cause.

## Task
Build **Materials-Triage** (public-data-only materials-research triage agent) as single-function
TDD increments, per `Deep-Plan-materials-triage-agent-2026-06-19-1429.md`. Core data models,
deterministic logic (scoring + ranking), retrieval (MP adapter), the literature RAG (#17), the
hypothesis layer, the Bedrock provider (#21), **and now the LangGraph orchestrator (#23, incl. the
hypothesis retry + spec-build HITL gate) are all complete and merged to `main`** (built by a parallel
session). This session pivoted to the **input-side guardrails** — the input policy gate (#18) and
trust boundary (#19) — built in a worktree. Remaining: output validator (#20), prompts (#22, folding
into role system prompt), renderers/CLI/eval/docs.

## Scope
- **DONE + merged:** data models (schema.py), logic (scoring.py + ranking.py), retrieval
  (SourceAdapter + Materials Project adapter), **literature RAG (#17)**, **hypothesis layer
  (models + `compile_spec`, now a `kind`-discriminated union)**, **Bedrock LLM provider (#21)**,
  demo notebooks + ADRs 0001/0002.
- **Orchestrator #23 on LangGraph — BUILT + MERGED** (parallel session): LangGraph skeleton with
  per-stage exclusion channels (#41), then hypothesis retry + spec-build HITL gate (#23). #9/#10 are
  subsumed (checkpointer ⊇ #9 trace+resume; `BaseStore` ⊇ #10 lab memory; `interrupt()` = spec HITL);
  `runs/<id>.json` is a **derived read-model exported from checkpoint history**, not a second store.
  [memory: langgraph-orchestrator-decision]
- **THIS SESSION (8): input-side guardrails.** Building the **input policy gate (#18)** + **trust
  boundary (#19)** in the `feat/input-policy-gate` worktree. Gate is DETERMINISTIC + **allowlist-first
  scope triage**, right-sized as the **weakest of 5 layers** (NOT the safety guarantee). v1 leans on a
  thin forbidden-action denylist + a **role system prompt** for scope/role; v2 = hybrid LLM scope
  check. [memory: input-gate-mechanism-decision] · ADR 0004 (+expansion) records the threat model.
- **Next up:** finish #18/#19 slices (trust-boundary `wrap_untrusted` + `ROLE_SYSTEM_PROMPT`), then
  output validator (#20), renderers (#25/#26), CLI (#27), eval (#28), design note (#29-doc),
  README (#30-doc).
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
- `src/materials_triage/agent/orchestrator.py` — **LangGraph orchestrator (#23), MERGED** (#41 skeleton
  + per-stage exclusion channels; #23 hypothesis retry + spec-build HITL gate). Parallel session's work.
- `src/materials_triage/policy/guardrails.py` — **input policy gate (#18), WIP on `feat/input-policy-gate`
  (uncommitted).** `check_input(text) -> GateDecision`; frozen `GateDecision(allowed, reason, category)`;
  deterministic forbidden-action denylist `_FORBIDDEN_ACTIONS` (categories `wet_lab`/`private_data`/
  `paywalled`). Still to add: `wrap_untrusted` trust-boundary wrapper (#19), `ROLE_SYSTEM_PROMPT`.
- `tests/test_guardrails.py` — gate tests (WIP, uncommitted): in-scope allowed + 3 forbidden-action
  refusals (= persistent deterministic red-team cases). 4 passing.
- `docs/design/0003-orchestrator-on-langgraph.md` (ADR, #40) · `0004-guardrail-architecture-threat-model.md`
  (ADR, #42; **expanded** with wrapper construction + attack-surface table, #43).
- `docs/design/0001-retrieval-rest-adapters.md` (#29) · `0002-literature-abstracts-only.md` (#31).
- `notebooks/deterministic_pipeline_demo.ipynb` (#29) · `literature_rag_demo.ipynb` (#32/#33) ·
  one-shot **LLM→spec** and **RAG→spec** demo notebooks (#38, parallel session).
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
- **— Session 7 (2026-06-21, this session): LangGraph orchestrator decision —**
- **LangGraph-vs-hand-roll RESOLVED → LangGraph**, because Kian confirmed **real `resume --from` +
  human-in-the-loop (spec gate) are in v1 scope** — those ARE LangGraph's checkpointer + `interrupt()`,
  so hand-rolling them is reinventing tested framework storage/resume. [memory: langgraph-orchestrator-decision]
- **The reframing that settled it (Kian's):** checkpointer and `runs/<id>.json` are *two different
  jobs*, not competitors. Checkpointer = **live execution state** (resume/recovery, framework-owned,
  transient); `runs/<id>.json` = **durable audit report** (what `view=audit` renders, OURS) — a
  **read-only export from `get_state_history()`**, ONE write path + one derived read model, NOT two stores.
- **This dissolves the earlier "opaque checkpoint" worry:** the durable artifact is our exported JSON
  (our schema); the checkpoint DB is ephemeral; and because the graph is **linear**
  (gate→spec→hypothesis→retrieve→filter→rank→synth→validate→render) the super-step↔named-step mapping
  is ~1:1, so the exporter is thin (`.values` / `.metadata.writes` → `TriageRun`/`Step`).
- **Not free — design discipline:** the checkpointer only persists what's routed through **typed graph
  state**, so make graph state = our domain pydantic state (one channel per step) or the audit export
  loses provenance/excluded-set/missing-flags. Revises deep plan: `core/run_trace.py` → exporter,
  `memory/store.py` → `BaseStore` wrapper. Node `RetryPolicy` ↦ the ~15% flakiness loop, but likely a
  **custom node** to retry ONLY on pydantic `ValidationError` and feed the malformed output back.
- **— Session 8 (2026-06-21, this session): input-side guardrails / threat model —**
- **A keyword denylist is NOT how guardrails work.** Frontier safety = defense-in-depth (training-time
  alignment, trained input/output classifiers, capability gating, monitoring), never substring
  matching. Kian pushed on this; reframed the gate accordingly. [memory: input-gate-mechanism-decision]
- **Co-locate each defense with the capability it constrains — not at the query door.** The brief's 4
  asks each belong to a *different* layer: forbidden-actions → capability-by-construction + (future)
  per-tool egress allowlist + per-node least privilege; no-fabrication → output validator #20; stay-in-
  role/social-eng → trust boundary #19 + constrained output + per-step role re-grounding. The input
  gate is the **weakest of 5 layers**, not the safety guarantee. (ADR 0004.)
- **v1 gate design (Kian rejected both "full hybrid now" and "deterministic allowlist"):** the brittle
  part was the *scope* decision. Solution — put scope/role in the **spec-building LLM's system prompt**
  (free: it already reads the query), keep a thin deterministic **forbidden-action denylist** for cheap
  certain logged refusals, and defer a dedicated scope *classifier* to v2 (hybrid). Manual spec-field
  edits are gated by **pydantic schema validation** (typed values can't carry an injection).
- **Trust-boundary wrapper must be unforgeable.** A fixed delimiter is useless (open code → attacker
  types the closer). Construction = **XML tags (model adherence) + an unguessable per-request nonce
  (anti-breakout) + escaping (collisions) + the system-prompt directive (obey-in-place)**, plus input
  hygiene (unicode-normalize, strip zero-width/control/bidi, max-length cap). The wrapper owns only the
  *structural* boundary; the rest is other layers. (ADR 0004 expansion #43.)
- **Granular forbidden categories** (`wet_lab`/`private_data`/`paywalled`) beat one `forbidden_action`
  bucket — needed to log *why* it refused. Watch denylist false-positives: dropped `"run a"`
  (would refuse "run a screening"); avoided `"internal"` alone (vs "internal energy").

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
- **Session 7 (2026-06-21) — LangGraph orchestrator decision (no code):**
  - Shipped **#37** (handoff update). A parallel session shipped **#38** (one-shot LLM→spec and
    RAG→spec demo notebooks).
  - **Settled the parked LangGraph decision → adopt LangGraph for #23** (resume + HITL in v1 scope).
    Worked through the checkpointer-vs-`runs/<id>.json` confusion with Kian and landed on
    checkpointer = live state / JSON = derived audit export. Recorded
    [memory: langgraph-orchestrator-decision]; indexed in MEMORY.md.
  - Proposed the #23 TDD build order (8 slices: compiling graph+checkpointer → domain state model →
    deterministic-core nodes → retry node → `interrupt()` spec gate → exporter → `resume --from` →
    `BaseStore` lab memory). Slices 4 & 5 clear the two orchestrator-23-carryforward debts.
    **Awaiting "start building" + a decision on whether ADR 0003 (orchestrator-on-LangGraph) lands
    first or folds into the #23 PR.**
  - **Reconciled the task list:** marked #17 (RAG) and #21 (provider) completed; added #32
    (hypothesis layer: models + `compile_spec` discriminated union) and #33 (LLM→spec / RAG→spec
    demos) as completed retrofit entries; sharpened #23's description to include the retry loop +
    the two weight-confirmation / error-wrap debts + the LangGraph checkpointer⊇#9 / BaseStore↦#10
    mapping.
- **Session 8 (2026-06-21) — input-side guardrails (#18/#19) + threat-model docs:**
  - Resumed; decided **ADR 0003 lands as its own commit** → shipped **#40** (ADR 0003 orchestrator-on-
    LangGraph). (Parallel session then merged **#41** skeleton + **#23** retry/HITL orchestrator.)
  - Created the `feat/input-policy-gate` worktree; built the gate via TDD: `GateDecision` + in-scope
    tracer (A) → wet-lab refusal (B) → private-data (C) → paywalled (D). **4 passing tests**, denylist
    refactored to `_FORBIDDEN_ACTIONS`. WIP **uncommitted**.
  - Long design discussion → **reframed the gate** (allowlist-first scope triage; weakest of 5 layers)
    and chose the **v1 = denylist + role-system-prompt** approach. Shipped **#42** (ADR 0004 guardrail
    architecture & threat model), then **#43** (ADR 0004 expansion: wrapper construction + attack-surface
    table). Drafted a reader-facing exploit/social-engineering guide but **deleted it at Kian's request**
    (shipped only the ADR in #43).
  - Saved [memory: input-gate-mechanism-decision] (+ reframe); indexed in MEMORY.md.

## Status
- **Working / merged:** `main` @ **`5b8321a`**. Full deterministic slice + RAG + hypothesis layer +
  Bedrock provider (#21) + **LangGraph orchestrator (#23 incl. retry + HITL gate, #41/#23)** + ADRs
  0001–0004 all live. No open PRs.
- **WIP (this session, uncommitted):** input policy gate (#18) on `feat/input-policy-gate` worktree —
  `policy/guardrails.py` + `tests/test_guardrails.py`, **4 passing** (in-scope allowed + wet_lab/
  private_data/paywalled refusals). Run with `PYTHONPATH="$PWD/src" pytest tests/test_guardrails.py`.
- **Partial / next in #18/#19:** `wrap_untrusted` trust-boundary wrapper (nonce + XML + escaping +
  hygiene) and `ROLE_SYSTEM_PROMPT` + `build_chat_messages` not yet written.
- **Known issues:** the gate WIP is **untracked** — commit it before any parallel-session `git clean`
  wipes it. Live Bedrock smoke test still ~15% flaky by design (now mitigated by the merged #23 retry).
- **Open threads:** v2 hybrid LLM scope check (deferred); v2 debts (tokenizer, DFT/XC); #31
  ExcludedCandidate reasons; output validator #20 still to build.

## Next Steps
1. **Merge latest `main` into `feat/input-policy-gate`** (worktree is stale at `c5664e0`; brings ADRs
   0003/0004 + the merged orchestrator). Then **commit the gate WIP** so it survives.
2. **Finish the gate slices (#18) — one red→green at a time, stop after each:** (the next slice, per
   Kian's call, is the trust-boundary wrapper):
   - **E** `wrap_untrusted(text, *, label, nonce)` — XML + unguessable nonce + escaping (anti-breakout);
     optional E4 unicode/zero-width/bidi hygiene + E5 max-length cap. **Open Qs for Kian:** E1–E3 only
     vs include E4/E5 now; **inject** the nonce vs generate inside (lean inject, for testability).
   - **F** `ROLE_SYSTEM_PROMPT` + pure `build_chat_messages(query) -> [("system",…),("human", wrapped)]`
     (user text NEVER in the system slot).
   - **G** wire `build_chat_messages` into `_bedrock_complete` — **coordinate with the parallel session**
     (they own `agent/llm.py`; keep the edit minimal/additive).
   - **RT** red-team tests: deterministic (denylist + wrapper breakout-neutralization) in CI; behavioral
     (poem→declined, "ignore instructions"→stays in role, prompt-leak→refused) `live`-marked.
3. **When shipping:** ship via no-mistakes (bootstrap from the **main repo**: push to `no-mistakes`
   remote → abort → `axi run --intent`); squash-merge in the GitHub UI; then `/sync-main`. Keep this
   handoff committed.
4. **After #18/#19:** output validator (#20); prompts (#22, mostly = the role system prompt);
   ExcludedCandidate reasons (#31); v2 items (hybrid scope check, tokenizer, DFT/XC); renderers
   (#25/#26), CLI (#27), eval (#28), design note (#29-doc), README (#30-doc).

## Context for Next Session
- **Branch:** `main` @ `5b8321a` == `origin/main`. Active worktree `feat/input-policy-gate`
  (`../materials-triage-policy-gate`, stale at `c5664e0`) holds the **uncommitted** gate WIP. A parallel
  session works the orchestrator in the main checkout — coordinate; don't disturb its WIP.
- **How to verify merged state:** `python -m pytest -q` (138 passed, 3 deselected), `ruff check .`.
  Live (needs creds): `pytest -m live` (Bedrock via `~/.aws/credentials`, OpenAlex, MP). RAG quick
  check: `OPENALEX_MAILTO=… python -c "from materials_triage.retrieval.rag import LiteratureRAG,
  OpenAlexFetcher; print(len(LiteratureRAG(OpenAlexFetcher()).search('perovskite oxygen evolution', k=5)))"`.
- **Credentials:** `X_API_KEY` (MP sandbox); `OPENALEX_MAILTO` optional (polite pool); AWS creds for
  Bedrock (#21+) — prefer `~/.aws/credentials` (botocore auto-detects; `load_dotenv` won't load it).
  conftest loads `.env` for live tests; AWS keys in `.env` must be UPPERCASE.
- **Git workflow:** `main` protected, signed commits (`git commit -S`, SSH), squash-merge via GitHub
  UI, then `/sync-main`. pre-commit `ruff format` can abort a commit → re-add + re-commit.
- **Auto-memory (persists):** see `MEMORY.md` — incl. **input-gate-mechanism-decision** (new),
  langgraph-orchestrator-decision, hypothesis-layer (RESOLVED→MEDIUM), ranking-weight-normalization,
  orchestrator-23-carryforward, llm-structured-output-flakiness, langgraph-msgpack-unregistered-types,
  orchestrator-exclusions-two-sources, dft-xc-functional-comparability-v2, rag-tokenizer-v2-todo,
  adapter-testing-seam, materials-project-api, handoff-doc-location, worktree-pythonpath,
  no-mistakes-run-bootstrap.
- **Task list (reconciled):** #1–#17, #21, #32 (hypothesis layer), #33 (demos), **#23 orchestrator
  (LangGraph, #41/#23)** completed; #9/#10 subsumed by #23; **#18/#19 in progress (this session)**;
  #20, #22, #24–#30 remain (#22 ≈ the role system prompt); #31 deferred.
