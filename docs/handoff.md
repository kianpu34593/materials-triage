# Session Handoff - 2026-06-23 12:01

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
today's 6 fields; owns the `FIELD_UNITS`/`_FIELD_ORIGIN` lockstep invariant)* — **supply side MERGED (#61)**:
schema-derived vocabulary via `tools/gen_mp_vocab.py` parsing the vendored MP OpenAPI → committed
`_mp_fields.py` (39 fields, units + xc origins); adapter derives `FIELD_UNITS`/`_FIELD_ORIGIN` from it;
`property_vocabulary()` exposes all 39; `PropertyValue.unit` relaxed to `str | None` for dimensionless; the
adapter's `_scalar` VRH-collapse was added (no-mistakes review caught that it was assumed-but-missing →
`retrieve()` crashed on elastic materials). Live-smoked.
- [x] `property_vocabulary()` on the MP adapter — derive queryable name surface from the published API schema
- [x] grow `FIELD_UNITS` + `_FIELD_ORIGIN` in lockstep with the new surface
- [x] bind the vocabulary into the hypothesis prompt ("use ONLY these names") — **done in #34 on
  `feat/wire-input-gate`** (not yet merged): `build_property_vocabulary_guidance` renders the adapter's
  retrievable property→unit surface, snapshotted once at build time (no live fetch, preserves replay)

  Findings: elasticity has no `origins[]` entry (moduli `origin=None`, retiring the dead `"elasticity"` map);
  MP WAFs the `Python-urllib` UA (use `requests`/a UA). [memory: vocab-prebuilt-not-runtime,
  two-model-categories-strictness, materials-project-api]

**2 · #38 — Push #37 predicates server-side** — ✅ **MERGED (#63, 2026-06-23)**. The architecture shifted
mid-build from the original "optimization on top of a local backstop" to a **trusted-adapter / one-owner**
model — see below. (The local-enforcement complement landed separately as 2c / #64.)
- [x] `ElementPredicate none` → `exclude_elements`; `all` → `elements` (was already done)
- [x] `BooleanConstraint` → same-named exact-match param
- [x] `CountConstraint` → `nelements_min`/`nelements_max`
- [x] **numeric `Constraint`** → `<field>_min`/`<field>_max` (reversed the "keep numeric local" call — push
  *everything* MP can express)
- [x] **`PUSHABLE_PARAMS`** — schema-derived gate (generator parses the vendored `/summary` GET params → 124
  names in `_mp_fields.py`); `_query_params` gates on the *computed param name* ∈ this set
- [x] **live contract suite** — each test sources params from the real `_query_params(spec)` and asserts MP
  honours them; this is the safety net (no local re-check)
- [ ] **LOCAL-FILTER REFOCUS → follow-up PR** (the DB-*inexpressible* half; see task **2c** below)

**Architecture as built (supersedes the old "two invariants").** One owner per predicate, decided by the
adapter's capability:
- **Server-side = single authority for everything MP can express** (numeric, boolean, element all/none,
  count). Correctness guaranteed by **live contract tests** (trusted-adapter), *not* a redundant local
  re-check. We deliberately **dropped invariant 1** (re-enforce-everything-locally) as redundant.
- **Pushability ≠ retrievability.** The filterable surface (124 `/summary` GET params) is distinct from and
  larger than the 39 retrievable fields. `is_magnetic` is retrievable but **not** a query param → pushing it
  **400-crashed `retrieve()`** (now fixed by the gate). `bulk_modulus`/`shear_modulus` filter via
  `k_vrh`/`g_vrh`, not `<field>_min` (a hand-pinned `_FILTER_PARAM_BASE` alias — the only non-1:1 case).
  [memory: mp-pushability-not-retrievability]
- **Trade-off accepted:** pushed filters lose per-candidate exclusion reasons in the audit (MP never returns
  the dropped rows); the trace records the *query*. Only locally-enforced predicates produce per-candidate
  `ExcludedCandidate` reasons.

**2c · 🆕 Exclusive-set local filter + make-it-loud (follow-up to #38) — ✅ DATA-PLANE MERGED (#64, 2026-06-23).**
*Direction set by Kian after a multi-source design discussion. Deliberately lighter than "build full local enforcement."*
**2c's last piece — render caveats in the PI/audit views — is built on `feat/export-synthesis` (not yet merged):** `render.py`'s `render_pi`/`render_audit` both surface run-level `caveats` as a labeled section (see the `[x]` below).

**Decided NOT to build (for now):** the universal-local-authority model (re-enforce every predicate locally
so push is pure optimization), the per-source `filter_capability()` declaration, and the per-call residual
report. Reason: a **second database has a different queryable surface**, and we don't yet want to commit to an
abstraction for that — *"do things server-side for now, see how it goes."* Server-side push stays the
**primary** filter (each adapter pushes what its API allows; MP via `PUSHABLE_PARAMS`).

**Local filter — rescoped.** It is **not** a redundant correctness backstop for what servers already do. Its
scope becomes the things a DB *fundamentally can't* express — **derived / holistic / cross-source** concerns
(synthesizability, holistic toxicity, abundance). No data source for those yet ⇒ **deferred**; the local
filter stays minimal.

**AS BUILT — exclusive-set local filter + caveats (merged #64, live-verified).** Kian's refinement
(2026-06-23): rather than only "make it loud," *build a lightweight
filter to capture the exclusive set* — the predicates that are **retrievable but not queryable (R∩¬Q)**,
derived deterministically from the two committed surfaces (`FIELD_UNITS` = R, `PUSHABLE_PARAMS` = Q). This
**enforces** `is_magnetic`/`any` locally (better than caveating them) while staying lightweight and
source-agnostic; only the genuinely-impossible (¬R∩¬Q) is caveated. Four quadrants: R∩Q → server pushes;
**R∩¬Q → local filter (the exclusive set)**; ¬R∩¬Q → **caveat**. Multi-source falls out free (each adapter's
own R/Q → its own exclusive set), so no `filter_capability()` to hand-maintain.
- [x] `SourceAdapter.classify_predicates(spec) → PredicateRouting` (adapter classifies; core stays
  source-agnostic). MP routes booleans/element-`any` to `local_*`, unsupported-field constraints to `caveats`.
- [x] `apply_local_filters(candidates, routing)` (core) — enforces local booleans (`boolean_mismatch`) +
  element `any` (`element_mismatch`); composes after `apply_hard_filters` (numeric). New reasons added.
- [x] `Candidate.elements` + `retrieve` requests-back the local-bucket fields (`is_magnetic`, `elements`) —
  "request back what you filter on."
- [x] orchestrator `_make_filter_node(adapter)` runs both filters into `filter_excluded`; writes
  `routing.caveats` to a new `caveats` channel → `TriageRun.caveats` (the run-level "make it loud").
- [x] **surface caveats in the PI + audit views** — built on `feat/export-synthesis` (not yet merged):
  the new `render.py` view layer (`render_pi`/`render_audit`) reads `TriageRun.caveats` and prints a
  labeled "Caveats:" section. The orchestrator `render` *node* stays a pass-through; rendering is a
  post-run presentation concern, not a graph step.
- **Pairs with 2b's "bounded + loud" caveat** (cap-hit) — same `caveats` channel, same honesty rationale.

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

**4 · #34 — Wire the orchestrator** *(replace the four PASS-THROUGH nodes)* — **built on
`feat/wire-input-gate`, not yet merged** (strict TDD). `render` is the only remaining pass-through (phase 5).
- [x] gate node — `_gate_node` calls the deterministic `check_input` and raises `InputRefused`
  (reason+category) on a forbidden-capability verdict; a fast logged refusal, not a topic/jailbreak filter
- [x] hypothesis node — binds the source vocabulary (`build_property_vocabulary_guidance`) and grounds in RAG
  via LLM-extracted keywords (`HypothesisProvider.extract_keywords` → `rag.search` → `build_hypothesis_prompt`,
  goal+abstracts fenced as untrusted DATA); only when a `rag` seam is injected (degrades gracefully without it);
  persists the retrieved passages into a new `literature` state channel
- [x] synthesis node — reuses the hypothesis-step `literature` (no second search); retries on a malformed
  `Synthesis` (pydantic) **or** an ungrounded citation (bad `record_id`s fed back via `prior_error` on
  `build_synthesis_prompt`), raising `SynthesisConformanceError` after the cap
- [x] output_validate node — `_output_validate_node` delegates to `validate_output` as a defensive final
  grounding gate, **no retry** (synthesis already retried; a violation here is a real contract breach)

**5 · Renderers → CLI** *(render BOTH views from ONE `TriageRun`)*
- [x] **#25** PI renderer (`view=pi`) · [x] **#26** audit renderer (`view=audit`) — built on
  `feat/export-synthesis` (not yet merged): `render.py` exposes `render_pi`/`render_audit` plus the
  `render_run(run, view=...)` dispatch; `export_run` now carries `synthesis` + `literature` so both views
  read every artifact from one `TriageRun`. Plain text in v1 (Rich deferred to v2).
- [ ] **#27** CLI — `triage "<q>" --view pi|audit`; `resume`

**6 · Capstone / polish**
- [ ] **#41** notebooks refresh
- [ ] **#40** RAG→synthesis (citations + caveats + ordering-fidelity) — unblocks after #35 + #20

**Out of scope for the CLI:** #28 (eval), #29/#30/#36 (docs), #31 (drop reasons), all HB* hosting tasks,
#37 area B (element-class constants) — **deferred**. (#37 area C "ranking beyond weighted-sum" is **partly
shipped** by #66 — the geometric-mean desirability ranker — see "Ranking" below.)

**Ranking — selectable arithmetic/geometric-mean rankers — ✅ MERGED (#66, 2026-06-23).** A second
deterministic ranker now sits alongside the preserved weighted-sum, selected per run by
`TriageSpec.ranking_method` (recorded → traceable/replayable). `arithmetic_mean` (default, unchanged
behavior) is the old `rank()` renamed; `geometric_mean` is the non-compensatory weighted geometric mean of
per-target **Derringer–Suich desirability curves** (`maximize`/`minimize`/`target` "moderate-is-best" shapes
+ a curvature exponent). The contract is fail-fast at spec construction (no silent wrong answers):
- **anchors/curvature are geometric-only** — rejected under `arithmetic_mean` (it scores via pool-relative
  `normalize`, which would silently ignore them); a `target` direction *requires* `geometric_mean`.
- **`geometric_mean` requires announced absolute bounds on every target** (no pool fallback) — so a `0`
  desirability means a genuine acceptability-floor failure, not merely pool-worst (which the geometric mean
  would catastrophically zero). Per direction: maximize→`lower`+`target`, minimize→`target`+`upper`,
  target→all three; strictly ascending; the unused monotonic anchor must be omitted.
- **`arithmetic_mean` keeps pool fallback** (additive, no catastrophic zero).
- **Default stays `arithmetic_mean`** (zero-setup). Flipping the default to `geometric_mean` is **deferred to
  the #34/#22 PR** — because a default-geometric spec forces every target to announce bounds, which nothing
  upstream provides until the hypothesis prompt does. **The bounds announcement is the #34/#22 hypothesis
  responsibility** (the ranking step only enforces via the schema → LLM retry). [memory:
  desirability-ranking-bounds-same-source]

## What's merged on `main` (the foundation to build on)
- **Core:** `core/schema.py` (all frozen models; `Provenance` now carries required `method` + optional
  `xc_functional`), `core/elements.py`, `core/scoring.py` (`apply_hard_filters`/`on_missing`),
  `core/ranking.py` (**two selectable rankers**: `rank_arithmetic_mean` weighted-average + `rank_geometric_mean`
  desirability, #66), `core/scoring.py` also carries the desirability primitives (`desirability_curve`,
  `resolve_bounds`, `score_desirability`), `core/hypothesis.py` (models + `compile_spec`, `kind`-discriminated
  union). #37 spec expressiveness areas **A** (BooleanConstraint / ElementPredicate all·any·none /
  CountConstraint) + **D** (trust metadata) shipped.
- **Retrieval:** `sources/base.py` + `stubs.py` + `materials_project.py` — MP adapter does a **two-call**
  retrieve (summary → batched `/materials/tasks/` for per-property `xc_functional`). `retrieval/rag.py`
  BM25 literature RAG (abstract-only).
- **#39 vocabulary (merged #61):** `tools/gen_mp_vocab.py` + vendored `tools/mp_summary_schema.json` →
  committed `src/materials_triage/sources/_mp_fields.py` (39-field `{unit, origin}` table); adapter derives
  `FIELD_UNITS`/`_FIELD_ORIGIN` from it and exposes `property_vocabulary()`; `_scalar` collapses the VRH
  moduli dict; `PropertyValue.unit: str | None` (dimensionless). `tools/` on the test pythonpath.
- **#38 server-side push (merged #63):** `_query_params` pushes every DB-expressible hard filter (numeric
  `<field>_min/_max`, booleans, `elements`/`exclude_elements`, `nelements` range), gated on the schema-derived
  **`PUSHABLE_PARAMS`** (124 `/summary` GET params, now also generated into `_mp_fields.py`). Fixes the
  `is_magnetic` 400 + maps the moduli via the `k_vrh`/`g_vrh` alias (`_FILTER_PARAM_BASE`). A `live`-marked
  **contract suite** (params sourced from the real `_query_params`) verifies MP honours each — the
  trusted-adapter safety net. [memory: mp-pushability-not-retrievability]
- **2c exclusive-set local filter (merged #64):** `SourceAdapter.classify_predicates(spec) -> PredicateRouting`
  (R∩¬Q → local, ¬R∩¬Q → caveat). `core/scoring.py` `apply_local_filters` enforces local booleans
  (`boolean_mismatch`) + element `any` (`element_mismatch`); `Candidate.elements` + retrieve request-back;
  orchestrator `_make_filter_node(adapter)` runs both filters + writes `TriageRun.caveats`. Live-verified.
- **Selectable rankers (merged #66):** `core/ranking.py` `rank_arithmetic_mean` (was `rank()`) +
  `rank_geometric_mean`; desirability primitives in `core/scoring.py`; `TriageSpec.ranking_method`
  (`arithmetic_mean` default | `geometric_mean`) dispatched in `orchestrator._rank_node` via `_RANKERS`.
  `RankingTarget` gains `direction="target"` + `lower/target/upper/curvature`; `TriageSpec`/`RankingTarget`
  validators enforce the geometric-only + announced-bounds + strict-ascending contract (see "Ranking" above).
  CLAUDE.md step 6 + the demo notebook were synced by the PR. [memory: desirability-ranking-bounds-same-source]
- **Agent:** `agent/llm.py` Bedrock `HypothesisProvider` (injected `complete` seam; `us.*` inference-profile
  model id). `agent/prompts.py` `ROLE_SYSTEM_PROMPT` + `build_chat_messages`. `agent/orchestrator.py`
  LangGraph graph with per-stage exclusion channels + retry + spec-build HITL gate; `core/run_trace.py`
  audit export + `memory/store.py` `BaseStore`. **⚠️ on `main`, the gate / hypothesis / synthesis /
  output_validate nodes are still PASS-THROUGHS — #34 wires all four (built on `feat/wire-input-gate`, not
  yet merged); the new lazy Bedrock `SynthesisProvider` + `HypothesisProvider.extract_keywords` seams and the
  `literature` state channel land with it, leaving `render` as the only pass-through.**
- **Guardrails:** `policy/guardrails.py` `check_input` + `wrap_untrusted` + `_scrub`.
- **Server (net-new, outside v1 deep-plan):** `server/mt_server/policy.py` `resolve_model` (#52); `server/`
  wired into pytest. See the hosting task list below.
- **Docs/ADRs:** 0001 retrieval · 0002 RAG abstracts-only · 0003 orchestrator-on-LangGraph · 0004 guardrail
  threat-model · 0005 hosting & step-cache.
- **Reference impl (NOT on `main`):** `feat/fast-track-wire-guardrails` (local + origin) — full end-to-end
  wiring + `core/synthesis.py`, `agent/validator.py`, `render.py`, `cli.py`. Port from it. (NB:
  `property_vocabulary()` + the VRH `_scalar` collapse are now **on `main`** via #61 — no longer "port from
  reference." ⚠️ Verify each remaining helper actually exists on `main` before citing it — the `_scalar`
  assumption-not-verified bug is exactly how #61's review caught a crash. [memory: verify-against-main-not-reference-impl])

## Status
- **`main` @ `a40ef46`**, clean. **284 tests pass** (10 `live` deselected, incl. the MP filter-contract suite).
- **Selectable rankers merged (#66):** arithmetic/geometric-mean rankers on `main` via
  `TriageSpec.ranking_method`; the desirability contract (geometric-only anchors, geometric-requires-announced-
  bounds, same-source/strict-ascending) is fail-fast at spec construction. Default stays `arithmetic_mean`;
  geometric-as-default deferred to the #34/#22 PR (needs upstream bounds announcement). [memory:
  desirability-ranking-bounds-same-source]
- **#38 + 2c done** (#63 server-side push, #64 exclusive-set local filter): the full filtering architecture is
  on `main` — **server-side primary (PUSHABLE_PARAMS) + deterministic local filter for the exclusive set
  (R∩¬Q) + loud caveats (¬R∩¬Q → TriageRun.caveats)**. `is_magnetic` and element `any` enforced end-to-end;
  live-verified.
- **#39 supply side done** (merged #61). Increment 4 (prompt binding) → **#34**.
- **#37 done** (A #51 + D #55; B deferred; **C "ranking beyond weighted-sum" partly shipped by #66**).
- **Parallel sessions:** the other worktrees are on `feat/retrieve-pagination` (2b, PR pending — was 529-blocked)
  and `feat/synthesis-primitives`. Independent of this handoff; do not touch their branches.
- **Known issues / loose ends:**
  - **2 informational no-ops from #66's review** (intentional): (1) `rank_geometric_mean` stores raw `dᵢ` in
    `ScoredCandidate.contributions` (multiplicative, do NOT sum to score) vs `rank_arithmetic_mean`'s additive
    shares — a future audit renderer must not assume additivity. (2) `docs/ultimate-design.md` §C3 still frames
    geometric-mean desirability as "future" — left as roadmap; reclassify when convenient.
  - **4 informational nits from #64's review** to fix opportunistically (next time touching the files):
    (1) `classify_predicates` comment oversells — the ¬R caveat is *additive* (explains the empty result),
    NOT *protective*; `apply_hard_filters` still drops the candidates as `missing_data`. (2) `classify_predicates`
    runs twice per run (retrieve + filter) — pure, harmless. (3) duplicate caveat strings if a numeric + boolean
    constraint share a property name. (4) `element_mismatch` conflates "composition not fetched" with "members
    genuinely absent" (boolean branch distinguishes `missing_data`).
  - Live Bedrock smoke test ~15% flaky by design (mitigated by the #45 retry loop).
  - LangGraph msgpack "unregistered type" warnings on our pydantic models — round-trips today, will block in a
    future version [memory: langgraph-msgpack-unregistered-types].

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
  mismatch, VRH-dict crash). Render BOTH views from ONE `TriageRun`. *#61 corollary:* a placeholder test
  value (`bulk_modulus=1.0`) hid the real `{voigt,reuss,vrh}`-dict crash — fixtures for object-typed fields
  must use the **real payload shape**, and verify a ported helper exists on `main` before citing it (the
  no-mistakes review's grep caught the missing `_scalar`). [memory: verify-against-main-not-reference-impl]
- **Pushability ≠ retrievability (#38).** What a field *returns* and what it can be *filtered on* are two
  surfaces. The `/summary` GET endpoint declares **124 query params** (≠ the 39 retrievable fields):
  `is_magnetic` is retrievable but not queryable (pushing it **400s**), and the moduli filter via
  `k_vrh`/`g_vrh` not `<field>_min`. Schema-derive the pushable set (`PUSHABLE_PARAMS`), gate on it, and
  **trust the adapter** — the live contract suite (params sourced from the real `_query_params`) is the only
  thing catching a silently-ignored/typo'd param, since there's no local re-check. [memory:
  mp-pushability-not-retrievability]
- **MP OpenAPI for the vocabulary:** at `GET /openapi.json` (3.1.0; `SummaryDoc` = 69 props) — needs the
  `X_API_KEY` header **and** a non-`urllib` User-Agent (MP WAFs `Python-urllib/*` → 403; `requests` passes).
  Origins are a small controlled set; **elasticity has no `origins[]` entry** (moduli `origin=None`,
  functional untraceable) and surface energies trace only to method-named docs. [memory: materials-project-api]
- **Encode LLM contracts in the JSON schema, not hidden `model_validator`s** (discriminated union). LLM
  structured output is ~15% schema-flaky → the orchestrator retry loop is load-bearing. [memory: llm-structured-output-flakiness]
- **MP API:** sandboxed mirror, `X_API_KEY` header; query-id ≠ returned-id; units not in payload (pinned in
  adapter); functional lives in the task doc, varies per-property. [memory: materials-project-api, dft-xc-functional-comparability-v2]
- **no-mistakes:** bootstrap from the **main repo** (not a worktree); `git push no-mistakes <branch>` → `axi
  abort` → `axi run --intent`; verify locally with `PYTHONPATH="$PWD/src" pytest`. [memory: no-mistakes-run-bootstrap]
  *#66 corollary:* runs are per-branch and coexist concurrently (multiple sessions ran in parallel fine); a
  worktree can drive `respond`/`rerun`, only the bootstrap *push* must come from the main repo. Transient
  `529 Overloaded` API errors crash the no-mistakes Claude agents (review/fix/lint) → just `rerun`.
- **Desirability/geometric ranker (#66): min-max normalization + a non-compensatory geometric mean is a trap.**
  Pool-relative `d=0` (worst-in-pool) under `Π dᵢ^wᵢ` zeros a candidate's whole score for being last on *one*
  axis — so the geometric ranker must use **absolute announced bounds** where `0` means a real floor failure,
  never pool fallback. Mixing one announced + one pooled anchor also flips a ramp's sign (maximize → minimize)
  → the **same-source rule**. The fix family is all fail-fast spec validation ("no silent wrong answers"): same
  theme as H₂O. Announcing the bounds is an **upstream/hypothesis** duty (#34/#22). [memory:
  desirability-ranking-bounds-same-source]

## Design direction (v2+): XC-functional-FIRST retrieval, not canonical-value acceptance
*Raised by Kian 2026-06-23 while tracing #38's per-property functional handling. Important; likely **not v1**.*

**The problem.** DFT property values vary *a lot* by XC functional, and they are not cross-comparable.
Today the MP adapter accepts MP's **canonical** summary value per property and merely *tags* its
functional on provenance (`xc_functional`) — it never chooses the functional or guards comparability. So a
`band_gap` ranking can silently mix a GGA value (material A) with an r2SCAN value (material B), which is
physically wrong. **We should not blindly accept the canonical value.** A scientist who knows the tasks
fixes the functional *first*, then fetches the value.

**Domain rule (which functional, and why).** The right functional is largely determined by the
property/material class *before* retrieval:
- **GGA** — fine for many **energies** / relative stability; the broadest data coverage.
- **GGA+U** — needed for **strongly-correlated** materials (transition-metal oxides, localized d/f electrons).
- **r2SCAN** — **most accurate** (better gaps, energetics), but also the **sparsest** data → choosing it
  trades accuracy for coverage. This coverage/accuracy tension is the crux of the iterate loop below.

**Proposed agent behavior (v2):**
1. **Decide the XC functional at the *hypothesis* phase** — make it an explicit, cited part of the spec
   (a new spec field, e.g. `xc_functional` preference/requirement), reasoned from property + material class,
   not left to MP's canonicalization.
2. **Retrieve values computed with that functional** — query/filter by `run_type` (push it as a retrieval
   constraint and/or filter the per-property provenance to the chosen functional) instead of accepting the
   canonical value. The value the pipeline ranks on must be the chosen-functional value.
3. **Iterate on coverage** — if too few materials have a value at the chosen functional (e.g. r2SCAN is
   sparse), **fall back to a different functional on a second turn** and record the swap as a caveat. This is
   a natural fit for the existing traced state machine + `resume`/iterate design (the run already supports
   re-running a step) and the multi-turn hypothesis loop.

**Implications / open questions for whoever picks this up:**
- Spec schema grows an XC-functional dimension (LLM-output model → keep it strict; see
  [memory: two-model-categories-strictness]). This is another instance of "quality ceiling = spec
  expressiveness" [memory: spec-expressiveness-quality-ceiling].
- Retrieval changes from "one canonical value + tag" to "value *selected by* functional." Needs the
  per-property functional **before** ranking, and a defined precedence/fallback order (r2SCAN → GGA+U → GGA?).
- The single-canonical assumption in `_origin_task_ids` (one task per origin group; dict last-wins on a
  duplicate origin name) must be revisited — multiple same-property tasks at different functionals become
  *first-class*, not collapsed by MP. The values for non-chosen functionals are in the raw `/tasks/` store,
  not the summary — so this likely needs task-level retrieval, not just summary.
- Supersedes the "tag only" stance of [memory: dft-xc-functional-comparability-v2] (PR #55 added the tag;
  this is the *consumption* — choose + fetch + flag/restrict ranking by functional).

## Context for Next Session
- **Branch:** `main` @ `a40ef46`, clean. Build the next increment in a worktree (run pytest with
  `PYTHONPATH="$PWD/src" pytest`). Port from `feat/fast-track-wire-guardrails`.
- **Verify merged state:** `python -m pytest -q` (284 pass, 10 deselected); `ruff check .`. Live (needs
  creds): `pytest -m live`.
- **Next up (pick one):** (a) **pagination** (2b — page `_skip`/`_limit` to exhaust the filtered set; the
  natural sibling now that #38 makes the page filterable; reuses the `caveats` channel for the "capped at N"
  notice); (b) **#34** — **in flight on `feat/wire-input-gate`** (gate/hypothesis/synthesis/output_validate
  wired + vocabulary binding done; not yet merged); after it lands, `render` is the only pass-through left;
  (c) **views / render** (#25–#27) — also unblocks 2c's caveat rendering. The deterministic filtering core is
  now complete, so the remaining v1 gaps are I/O (pagination), the LLM nodes (#34, in flight), and presentation
  (views).
- **Credentials:** `X_API_KEY` (MP), `OPENALEX_MAILTO` (optional polite pool), AWS creds for Bedrock
  (prefer `~/.aws/credentials`; conftest `load_dotenv`s `.env`; AWS keys in `.env` must be UPPERCASE).
  **Never read/print the AWS creds or `X_API_KEY`.**
- **Git workflow:** `main` protected, signed commits (`git commit -S`), squash-merge via GitHub UI, then
  `/sync-main`. Ship via `/commit-commands:commit-push-pr` or no-mistakes. pre-commit `ruff format` can
  abort a commit → re-add + re-commit.
- **Collaboration rules (CLAUDE.md):** ask before choosing between approaches; one function at a time then
  stop for approval; TDD via the `tdd` skill (never batch tests); **don't start coding until told.**
- **Task tracker:** v1 path = ~~#39~~, ~~#38~~, ~~2c~~, ~~ranking #66~~ → **pagination (2b, in flight on
  `feat/retrieve-pagination`)**, **#34 (in flight on `feat/wire-input-gate` — wires the four LLM-path nodes)**,
  #20, #35, #22, **#25 + #26 (renderers — built on `feat/export-synthesis`, not yet merged)**, #27, #41,
  #40 (see Plan). Completed: #1–#19, #21,
  #23, #24, #32, #33, #37, **#39 (supply side; binding → #34)**, **#38 server-side push (#63)**, **2c
  exclusive-set local filter (#64)**, **selectable rankers (#66)**. 2c's render-caveats piece is now built
  in the renderers (`feat/export-synthesis`); the CLI (#27) is the last view-layer gap. **#34/#22 now also owns:** bind the source vocabulary into the
  hypothesis prompt AND have the LLM announce desirability bounds for `geometric_mean` targets (prerequisite to
  flipping the default to `geometric_mean`). Deferred: #37 area B (on the critical path for "metal oxides"); the
  **multi-source filter abstraction** ("see how it goes"); **v2 XC-functional-first retrieval** (design note
  above). Hosting = HB1–HB10.
