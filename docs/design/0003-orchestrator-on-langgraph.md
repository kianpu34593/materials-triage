# ADR 0003 — Orchestrator built on LangGraph

**Status:** Accepted · **Date:** 2026-06-21 · **Scope:** orchestrator (#23), and the trace/resume
(#9) and lab-memory (#10) concerns it subsumes

## Context

The orchestrator runs the nine-step workflow — gate → spec-build → hypothesis → retrieve → filter →
rank → synthesis → output-validate → render — as a **traced state machine** (a deep-plan locked
decision: "execution = traced state machine, not an autonomous tool-calling loop"). That locked
decision also said *no framework* — hand-roll the state machine — and parked two dependent pieces
behind it: **#9** (a `TriageRun`/`Step` trace persisted to `runs/<run_id>.json`, replayable via
`resume --from`) and **#10** (a lab-memory store of saved specs).

Two v1 requirements were then confirmed that the hand-rolled plan had treated as optional:

1. **Real `resume --from`** — re-run from any step, reusing cached upstream results, after a crash
   or a knob-tweak.
2. **Human-in-the-loop at the spec-building gate** — pause mid-run, surface the LLM-recommended
   spec (including normalized ranking weights) to the user, accept their edit, and continue.

These are exactly checkpoint/resume and a pause-resume interrupt — tested, non-trivial machinery.
Hand-rolling them means writing our own checkpoint serializer, step cache, and pause/resume protocol:
**reinventing storage and control flow a framework already provides.** The choice was: (a) **build
the orchestrator on LangGraph** (`StateGraph` + a checkpointer + `interrupt()` + `BaseStore`), or
(b) **hand-roll** the traced state machine as the deep plan originally locked.

## Decision

**Build #23 on LangGraph**, reversing the deep plan's "no framework" sub-decision (the "traced state
machine, not an autonomous loop" decision *stands* — LangGraph is how we implement a deterministic,
linear, traced state machine, not an agentic loop). The nine steps become graph nodes wired in a
fixed linear edge order; a **checkpointer** persists execution state after every super-step; an
**`interrupt()`** at the spec-build node is the human gate; a **`BaseStore`** holds lab memory.

Critically, the durable audit artifact and the checkpointer are **two different jobs, not competing
stores**:

- **Checkpointer = live execution state** — framework-owned, transient, the substrate for
  `resume --from` and crash recovery. We do not define its schema.
- **`runs/<id>.json` = durable audit report** — *ours*, long-lived, the thing `view=audit` renders.
  It is a **read-only export derived from checkpoint history** (`get_state_history()`), not a second
  write path. **One write path (the checkpointer) + one derived read-model.**

## Rationale

- **`resume`/HITL are precisely the framework's primitives.** A checkpointer is durable
  step-by-step state with replay; `interrupt()` is a first-class pause-for-human-input that survives
  a process restart. Hand-rolling both — correctly, with crash safety — is the bulk of #9 and the
  spec gate, and it is exactly what LangGraph has already tested.
- **One mechanism subsumes three parked/open items.** The checkpointer ⊇ **#9** (the `TriageRun`
  trace *and* `resume --from`); `BaseStore` ⊇ **#10** (lab memory); `interrupt()` ⊇ the
  spec-building human gate. Three bespoke subsystems collapse to configuration of one.
- **The graph is linear, so the audit export is thin.** Because the edge order is fixed
  (gate→…→render) the LangGraph super-step ↔ our named-step mapping is ~1:1. The exporter walks
  `get_state_history()` and reads each snapshot's `.values` / `.metadata.writes` into a
  `TriageRun`/`Step`. No bespoke trace-writing scattered through the nodes.
- **The structured-output retry loop has a home.** The measured ~15% LLM schema-flakiness
  (malformed `Hypothesis` output, gate-rejected) needs a capped retry that re-invokes the provider
  and feeds the malformed output back. This is a node concern — a custom retry node (rather than a
  blanket `RetryPolicy`, so we retry *only* on pydantic `ValidationError`, not on infra errors).
- **In-family dependency.** We already depend on `langchain-aws` (`ChatBedrockConverse`); `langgraph`
  is the same ecosystem — no new vendor surface, and the provider already returns validated pydantic.
- **Mockability preserved.** The deterministic nodes wrap existing pure functions
  (`apply_hard_filters`, `rank_arithmetic_mean`/`rank_geometric_mean`) and the injected-seam adapters/provider; the LLM provider's
  `complete` seam and the source adapter's `http_get` seam still make the whole graph offline-testable
  with a `MemorySaver`. LangGraph does not force network or AWS into construction.

## Trade-offs (accepted)

- **Reverses a locked decision** (hand-rolled, framework-free). Justified: the two confirmed v1
  requirements (resume + HITL) move the cost-benefit decisively, and a framework-free version would
  re-implement the same primitives with more bugs. This ADR is the record of that reversal.
- **Design discipline — checkpoints persist only typed graph state.** The checkpointer captures
  exactly what is routed through the graph's typed state channels. So **graph state must equal our
  domain pydantic state** (one channel per step) or the audit export silently loses provenance, the
  excluded-set + drop reasons, missing-data flags, and citations. This constraint shapes slice 2
  (the state model) and is the main thing that can go wrong.
- **A framework to learn / pin.** LangGraph's checkpoint internals (channel versions, serializer
  format) are opaque, but we never depend on them — our durable artifact is the exported JSON in our
  own schema, and the checkpoint DB is treated as ephemeral.
- **Revises two planned modules.** `core/run_trace.py` is no longer a bespoke trace store — it
  becomes the checkpoint→`runs/<id>.json` **exporter**; `memory/store.py` becomes a thin
  **`BaseStore` wrapper**, not a hand-rolled persistence layer.

## Alternatives considered

- **Hand-rolled traced state machine (the original locked decision).** A dict-of-steps with a
  custom JSON checkpoint and a bespoke pause/resume protocol. Rejected for v1 now that resume + HITL
  are in scope: it reinvents a checkpointer and an interrupt with more surface area to get crash
  safety, partial-resume, and serialization right. Would have been defensible had resume/HITL stayed
  out of v1.
- **Autonomous tool-calling agent loop.** Rejected by the deep plan and unchanged here — the
  workflow is a fixed, auditable pipeline; an LLM-driven control loop is neither traceable nor
  honest-by-construction. LangGraph is used in its *static-graph* capacity, not its agent capacity.

## Consequences

- New runtime dependency `langgraph`; the orchestrator lives in
  `src/materials_triage/agent/orchestrator.py` as a `StateGraph` compiled with a checkpointer.
- `core/run_trace.py` is the checkpoint-history→`TriageRun`/`Step`→`runs/<id>.json` exporter
  (audit-shaped); `memory/store.py` wraps a LangGraph `BaseStore`.
- The spec-build node calls `interrupt()` to surface the recommended spec — including the
  **weight-normalization confirmation** (a carry-forward debt: normalized ranking weights are
  resurfaced to the human to confirm) — and the orchestrator wraps `compile_spec`'s raw
  `ValidationError` for the retry loop / human (the second carry-forward debt).
- Build proceeds as TDD slices (tracer-bullet compiling graph → typed state model → deterministic
  nodes → retry node → `interrupt()` spec gate → exporter → `resume --from` → `BaseStore`), one at a
  time per the collaboration rules.
- Feeds the execution-model section of the full design note (#29).
