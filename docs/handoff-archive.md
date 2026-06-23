# Handoff Archive — deep history

> Overflow from the living [`docs/handoff.md`](handoff.md). Append-only narrative history and
> older session-by-session discoveries live here so the living doc stays lean. Most gotchas below
> are also captured in auto-memory (see `MEMORY.md`). Newest sessions at the bottom.

## Work Done (per-session history)

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
    refactored to `_FORBIDDEN_ACTIONS`.
  - Long design discussion → **reframed the gate** (allowlist-first scope triage; weakest of 5 layers)
    and chose the **v1 = denylist + role-system-prompt** approach. Shipped **#42** (ADR 0004 guardrail
    architecture & threat model), then **#43** (ADR 0004 expansion: wrapper construction + attack-surface
    table). Drafted a reader-facing exploit/social-engineering guide but **deleted it at Kian's request**
    (shipped only the ADR in #43).
  - Saved [memory: input-gate-mechanism-decision] (+ reframe); indexed in MEMORY.md.
- **Session 9 (2026-06-22) — orchestrator completion (parallel) + fast-track demo + docs + cleanup:**
  - Parallel session **completed the orchestrator**: merged **#45** (hypothesis retry + spec-build HITL
    gate) and **#47** (audit export + crash-recovery resume + lab memory) → #23/#24/#9/#10 done; and
    **#46** (input-side guardrails #18/#19 + role prompt `agent/prompts.py`).
  - **Fast-track demo build** (throwaway branch `feat/fast-track-wire-guardrails`, pushed, **no PR to
    `main`**): wired the whole pipeline end-to-end — gate (`check_input`), hypothesis (trust-boundary
    `build_chat_messages` + vocabulary binding), retrieve/filter/rank, synthesis (`core/synthesis.py`,
    grounded+cited), output validator (`agent/validator.py`), PI/audit renderers (`render.py`), and a
    `cli.py` + `scripts/demo.py`. Verified live (MP + Bedrock) across 5 parallel subagent runs;
    diagnosed + fixed vocab drift (added `property_vocabulary()`), the VRH-dict crash, and the PI/audit
    view mismatch. **196 tests** passed on the branch.
  - Shipped **docs-only** PRs: **#48** (kid-friendly 9-step README flowchart) and **#49**
    (`docs/fast-track-learnings.md` + `docs/ultimate-design.md` + handoff pointer); used `/lavish` to
    visualize the ultimate design.
  - **Cleanup:** deleted the stale merged remote branch `docs/literature-rag-demo` (superseded by
    #32/#33; notebook already on `main`); removed the fast-track + policy-gate worktrees; `stash@{0}`
    holds the parallel readme-kid-flowchart notebook WIP; kept untracked `.mcp.json`.
  - **Reconciled the harness task tracker vs merged `main`** (this session): flipped **#34 → pending**
    (fast-track-only; nodes still pass-throughs), confirmed #22 stays pending, verified all other
    statuses correct. Refreshed this handoff (`/handoff update`).
  - **Added 4 v1 build tasks from the fast-track learnings** (Kian's call — full v1, not doc-only):
    **#37** expand spec expressiveness (booleans/counts/element-class — fixes H₂O), **#38** server-side
    filter pushdown, **#39** schema-derived vocabulary, **#40** wire literature RAG into synthesis
    (citations + caveats + ordering-fidelity). Deps wired: #38/#39 ← #37; #40 ← #35 + #20.
- **Session 10 (2026-06-22) — hosting & step-cache design (brainstorm; no v1 code):**
  - Briefly explored the **output validator (#20)** via `/tdd` (read the fast-track `validator.py` +
    `synthesis.py` reference, scoped the slice, surfaced the coupled-vs-decoupled `Synthesis`
    dependency) — then **paused** because a parallel session is editing the spec (#37).
  - Long **hosting brainstorm** with Kian: sorted his 3 asks into FE/BE; settled topology (monolith on
    AWS → splittable), billing (**Pattern 1** pooled+meter; Bedrock has no user API key), repo layout
    (**monorepo**, `server/` + `web/` siblings), the SSE+POST request/stream protocol incl. the HITL
    spec-gate pause, the storage layers, and the **content-addressed step cache** (both caching +
    idempotency + cross-attempt diff; LLM cached-for-repro + force-fresh; no TTL; global shared cache).
  - **Visualized via `/lavish`** (`.lavish/hosting-design.html`) — Kian reviewed in-browser, requested
    the §4 flow as a **Mermaid sequence diagram** (applied), and queued answers resolving all 4 open
    questions (Q1 global force-fresh first · Q2 no-TTL/`source_version` · Q3 global shared cache · Q4
    → write ADR + add tasks).
  - **Wrote ADR 0005** (`docs/design/0005-hosting-and-step-cache.md`) and **added 10 harness tasks**
    (#1–#10) for the hosting build with deps (2←1, 3←2, 4←1, 7←6, 8←7, 9←7+2, 10←2); #1/#5/#6 unblocked.
  - **Worktree shuffle:** discovered ADR 0005 + `.lavish/` were locally excluded via `.git/info/exclude`
    (a no-mistakes session is running on the `feat/spec-expressiveness-37` worktree). Reverted a stray
    `handoff.md` edit there to keep that worktree clean, then moved all docs work to a fresh
    **`docs/hosting-adr-0005`** worktree off `origin/main`; force-added the ADR, added `.lavish/` to the
    tracked `.gitignore`. **This PR is docs-only.**
- **Session 11 (2026-06-22) — first server-side build increment + ship both PRs:**
  - Built **`server/mt_server/policy.py` `resolve_model`** TDD, one red→green slice at a time (6 tests),
    after Kian redirected from `cache_key` (which belongs in core) to a genuinely-server-side unit.
    Wired `server/` into `pyproject` pytest discovery.
  - Drove it through **no-mistakes** (run `01KVRJ67…`): review 0 findings, test passed (server tests ran
    green in the gate's own checkout), one `document`-step `ask-user` finding (ADR-0005-missing) — verified
    the ADR exists on the docs branch and **approved** rather than authoring a duplicate. → **PR #52**,
    CI green, merged.
  - Pushed the docs branch + opened **PR #53** (ADR 0005 + `.gitignore` + Session-10 handoff) directly
    (docs-only). Both PRs **merged**.
  - Ran **`/sync-main`**: `main` → `129cb0a`; removed the `mt-hosting-docs` worktree; force-deleted the
    two merged branches (`feat/hosting-server`, `docs/hosting-adr-0005`); restored the main repo to `main`.
  - Refreshed this handoff (`/handoff update`).
- **Session 12 (2026-06-22) — #37 area D (value-level trust metadata) built, shipped, merged:**
  - In the `mt-value-trust` worktree, TDD one model/function at a time (6 signed commits): D1
    `Provenance.method` **required** + `literature` (every existing `Provenance` fixture across 7 test
    files updated) → D2 `Provenance.xc_functional` optional → D3a `_origin_task_ids`+`_FIELD_ORIGIN` →
    D3b `_field_task_id` → D3c `_fetch_run_types` (batched tasks call) → D3d wired into `retrieve`
    (per-property provenance + functional). Scope changed mid-build per Kian: **`uncertainty` cut**;
    **adapter population pulled in** (originally deferred). Live-probed the real MP API to design against
    actual `origins`/`run_type` shapes.
  - **Rebased onto post-area-A `main`** (#51/#52/#53) with **zero conflicts**; 206 tests pass, ruff clean,
    live-verified. Pushed to origin; Kian ran no-mistakes from the main checkout → **PR #55 merged**.
  - Saved memories: `no-uncertainty-field-on-propertyvalue`, `mp-summary-field-surface-and-field-origin-scope`.
  - Ran **`/sync-main`**: `main` already at `06e4eae`; force-deleted merged `feat/value-trust-metadata`
    (local + remote auto-deleted); refreshed this handoff (`/handoff update`, shipped as #56).
- **Session 13 (2026-06-22) — sync, task reconciliation, planning the "better-than-yesterday" v1:**
  - `/sync-main` after #56 merged: `main` → `cb3d92e`, pruned `feat/spec-expressiveness-37`.
  - Marked **#37 completed** (areas A #51 + D #55 shipped; **area B element-class + area C ranking
    DEFERRED** per Kian — low priority). This unblocked #38/#39/#41.
  - Scoped the path to a working CLI v1 and the quality work to beat the fast-track demo (see the
    living handoff's Plan section). Trimmed this handoff doc into living + archive.

## Older discoveries (session-tagged; most also in auto-memory / MEMORY.md)

- **TDD discipline:** thin frozen models; behavior lives in functions. Some happy-path tests are
  characterization (green-on-arrival) — flag them honestly.
- **Design split ("Way B"):** `Criterion` → `Constraint` (hard min/max) + `RankingTarget` (soft
  direction/weight/on_missing). Weights are **proportional, sum to 1**.
- **`identifier` vs `record_id`:** Candidate.identifier = source-returned identity; Provenance.record_id
  = per-value receipt. Coincide in single-source v1.
- **Cloudflare bans `Python-urllib` UA (403);** `requests`/browser UA pass. Change UA, don't retry.
- **Hypothesis-layer decision RESOLVED → MEDIUM** (cited spec-deltas bridging fuzzy goal → DB proxies);
  deterministic core ≈ MP-API by design; Rich is v2. [memory: hypothesis-layer-open-decision]
- **pre-commit `ruff format` can ABORT a commit** by reformatting staged files — re-`git add`, re-commit.
- **Literature RAG (#17, PR #31):** abstract-only (ADR 0002); MP = fact layer, RAG grounds *direction*
  + *cited claims*. Keep+flag missing abstracts (rank on title), drop only works empty in BOTH title
  and abstract, keep zero-relevance hits stable, score stamped by `_rank` on frozen copies.
- **OpenAlex specifics:** abstracts as `abstract_inverted_index`; ~20–40% null abstracts; `id`/`doi`
  are URLs (strip prefixes); keyless, set `mailto` for the polite pool. Endpoint `/works?search=&…`.
- **Formula-aware tokenizer + v2 debt** (`_tokenize` keeps TiO2 / La0.6Sr0.4CoO3 intact). v2: case-fold,
  synonymy, sub-formula, stemming; unicode-minus formulas don't survive. [memory: rag-tokenizer-v2-todo]
- **BM25 small-corpus gotcha:** N=2 docs → IDF=0 → all-zero scores; ranking tests need ≥3 docs.
- **— Session 6: LLM provider #21 —**
- **Structured output ≠ truth, only form.** Pydantic validation conforms the LLM's *shape*; truth is
  enforced by citations + the human gate + the deterministic pipeline + output validator (#20).
- **model_validator rules are INVISIBLE to the JSON schema the LLM receives.** The original
  flat-`Proposal` + `_payload_matches_kind` validator let Bedrock emit `kind="ranking_target"` with no
  payload (legal per schema). Fix = **discriminated union** (#34): the `oneOf` per-branch-required
  payload is now IN the schema. **Lesson: encode LLM contracts in the schema, not hidden validators.**
- **Bedrock model id needs the `us.*` inference-profile form** — the bare `anthropic.claude-…` id
  raises `ValidationException: on-demand throughput isn't supported`.
- **Credential detection for the live test:** gate on `botocore.session.Session().get_credentials()`
  (defensive import), which resolves env vars / a profile / `~/.aws/credentials` without reading the
  secret. **`load_dotenv()` does NOT load `~/.aws/credentials`** (INI read by boto3); conftest loads
  `.env` at collection time so `skipif` sees vars; AWS keys in `.env` must use UPPERCASE names.
- **— Session 7: LangGraph orchestrator decision —**
- **LangGraph-vs-hand-roll RESOLVED → LangGraph**, because real `resume --from` + HITL (spec gate) are
  in v1 scope — those ARE LangGraph's checkpointer + `interrupt()`. [memory: langgraph-orchestrator-decision]
- **The reframing that settled it:** checkpointer = **live execution state** (resume/recovery,
  framework-owned, transient); `runs/<id>.json` = **durable audit report** (what `view=audit` renders) —
  a **read-only export from `get_state_history()`**, ONE write path + one derived read model, NOT two stores.
- **Design discipline:** the checkpointer only persists what's routed through **typed graph state**, so
  graph state = our domain pydantic state (one channel per step) or the audit export loses provenance/
  excluded-set/missing-flags. [memory: orchestrator-exclusions-two-sources]
- **— Session 8: input-side guardrails / threat model —**
- **A keyword denylist is NOT how guardrails work.** Frontier safety = defense-in-depth (training-time
  alignment, trained classifiers, capability gating, monitoring), never substring matching. The input
  gate is the **weakest of 5 layers**, not the safety guarantee. [memory: input-gate-mechanism-decision]
- **Co-locate each defense with the capability it constrains — not at the query door.** forbidden-actions
  → capability-by-construction + per-tool egress allowlist + per-node least privilege; no-fabrication →
  output validator #20; stay-in-role/social-eng → trust boundary #19 + per-step role re-grounding. (ADR 0004.)
- **Trust-boundary wrapper must be unforgeable:** **XML tags + an unguessable per-request nonce + escaping
  + the system-prompt directive**, plus input hygiene (unicode-normalize, strip zero-width/control/bidi,
  max-length cap). The wrapper owns only the *structural* boundary. (ADR 0004 expansion #43.)
- **Granular forbidden categories** (`wet_lab`/`private_data`/`paywalled`) beat one bucket — needed to log
  *why* it refused. Watch denylist false-positives: dropped `"run a"`; avoided `"internal"` alone.
- **— Session 9: fast-track end-to-end demo —**
- **Vocabulary drift → silently-empty results.** The hypothesis LLM free-named `band_gap_eV` vs the
  adapter's `band_gap` → every candidate `missing`, 0 ranked. Fix = **vocabulary binding** (#39): a
  `SourceAdapter.property_vocabulary()` publishing retrievable names+units, fed into the prompt.
- **Only the live end-to-end run caught the integration bugs** unit tests missed: vocab drift; a PI/audit
  view mismatch (render BOTH views from ONE `TriageRun`); a VRH-modulus dict crash (MP returns
  `{voigt,reuss,vrh}` → collapse to `vrh` via `_scalar()`).
- **Synthesis ordering-fidelity is NOT enforced.** The prose called a candidate "first" that the ranker
  placed fourth — grounding passed but ordering wasn't checked. (folded into #40.)
- **— Session 10/11: hosting & step-cache design —**
- **Frontend = render-only; backend = the whole agent.** The HITL `interrupt()` makes a stateful backend
  mandatory. **Monolith on AWS now, split FE/BE later; serverless rejected** (Lambda's 15-min cap fights
  long pausable streaming runs). **Monorepo** (`server/` + `web/` siblings importing the pure core).
- **Bedrock has NO paste-able user API key** (auth is AWS IAM / SigV4) → billing = **Pattern 1: pooled
  account + meter-and-bill**. BYO-spend (Pattern 4 Anthropic key / enterprise Pattern 2 AssumeRole) deferred.
- **Step cache = content-addressed** `key(step)=H(step_name, RECURSIVE resolved_inputs, source_version,
  llm_salt)`. Recursive inputs + a real `source_version` are the two correctness load-bearers. Cache LLM
  steps for repro + a force-fresh toggle; **no TTL**; **global shared cache** (public data ⇒ free
  cross-user reuse). Checkpointer must move off `MemorySaver` to Postgres/DynamoDB before >1 instance. ADR 0005.
- **The cache belongs in core, not server.** The boundary is *web-vs-not*, not pure-vs-impure: core holds
  execution machinery (orchestrator, checkpointer, run_trace, memory/store); `server/` owns only web
  concerns (FastAPI/SSE/auth/limits/metering + *choosing* storage backend).
- **no-mistakes gotchas:** the gate runs in its OWN worktree (editable install resolves to
  `~/.no-mistakes/worktrees/<id>`); verify locally with `PYTHONPATH="$PWD/src" pytest`. `axi run` can't
  START a fresh run — bootstrap from the **main repo** (not a worktree, hook misfires): `git push
  no-mistakes <branch>` → `axi abort` → `axi run --intent`. Gate refuses ANY untracked file.
  [memory: no-mistakes-run-bootstrap, worktree-pythonpath]
- **— Session 12: #37 area D —**
- **Cut a field nothing fills.** Planned `PropertyValue.uncertainty` was DROPPED — MP's DFT values carry
  no error bar. The test that killed it KEPT `method`/`xc_functional`: *does any v1 producer fill this?*
  [memory: no-uncertainty-field-on-propertyvalue]
- **The functional isn't in the MP summary endpoint — and varies PER PROPERTY.** `origins[]` is keyed by
  MP-internal doc names (NOT our field names) + carries only `task_id`; the functional (`run_type`: GGA /
  GGA+U / r2SCAN) lives in the **task doc**. Adapter now makes **two calls**: summary → batched
  `/materials/tasks/?task_ids=…&_fields=task_id,run_type`. Bridge = hardcoded `_FIELD_ORIGIN`. Within one
  material band_gap can be GGA while energy is r2SCAN — confirmed live. [memory: dft-xc-functional-comparability-v2]
- **MP summary exposes ~69 fields (22 numeric + 6 boolean); the adapter supports only 6.** `_FIELD_ORIGIN`
  is scoped to those 6; expanding is **#39**, and `FIELD_UNITS`+`_FIELD_ORIGIN` must grow in **lockstep** (a
  lockstep invariant test was proposed + deferred to #39). [memory: mp-summary-field-surface-and-field-origin-scope]
