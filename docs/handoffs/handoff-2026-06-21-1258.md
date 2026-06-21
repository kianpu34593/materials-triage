# Session Handoff - 2026-06-21 12:58

> Renamed from `handoff-2026-06-20-1221.md`. NOTE: that prior file disappeared from disk
> (untracked `docs/handoffs/` was wiped by a parallel session's `git clean`/checkout). This
> file is reconstructed from it + this session's work. **Keep handoffs committed or they vanish.**

## Task
Build **Materials-Triage** (public-data-only materials-research triage agent) as single-function
TDD increments, per `Deep-Plan-materials-triage-agent-2026-06-19-1429.md`. Core data-model,
deterministic logic, retrieval, **and the literature RAG (#17) are all complete and merged to
`main`**. The project is now entering the **LLM layer** (#21 provider / #22 prompts / #23
orchestrator) and **guardrails** (#18 input gate / #19 trust boundary / #20 output validator).
**Multiple Claude sessions run in parallel on this repo** — coordinate via worktrees.

## Scope
- **DONE + merged:** data models (schema.py), logic (scoring.py + ranking.py), retrieval
  (SourceAdapter + Materials Project adapter), **literature RAG (#17)**, plus demo notebooks +
  ADRs 0001/0002.
- **In progress (another session):** #21 LLM provider (Bedrock client + mock) and the hypothesis
  layer (`core/hypothesis.py`, `compile_spec`); see `agent/`, `tests/test_llm.py`,
  `tests/test_hypothesis.py` as uncommitted/untracked WIP on its branch(es) (`feat/llm-provider`,
  `feat/hypothesis-discriminated-union`).
- **Next up:** guardrails (#18–#20), prompts (#22), orchestrator (#23), then renderers/CLI/eval.
- **PARKED behind a LangGraph decision:** #9 TriageRun/Step trace + #10 lab memory store —
  revisit before #23 (checkpointer ⊇ #9 trace+resume; BaseStore ↦ #10 lab memory).
- **Collaboration rules (CLAUDE.md — follow exactly):** ask before choosing between approaches;
  implement ONE function at a time then stop for approval; TDD via the `tdd` skill (one red→green
  at a time, never batch); discuss behavior before coding; **don't start coding until told**.

## Files
- `src/materials_triage/core/schema.py` — all frozen data models (merged).
- `src/materials_triage/core/elements.py` — 118 IUPAC element symbols (merged #20).
- `src/materials_triage/core/scoring.py` — `normalize` + `apply_hard_filters` + `on_missing` (merged).
- `src/materials_triage/core/ranking.py` — weighted-average ranker (merged #26).
- `src/materials_triage/core/hypothesis.py` — hypothesis models + `compile_spec` seam (merged #30;
  **further modified, uncommitted** by the parallel session).
- `src/materials_triage/sources/base.py` / `stubs.py` / `materials_project.py` — retrieval (merged).
- `src/materials_triage/retrieval/rag.py` — **literature RAG (#17), MERGED (#31)**. Public surface:
  `LiteraturePassage` (frozen model), `LiteratureRAG.search(query, k=10)`, `AbstractFetcher`
  (Protocol), `OpenAlexFetcher` (live transport). Internals: `_reconstruct_abstract`, `_parse_work`,
  `_tokenize`, `_rank`.
- `tests/test_rag.py` — RAG tests (merged #31; **also locally modified** by the parallel session — WIP).
- `docs/design/0001-retrieval-rest-adapters.md` — retrieval ADR (merged #29).
- `docs/design/0002-literature-abstracts-only.md` — abstract-only ADR (merged #31).
- `notebooks/deterministic_pipeline_demo.ipynb` — MP pipeline demo (merged #29).
- `notebooks/literature_rag_demo.ipynb` — **RAG demo, merged #32 + cleanup #33**.
- `pyproject.toml` — runtime deps `pydantic>=2`, `requests>=2`, **`rank-bm25>=0.2`** (added #31);
  `notebook` + `dev` extras; `live` pytest marker (deselected by default). **Locally modified** by
  the parallel session (WIP — likely Bedrock/LLM deps).
- `src/materials_triage/agent/`, `tests/test_llm.py`, `tests/conftest.py` — **untracked WIP** of the
  parallel LLM-provider session. **Do not touch.**

## Discoveries (gotchas — most also in auto-memory; see MEMORY.md)
*(Append-only. Prior-session findings preserved; this session's appended at the end.)*
- **TDD discipline:** thin frozen models; behavior lives in functions, not models. Some happy-path
  tests are characterization (green-on-arrival) — flag them honestly.
- **Design split (user's "Way B"):** `Criterion` → `Constraint` (hard min/max gate) +
  `RankingTarget` (soft direction/weight/on_missing). Weights are **proportional, sum to 1**.
- **`identifier` vs `record_id`:** Candidate.identifier = source-returned identity; Provenance.record_id
  = per-value receipt. Coincide in single-source v1.
- **Materials Project API** is a sandboxed mirror: `X_API_KEY` header; query-id ≠ returned-id; units
  not in payload (pinned per-field in the adapter); `origins[]` = per-property provenance. [memory]
- **Retrieval = thin REST adapters** (ADR 0001), injected `http_get`, lazy `requests`, `live` marker.
  Composition scoping pushed server-side (`elements=`); numeric gate stays in `apply_hard_filters`.
- **Cloudflare bans `Python-urllib` UA (403);** `requests`/browser UA pass. Don't retry — change UA.
- **Hypothesis-layer decision RESOLVED → MEDIUM** (cited spec-deltas bridging fuzzy goal → DB
  proxies); deterministic core ≈ MP-API by design; Rich is v2. [memory: hypothesis-layer-open-decision]
- **LangGraph note:** checkpointer ⊇ #9 trace + resume; BaseStore ↦ #10 lab memory. Decide before #23.
- **pre-commit `ruff format` can ABORT a commit** by reformatting staged files (incl. notebooks) —
  re-`git add` and re-commit. Hit this twice this session on the notebook.
- **— This session (2026-06-21) —**
- **Literature RAG (#17) shipped (PR #31).** Abstract-only (ADR 0002): MP is the fact layer, the RAG
  grounds *direction* (hypothesis) + *cited claims* (synthesis); claim-framing ("authors report…")
  enforced by validator #20; capability-safety (no scraper). Decisions: include+flag missing
  abstracts (rank on title), **drop only works empty in BOTH title and abstract** (user's refined
  rule, added via the no-mistakes review fix), keep zero-relevance hits in stable order, score=0.0 at
  parse → stamped by `_rank` on frozen copies.
- **OpenAlex specifics:** abstracts ship as `abstract_inverted_index` (`{word:[positions]}`) — must
  reconstruct; ~20–40% of recent works (more overall) have `null` abstracts (Elsevier redistribution,
  editorials, datasets). `id`/`doi` are URLs (strip prefixes → `W123` / bare DOI). Keyless; set a
  `mailto` (User-Agent + `mailto` param) for the polite pool. Endpoint
  `/works?search=&per-page=&select=`.
- **Formula-aware tokenizer + v2 debt.** `_tokenize` = `[a-z0-9]+(?:\.[a-z0-9]+)*` keeps integer AND
  decimal-subscript formulas (TiO2, La0.6Sr0.4CoO3) and decimals (3.5) intact; hyphens/punctuation
  split. v1 ceilings deferred to v2: case-folding (Co vs CO), no synonymy, whole-formula-only, no
  stemming. [memory: rag-tokenizer-v2-todo]. Note unicode-minus formulas (CuIn1−xAlxSe2) do NOT
  survive — chose the perovskite demo example because La0.6Sr0.4CoO3 does.
- **BM25 small-corpus gotcha:** with N=2 docs a term in 1 doc gets IDF=0 (log 1) → all-zero scores.
  Ranking tests need ≥3 docs to discriminate. Real pools (pool_size=200) are unaffected.
- **null-title bug caught by no-mistakes review (real, not nitpick):** `_parse_work` indexed
  `work["title"]` into a required field; live OpenAlex returns `title: null` often → one record would
  crash the whole batch, contradicting "keep & flag." Fixed: `work.get("title") or ""`, guard null
  author `display_name`, drop only both-empty. Offline fixtures masked it — **test ragged identity
  fields, not just ragged properties.**
- **DFT/XC-functional comparability (V2 concern, Kian on PR #29):** MP values are DFT/XC-functional-
  dependent — carry uncertainty and aren't cross-functional rankable; v1's weighted ranker assumes
  comparability. v2: tag functional in provenance (MP `origins[]`), restrict/flag cross-functional
  ranking, surface DFT uncertainty as a caveat. [memory: dft-xc-functional-comparability-v2]
- **no-mistakes from a worktree:** `axi run` can't bootstrap a fresh run (maps to `rerun` → "no
  previous run"); the push-proxy hook misfires from a worktree (`invalid gate path: .`). Recovery
  that WORKED: `git push no-mistakes <branch>` (ref lands), then manual
  `no-mistakes daemon notify-push --gate <ABS bare-repo path> --ref refs/heads/<branch> --old <base>
  --new <head>` → creates a run → `axi abort` → `axi run --intent "…"` (now rerun uses the intent).
  [memory: no-mistakes-run-bootstrap]
- **LLM structured-output flakiness (parallel session):** ~15% of single Bedrock calls emit malformed
  Hypothesis output (3 modes), all gate-rejected → a generic retry loop (#23) is load-bearing.
  [memory: llm-structured-output-flakiness]
- **Handoffs in untracked `docs/handoffs/` get wiped** by parallel sessions' `git clean`/checkout —
  observed TWICE this session (within minutes). Commit the handoff (this file lives on branch
  `docs/session-handoff`) so it survives.

## Work Done (all merged to `main` unless noted)
*(Append-only history.)*
- **Sessions 1–4 (≤2026-06-20):** Provenance/PropertyValue/Candidate/Constraint/RankingTarget/
  TriageSpec/TriageResult models (#13–#21); `normalize` (#22); `apply_hard_filters`+`missing_data`
  (#24); `on_missing` (#25); weighted-average ranker (#26); SourceAdapter+stubs (#27); Materials
  Project adapter + composition scoping (#28); pre-commit ruff hook (#23). Literature RAG (#17)
  DESIGNED. Live MP demo notebook + ADR 0001 built (untracked, later PR #29).
- **Session 5 (2026-06-21) — literature RAG #17 built, shipped, demoed:**
  - Built #17 TDD in a worktree (`feat/literature-rag`): unit A `_reconstruct_abstract` → B
    `LiteraturePassage`+`_parse_work` → C `_rank`+formula tokenizer → D `LiteratureRAG.search`+
    `AbstractFetcher` → E live `OpenAlexFetcher`. Added `rank-bm25`. ADR 0002. ~111 offline tests +
    1 live (green vs real OpenAlex).
  - Validated via **no-mistakes** (run `01KVNAX4…`): review caught the null-title batch-crash bug →
    fixed (keep either, drop both-empty) + author-null guard + regression tests; document gate's
    "ADR 0001 missing" was approved as-is (it was in open PR #29). **Merged as PR #31** (squash →
    `a8be134`).
  - Merged the held docs: **PR #29** (MP demo + ADR 0001 → `4171bf8`).
  - Built the **OpenAlex RAG demo notebook** → **PR #32** (`7c27583`); then **cleanup PR #33**
    (`382095d`): cleared baked outputs, reset to k=8 perovskite, fixed representative-run cell.
  - Ran `/sync-main` repeatedly; pruned all merged branches + worktrees. Saved memories:
    rag-tokenizer-v2-todo, dft-xc-functional-comparability-v2.

## Status
- **Working / merged:** `main` @ **`382095d`**. Full deterministic vertical slice is live: data
  models + scoring + ranking + MP retrieval + **literature RAG over OpenAlex** + two demo notebooks
  + ADRs 0001/0002. Task list **#1–#17 effectively done** (incl. #16/#17 merged); #9/#10 parked.
- **In progress (NOT mine):** the LLM provider (#21) + hypothesis-layer work by a parallel session
  on `feat/llm-provider` / `feat/hypothesis-discriminated-union`. Uncommitted edits to
  `pyproject.toml`, `core/hypothesis.py`, `tests/test_hypothesis.py`, `tests/test_rag.py`; untracked
  `src/materials_triage/agent/`, `tests/test_llm.py`, `tests/conftest.py`. **Leave intact.**
- **Blockers / known issues:** none for merged work. Open threads: LangGraph decision (gates
  #9/#10/#23); #31 (ExcludedCandidate element/composition drop reasons) still deferred; v2 debts
  logged (tokenizer, DFT/XC). Hazard: untracked handoffs get wiped by parallel `git clean` — this
  handoff is committed on `docs/session-handoff` to survive.

## Next Steps
1. **Decide what to pick up next** (don't start coding until told). Candidates per build order:
   guardrails **#18 input gate → #19 trust boundary → #20 output validator** (these make the RAG/LLM
   output safe), or continue the **LLM layer #21→#22→#23** (coordinate with the parallel session to
   avoid collision).
2. **Before #23:** settle the **LangGraph** decision (checkpointer/BaseStore ↦ #9/#10) and plan the
   **structured-output retry loop** (load-bearing per the ~15% Bedrock flakiness finding).
3. **When coding:** branch off `main` in a **worktree** (parallel sessions!), run tests with
   `PYTHONPATH="$PWD/src" python -m pytest -q` (+ `-m live` for live OpenAlex/MP), `ruff check src tests`.
   Ship via PR; squash-merge in the GitHub UI; then `/sync-main`. **Commit handoffs** so they persist.
4. **Eventually:** ExcludedCandidate element/composition reasons (#31); the v2 items (tokenizer,
   DFT/XC); renderers (#25/#26), CLI (#27), eval (#28), design note, README.

## Context for Next Session
- **Branch (this handoff):** committed on `docs/session-handoff` (off `main` @ `382095d`) in a
  worktree, because the live main checkout was occupied by a parallel session and untracked handoffs
  kept getting wiped. `main` == `origin/main` @ `382095d`.
- **Uncommitted (not mine):** the parallel session's `feat/llm-provider` /
  `feat/hypothesis-discriminated-union` edits + untracked `agent/`/`test_llm.py`/`conftest.py`. Don't disturb.
- **How to verify merged state:** in a clean worktree off `main`: `python -m pytest -q`,
  `ruff check src tests`. RAG live check: `OPENALEX_MAILTO=… python -c "from
  materials_triage.retrieval.rag import LiteratureRAG, OpenAlexFetcher; print(len(LiteratureRAG(
  OpenAlexFetcher()).search('perovskite oxygen evolution', k=5)))"`.
- **Dependencies:** `X_API_KEY` (MP sandbox); `OPENALEX_MAILTO` optional (polite pool); Bedrock IAM
  creds for the LLM layer (#21+).
- **Git workflow:** `main` protected, signed commits (`git commit -S`, SSH), squash-merge via GitHub
  UI, then `/sync-main`. pre-commit `ruff format` can abort a commit → re-add + re-commit.
- **Auto-memory (persists):** see `MEMORY.md` — entries incl. hypothesis-layer (RESOLVED→MEDIUM),
  ranking-weight-normalization, orchestrator-23-carryforward, llm-structured-output-flakiness,
  dft-xc-functional-comparability-v2, rag-tokenizer-v2-todo, worktree-pythonpath, no-mistakes-run-bootstrap.
- **Task list:** #1–#17 done (RAG #17 merged #31); #9/#10 parked; #18–#30 remain; #31 (element/
  composition drop reasons) deferred.
