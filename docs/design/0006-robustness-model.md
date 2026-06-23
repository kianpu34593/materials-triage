# ADR 0006 — Robustness model

**Status:** Accepted · **Date:** 2026-06-23 · **Scope:** how the agent stays available and
recovers from failure — the fault-tolerance, redundancy, recovery, degradation, and
isolation/observability policies that turn today's scattered error handling into one defined
reliability layer. Builds on the LangGraph orchestrator + checkpointer (ADR 0003), the
capability-by-construction safety model (ADR 0004), and the storage/idempotency decisions (ADR
0005).

## Context

The framework has five high-level requirements: **useful, honest, traceable, configurable, and
robust.** The first four are met; **robust** is not yet *defined* — it exists only as bits and
pieces. Concretely, today we have:

- **Content-conformance retries** in the `hypothesis` and `synthesis` nodes — they `except
  ValidationError` and re-prompt when the LLM returns malformed or ungrounded output.
- **HTTP timeouts** (`timeout=30`) on the Materials Project and OpenAlex calls.
- **Soft-degrade** in a few optional steps (RAG, critic, synthesis narrative omit-with-caveat).
- An **in-process `MemorySaver`** checkpointer (ADR 0003) and an audit `TriageRun` export.

What is missing is the part most people mean by "robust": surviving a **dependency fault** (Bedrock
/ Anthropic / OpenAI throttled or down; a 5xx or connection reset from any API), and **picking a run
back up after an infra problem** (a server restart mid-run). The existing retry loops do *not* help
here — they catch `ValidationError` only, so a throttle or outage raises an uncaught exception and
the run crashes. The checkpointer is in-memory, so a process restart loses every in-flight (and
every paused HITL) run.

This ADR defines robustness as a deliberate, testable layer rather than a property that emerges from
ad-hoc `try/except`.

## The load-bearing precondition: every external call is side-effect-free

All outbound calls are **reads or inference** — Materials Project queries, OpenAlex fetches, and LLM
completions. By capability-by-construction (ADR 0004) **no tool exists that writes, synthesizes, or
mutates anything**. Therefore *blind retry and failover are always safe here*: replaying a call can
never double-execute a side effect. Robustness inherits the safety model — this is why the policies
below can be aggressive.

## Decision

Robustness is five concerns. Each has exactly one home, so reliability is configured in one place
per concern, not sprinkled across nodes.

### 1. Name the two retry axes (they are different and both required)

- **Content-conformance retry** (exists): "the dependency answered, but the *answer* is malformed /
  ungrounded." Caught as `ValidationError`, fixed by re-prompting with the reason fed back. Lives in
  the `hypothesis` / `synthesis` nodes. Unchanged by this ADR.
- **Fault-tolerance retry** (new, §2): "the dependency did not answer (timeout / throttle / 5xx /
  reset)." A transport concern, independent of content. These must never be conflated — a malformed
  payload should not be retried with backoff, and a throttle should not be re-prompted.

### 2. Fault tolerance (A) — one `resilient_call` wrapper at every seam

Every outbound call is made through a single wrapper with:

- **Timeouts**: an explicit connect + read timeout on *every* call (HTTP already has 30s; the LLM
  call currently has none and must get one — botocore's internal retry is not a substitute).
- **Retry on transient faults** with **exponential backoff + full jitter**, a bounded attempt count
  (default 3–4) and a capped total wall-time.
- **Fail fast on terminal faults** — no retry, surface immediately.

Fault classification (the policy, applied uniformly):

| Class | Examples | Action |
|---|---|---|
| **Transient** | connect/read timeout, connection reset, HTTP 429 / throttling, HTTP 5xx, Bedrock `ThrottlingException` / `ServiceUnavailable` / model timeout | retry with backoff |
| **Terminal** | HTTP 4xx except 429 (auth, validation, not-found), credential errors | fail fast |
| **Content** | pydantic `ValidationError` | not this layer — see §1 |

Because calls are side-effect-free (precondition above), retry needs no idempotency key.

### 3. Redundancy / failover (B) — an ordered provider chain

- A **composite LLM seam** wraps an ordered list of backends, **each itself wrapped in A**. On a
  terminal-unavailable or an exhausted retry budget, it advances to the next backend. The seam
  contract (prompt in → validated `Hypothesis`/`Synthesis` out) is identical across backends, so
  failover is transparent to the orchestrator.
- Config: `MT_LLM_BACKENDS="bedrock,anthropic,openai"` (ordered). **Depends on the multi-backend
  switch (the planned Anthropic/OpenAI work);** once two backends exist, the chain is cheap.
- `temperature` is pinned (=0 by default) as part of the contract, so failover produces *comparable*
  outputs rather than silently changing sampling between providers.
- **Scope of redundancy:** the **LLM** is the redundant dependency. The **numeric source** (Materials
  Project) is single-source *by design* in v1 — its "failover" is the deferred v2 cross-source merge;
  until then MP is essential (§5). **OpenAlex/RAG** is optional and degrades (§5).

### 4. Recovery across restarts (C) — durable checkpointer + idempotent resume

- Replace the in-process `MemorySaver` with a **durable checkpointer** — `SqliteSaver` on the
  mounted volume for the single-instance deployment, Postgres for multi-instance (ADR 0005 storage
  layers). State then survives a process restart.
- **Register our pydantic models with the LangGraph msgpack serde** (the known serialization debt) so
  rich domain state round-trips losslessly; otherwise a future LangGraph version blocks it.
- **Resume contract:** `run_id == thread_id` (ADR 0005). On boot, or after an infra error, the
  RESTful API calls `resume(run_id)`, which re-enters at the last completed checkpoint; pass-through
  steps and the deterministic core re-run cheaply. Resuming a **completed** run returns the stored
  `TriageRun` and never re-executes (idempotent — dovetails with the ADR 0005 content-addressed step
  cache).
- This is **crash recovery, not human knob-edit rewind** (consistent with the existing resume
  decision): an infra fault re-runs the failed step reusing upstream state.

### 5. Graceful degradation (D) — essential vs optional, declared not implicit

Each node is classified, and the policy is explicit:

- **Essential** — `gate`, `retrieve`, `filter`, `rank`, `output_validate`, and `hypothesis` (no spec
  without it). After A's budget (and B's chain, for the LLM) is exhausted, the run **fails honestly**
  with a recorded reason. The numeric layer is never faked — that would break *honest*.
- **Optional** — the literature **RAG**, the ranking **critic**, and the **synthesis narrative**
  (prose, not numbers). On failure they **degrade with a caveat**: the run still returns a ranked,
  fully-cited shortlist, and the missing enhancement is flagged (the first-class missing-data
  principle). This formalizes the soft-degrade that already exists ad hoc.

### 6. Isolation & observability (E)

- **Bulkhead:** the per-call timeouts of §2 mean a hung dependency cannot hang the whole run.
- **Circuit breaker (optional):** after *K* consecutive terminal-unavailables, a provider is
  fast-failed (skip to failover) for a cooldown, so a down provider is not hammered.
- **Structured logging** of every attempt (provider, latency, retry count, fault class, outcome),
  so the audit trace shows *recoveries*, not just final success.
- **Health surface:** `materials-triage doctor` is the CLI seed; the RESTful API exposes `/health`
  (liveness) and `/ready` (credentials resolve + checkpointer reachable). Rate limiting / backpressure
  on the API ties to the ADR 0005 anonymous-usage limits.

## Consequences

- Reliability becomes a property of **one wrapper (A) + one chain (B) + one checkpointer (C)** plus a
  **declared per-node policy (D)** — auditable and testable, not scattered.
- Aggressive retry/failover is sound **only because** all calls are side-effect-free reads/inference
  (ADR 0004); robustness depends on the safety model and must not outlive it (if a write-capable tool
  is ever added, its calls are excluded from blind retry).
- Costs: retries/timeouts lengthen the latency tail; failover adds provider-config surface; the
  durable checkpointer adds a storage dependency and requires the msgpack type registration.
- `temperature` pinning becomes part of the contract (a deliberate behavior change vs today's
  provider-default sampling).

## Alternatives considered

- **Status quo — per-call ad-hoc `try/except`:** rejected. Inconsistent fault classification, drift,
  hard to test, and silently conflates the content and transport axes.
- **`tenacity` vs a thin hand-rolled wrapper:** lean hand-rolled (no new runtime dep, an injectable
  seam consistent with the rest of the codebase); `tenacity` is acceptable — decide at build time.
- **LangGraph node-level `RetryPolicy` (on `add_node`):** a viable place for transport-fault retry at
  the graph layer; complements rather than replaces A. Consider for the essential nodes.
- **Keep `MemorySaver`, just re-run from scratch on restart:** rejected — loses paused HITL runs and
  discards multi-second / multi-LLM work; cannot satisfy "pick the run back up."
- **Numeric-source redundancy now:** deferred to the v2 cross-source merge (already a deferred
  decision); MP stays single-source and essential in v1.

## Implementation phases (status at this ADR)

1. **A — fault-tolerance wrapper** · *planned next* (independent of the backend switch).
2. **C — durable checkpointer + idempotent resume** · *planned* (after A).
3. **B — provider failover chain** · *after the Anthropic/OpenAI multi-backend switch*.
4. **D / E — declared degradation policy + health/observability + API limits** · *formalize alongside
   the RESTful API*.

## Open questions

- `tenacity` vs hand-rolled wrapper; `SqliteSaver` vs Postgres for the single-instance volume;
  circuit-breaker thresholds and cooldown; per-provider timeout and retry budgets.
