# Session design log — fast-track GUI + hypothesis-phase improvements

> **Context:** built on the throwaway `feat/fast-track-wire-guardrails` branch as a
> vertical demo of the wiring — not production-merged code. Date: 2026-06-23.
> Each entry is **Decision → Why**, grouped by feature, with the commit that landed it.
>
> Two honest footnotes carried throughout:
> - The **msgpack-unregistered-type** warning is pre-existing (the LangGraph
>   checkpointer serializing our pydantic models — round-trips today, documented).
> - This is a demo branch; it exercises the design, it is not merged to `main`.

---

## A. Local GUI (`0d1962a`)

- **FastAPI + minimal HTML, not Streamlit/Gradio.** → Lives under the existing
  `server/` hosting layer and is closer to the eventual public web app; full
  control over routes/markup.
- **Live pipeline (Bedrock + MP), not offline fakes.** → Real results for the demo.
- **Render output in a monospace `<pre>`, not markdown→HTML.** → The renderer emits
  whitespace-aligned shortlist columns; `pre` preserves the alignment with zero deps.
- **Wrap the existing `cli.triage` / `render_run` seam; no core change.** → The
  pipeline was already factored; the GUI is a thin front door.
- **Edge-only optional `gui` extra + lazy imports.** → The pure core must never
  depend on the web layer (repo architecture rule).
- **No tests on this branch.** → Throwaway branch; verified manually instead.

## B. Live step progress (`8019048`)

- **Server-Sent Events, not WebSocket or polling.** → Progress is one-directional
  (server→client); SSE needs minimal JS (`EventSource`) and no extra machinery.
- **Show step name + status checklist, not step + stat.** → Clean, low-risk, reads
  unambiguously as progress.
- **Drive via `orchestrator.stream(stream_mode="updates")`, one frame per completed
  node; labels derived from `WORKFLOW_STEPS`.** → Single source of truth — the
  checklist can't drift from the real graph.
- **Keep the synchronous POST as a `<noscript>` fallback (progressive
  enhancement).** → Robust if JS is off.

## C. Interactive spec gate (`73f9d70`)

- **Surface the *existing* `interrupt()`, don't build a new mechanism.** → The HITL
  spec gate already existed (the CLI just auto-accepted it).
- **Three actions: approve / edit / regenerate; regenerate = fresh run.** → The
  graph is linear with no back-edge to `hypothesis`, and the LLM is
  non-deterministic, so "regenerate" must restart.
- **Edit via a JSON textarea, not a structured form.** → Full coverage of every
  spec field with ~no extra UI; right fit for a throwaway demo.
- **In-process registry keyed by `thread_id`; SSE pauses at the gate, a separate
  `/triage/resume` endpoint continues.** → SSE is one-directional, so the approval
  needs a client→server channel; single-process demo makes an in-memory registry fine.
- **Invalid edit re-opens the gate (run left parked).** → Retryable and honest.

## D. Input gate hardening (`171c1f0`)

- **Allowlist-first deterministic scope check, not an LLM classifier.** →
  Deterministic, injection-resistant, matches the documented design, no LLM call.
  Tradeoff accepted: odd phrasing with zero domain keywords could be wrongly refused.
- **Wet-lab synthesis via a targeted verb+object regex, not the bare word
  "synthesize".** → "synthesize" is polysemous ("synthesize the literature") and its
  past tense is a legit screening property ("synthesized below 400 °C").
- **Allowlist signal = curated domain terms + chemical-formula regex; *not* raw
  element symbols.** → 1–2 letter symbols (In, As, No, Be) collide with common
  English words.
- **Stem matching (leading word boundary).** → Catch plurals/inflections
  ("oxide**s**", "composition**s**").
- **Every refusal carries a capabilities blurb.** → Polite redirect telling the user
  what the agent *can* do.

## E. RAG wired into the hypothesis (`85c5c68`)

- **RAG as an optional injected seam (`rag=None` preserves old behavior).** →
  Backward-compatible and offline-testable.
- **Soft-degrade on RAG error/absence, don't fail the run.** → RAG is *grounding,
  not ground-truth* (the numeric layer is still Materials Project).
- **Reuse the existing `citations` field; inject passages with citation handles; no
  schema change.** → The proposal schema already modeled citations.
- **Fence passages in `wrap_untrusted`.** → Retrieved abstracts are untrusted DATA
  (the trust boundary), same as the user goal.
- **`k = 5` passages.** → Enough to ground without flooding the prompt out of attention.

## F. LLM query-gen + live RAG trace (`354c976`)

- **Add a real LLM query-rewrite step, not just visualize the existing flow.** → A
  focused query beats dumping the raw goal into BM25/OpenAlex; completes the
  goal→query→RAG→response→prompt chain.
- **Live data from the real run, not canned.**
- **Separate `QueryProvider` (plain-text Bedrock), not a reuse of
  `HypothesisProvider`.** → Different output type (a query string vs. a structured
  Hypothesis).
- **Record the interaction as plain JSON dicts on a `rag_trace` state channel.** →
  Keeps the checkpoint channel simple, avoids more msgpack-unregistered-type
  warnings, and is JSON-safe for the SSE frame.
- **Emit the `rag_trace` SSE frame when the hypothesis node completes (read from the
  stream chunk delta).** → Surfaces the grounding live, mid-run, before the gate.

## G. Fidelity gate + facet seeders (`592227a`)

- **Auto-inject missing facets, not flag-and-retry.** → The linear graph has no
  retry edge back to `hypothesis`; auto-injection is deterministic/reproducible, and
  the human still approves the seeded spec at the gate (editable backstop).
- **Run it in `_spec_build_node` after `compile_spec`, before the interrupt.** → The
  human approves the *already-seeded* spec.
- **Seed *and* enforce locally, not seed-only or push server-side.** → Honest
  end-to-end (no silent drops), fully offline-testable. Cost: a formula parser + two
  new exclusion reasons.
- **Enforce `excluded_elements`/`max_nelements` with a local `apply_element_filters`
  + `formula_elements` parser.** → They were validated-only; `Candidate` carries only
  a formula string; a local filter works regardless of what the source pushes.
- **Two new `ExcludedCandidate.reason` values (`excluded_element`,
  `too_many_elements`).** → Element/count drops must be recorded with a structured
  reason — never silent.
- **Toxic set = committed RoHS/REACH + radioactive; oxidation-state-dependent
  elements (Cr, Ni, Co, Ba…) flagged in a caveat, not excluded.** → Composition is
  the workable "toxicophore" in materials science; Cr(VI)-vs-Cr(III) and leachability
  aren't resolvable from public DFT data.
- **Pure logic in `core/fidelity.py` (no LLM/IO); literal stem detection, negation
  unhandled.** → Testable, matches "core = deterministic domain"; conservative, with
  the human gate as backstop.
- **Coherence guards (required-from-anion never also excluded; `max_nelements ≥
  required count`).** → Always produce a spec that passes `TriageSpec`'s own validators.
- **Measured effect:** spec fidelity to stated hard requirements went 0/5 → 5/5
  across live runs; toxic-exclusion variance collapsed from 0–6 elements to ~29–30.

## H. Critic agent for off-goal objectives (`9dfa505`)

- **Proposer→critic LLM-judge, not a deterministic cue-map.** → More flexible; the
  LLM judges relevance against the goal text.
- **Renormalize survivors to sum to 1.** → Preserve relative emphasis; the spec
  requires weights sum to 1.
- **Run the critic in the *hypothesis* node, not `spec_build` or a new graph node.**
  → `spec_build` re-executes on every spec-gate resume (would double the critic's LLM
  call); operating on ranking *proposals* lets `compile_spec` auto-renormalize; avoids
  changing `WORKFLOW_STEPS` (which would touch the GUI checklist and tests).
- **Prune ranking *proposals*, not compiled targets.** → The rationale lives on the
  proposal and is lost after `compile_spec`; pruning proposals gets renormalization
  for free.
- **Guard: never drop *all* ranking targets.** → An empty ranking leaves the
  shortlist unordered; a critic that disowns every objective isn't trusted.
- **Soft-degrade on critic error.** → Best-effort, like RAG.
- **Add a prompt layer too (rationale must name a goal phrase; don't invent).** →
  Defense-in-depth — cut invention at the source (measured ~5/5 → 1/5 before the
  critic even runs).
- **Strict critic output models (`extra="forbid"`).** → LLM-output models stay strict
  to catch flaky structured output.
- **Measured effect:** off-goal ranking targets in the final spec went ~5/5 → 0/5
  across live runs (prompt cut invention to 1/5; critic dropped the straggler), no
  false drops.

### H′. Critic extended to redundancy (#7) and bound sanity (#6)

- **Reuse/extend the existing critic rather than hardcoded tables — chose approach
  C over committed `PROPERTY_RANGES`/`PROXY_GROUPS`.** → A static physical-range or
  proxy-group table smuggles *invented domain facts* into the deterministic layer —
  the exact thing the architecture forbids. Redundancy ("are these two the same
  thing?") and bound sanity are *reasoning* judgments, the critic's wheelhouse.
- **#7 redundancy folds into the existing keep/drop mechanism.** → "Redundant with a
  kept objective measuring the same property" is just another reason to drop; prune +
  `compile_spec` renormalize already handle it. One sentence added to the prompt.
- **#6 bound sanity is flag-only (advisory), never auto-applied.** → Judging a bound
  "too loose" requires physical-range knowledge the LLM only *approximates* — a mild
  fact-recall risk. Quarantine it: the critic emits `BoundFlag`s surfaced to the
  human, but auto-changes stay reasoning-only (ranking drops). A hallucinated flag is
  then harmless (a note, not a silent rewrite).
- **Implementation:** `RankingCritique` gains `bound_flags: tuple[BoundFlag, ...]`;
  the critic prompt now shows constraints too and asks for (relevance + redundancy)
  verdicts plus bound flags; flags ride `rag_trace.bound_flags` and render in the GUI
  "critic review" stage. Verified live: dropped a redundant stability proxy and
  flagged a loose 12 eV band-gap ceiling, both with sound reasons.

## Cross-cutting

- **Every new LLM/RAG component is an injected seam (provider pattern).** →
  Offline-testable and consistent with the existing adapter/provider design.
- **Heavy deps (FastAPI, langchain-aws, requests) stay lazy/edge-only.** → Importing
  the pure core never needs them.
- **Per-feature signed commits to the feature branch, never `main`.** → Repo workflow
  (signed-commit branch protection).
- **Verify offline *and* live before each commit; for fidelity/critic, measure with
  N live runs.** → The LLM is flaky; single runs don't show the variance the harness
  fixes.

---

## Problem-status snapshot (original 9-problem hypothesis-phase diagnosis)

| # | Problem | Status | Fix |
|---|---------|--------|-----|
| 1 | "oxide" silently dropped (no `require O`) | ✅ Fixed | fidelity gate seeds + server-side enforce (`592227a`) |
| 2 | "simple compositions" dropped (no `max_nelements`) | ✅ Fixed | fidelity gate seeds + `apply_element_filters` (`592227a`) |
| 5 | invented ranking target (`bulk_modulus`) | ✅ Fixed | prompt + critic agent → 0/5 off-goal (`9dfa505`) |
| 9 | toxic list arbitrary & non-deterministic | ✅ Fixed | committed RoHS/REACH set + caveat (`592227a`) |
| 7 | redundant stability proxies | ✅ Fixed | critic drops the duplicate proxy (relevance/redundancy verdict), renormalized |
| 6 | loose/meaningless thresholds (`band_gap` max 12) | 🟡 Addressed (advisory) | critic emits advisory `bound_flags` at the gate; flag-only by design (no silent rewrite of physics) |
| 4 | citation theater (hypothesis citations unverified) | 🟡 Partial | critic uses cited/uncited signal; **no hard validation** of hypothesis citations yet |
| 8 | query-gen lossy | 🟡 Partial | query-gen added + stable; dropped facets now covered by the fidelity gate; **multi-query not built** |
| 3 | RAG off-topic (perovskite-PV flood) | ❌ Open | needs multi-query + domain-filter + relevance-gate |
