# ADR 0005 — Hosting topology, billing & the step cache

**Status:** Accepted · **Date:** 2026-06-22 · **Scope:** how the agent is hosted as a public web
app — the frontend/backend split, the deployment topology, the billing/auth model, the
request/stream protocol (incl. the HITL spec gate), the storage layers, and the content-addressed
**step cache** that powers cheap re-runs, idempotency, and a cross-attempt diff. Builds on the
LangGraph orchestrator (ADR 0003) and the guardrail architecture (ADR 0004).

## Context

`v1` is an installable package + CLI (a locked decision) over a traced LangGraph state machine
(ADR 0003). The next surface is a **public web app**: a chat UI with a right-side banner showing the
goal and the nine workflow steps live (Cowork-style), reachable **without sign-in** for an easy demo,
rate-limited, with signed-in users getting model choice + a higher limit. Three questions had to be
settled before any of that is built: (1) what is frontend vs backend; (2) where it runs and how
usage is paid for, given the LLM is on AWS Bedrock; (3) how runs are stored so a re-run is cheap,
idempotent, and diffable against the previous attempt.

The constraints that drive the answers: secrets (Bedrock IAM creds, `X_API_KEY`) can never reach a
browser; the spec-building step is a real `interrupt()` that **pauses the run server-side** (ADR
0003), possibly for minutes; runs are multi-second (several LLM + API calls); and the data is
**public-only**, which turns out to be load-bearing for the cache design.

## Decision

### 1. Frontend vs backend

- **Frontend (`web/`, browser):** the chat thread, the right-side **steps banner** (a live render of
  the `TriageRun` trace / checkpoint stream), the sign-in button, the model dropdown (greyed for
  anon), the spec-gate panel, and the PI / audit views. It *renders* a stored run and computes
  nothing scientific.
- **Backend (`server/`):** the entire agent (orchestrator, gate, retrieve/filter/rank, synthesis,
  validator, checkpointer, lab memory) plus auth, rate-limiting, model policy, and metering. It is
  the only thing that touches secrets and holds the paused-run state.

### 2. Topology & billing

- **Topology: monolith on AWS now (Option A), designed to split into FE+BE later (Option B).** One
  cloud = **AWS**, because the LLM is already on Bedrock — the backend assumes an **IAM role** (no
  long-lived keys). One container (Fargate / App Runner) serves the API and the built frontend
  assets. Serverless (Lambda) is **rejected for v1**: long, stateful, *pausable* runs fight the
  15-minute cap and statelessness.
- **Billing: Pattern 1 — pooled account + meter.** Bedrock has no paste-able user API key (auth is
  AWS IAM / SigV4), so the platform owns **one** Bedrock account, calls it for everyone, and meters
  per-user. "Charging their own account" means an account **on our platform**, not their AWS bill.
  - **Anonymous:** no login (session token), fixed model, tight per-IP rate limit (caps *our* bill).
  - **Signed-in:** identity, model choice, higher per-user rate limit, optional credits.
  - **Deferred:** Pattern 4 (paste an Anthropic key → a second `ChatAnthropic` transport) for true
    BYO-spend; Pattern 2 (cross-account IAM `AssumeRole` with `ExternalId`) for enterprise BYO-AWS.

### 3. Repo layout: monorepo

No separate repos. `src/materials_triage/` stays the **pure, offline-testable** core (locked
decision — no web framework in it). `server/` and `web/` are **siblings that import the core**;
`server/` calls `build_orchestrator(...)`, never reimplements it. Web deps live behind an optional
`[server]` extra. `web/` and `server/` deploy to different targets from one repo, and `web/` can be
promoted to its own repo later if a frontend team appears.

### 4. Request/stream protocol

- **SSE for step events** (one-way server→client stream that feeds the banner); **POST for actions**
  (start a run, resume with a spec). WebSockets buy nothing here.
- **`run_id == thread_id`** so LangGraph's checkpointer keys the HITL pause and crash-recovery resume
  on the same id.
- The **spec gate is a real pause**: `interrupt()` persists state; the browser shows the draft spec;
  `POST /runs/{id}/resume` feeds a `Command(resume=...)` back into the same thread. Manual spec fields
  are pydantic-validated server-side (the input gate for manual fills, ADR 0004).
- **Both views read one stored `TriageRun`** (`view=pi` / `view=audit`) — never re-running the
  pipeline (this is the fix for the fast-track "render called the pipeline twice → two LLM runs" bug).
- The **auth → rate-limit → model-policy** chain runs as middleware *before* the graph starts, so a
  429 never burns a Bedrock call.

Minimal endpoint surface: `GET /session`, `POST /auth/...`, `POST /runs`, `GET /runs/{id}/events`
(SSE), `POST /runs/{id}/resume`, `GET /runs/{id}?view=pi|audit`.

### 5. Storage layers

Different lifetimes, not one box. Demo collapse: **Postgres** for everything durable + **Redis** for
rate buckets (single-instance demo can start with SQLite + in-memory buckets).

| Data | Lifetime | Demo | Real | Note |
|---|---|---|---|---|
| Checkpointer (live graph state) | transient, framework | `MemorySaver` | Postgres / DynamoDB | Must be durable+shared once >1 instance, or the HITL pause is lost on restart. |
| `TriageRun` trace (audit export) | durable, ours | SQLite / JSON | Postgres JSONB / S3 | What `view=audit` renders; one write path (ADR 0003). |
| Lab memory (saved specs) | durable, per identity | SQLite | Postgres | Keyed per user / per session. |
| **Step cache** | durable, content-addressed | with traces | Postgres / Redis | The cross-attempt check — see §6. |
| Users / auth | durable | — | Postgres | Only when sign-in exists. |
| Rate buckets | ephemeral, TTL | in-memory | Redis | Shared across instances. |
| Metering (tokens/user) | durable | — | Postgres | Pattern-1 billing input. |

### 6. The step cache (content-addressed)

The cache delivers **all three** of: cost/reproducibility reuse, an idempotency short-circuit, and a
user-facing cross-attempt diff.

**Cache key:**

```
key(step) = H(
    step_name,
    resolved_inputs(step),   # RECURSIVE — transitively folds in goal → spec → …
    source_version,          # an MP / OpenAlex data refresh busts the cache
    llm_salt if step is LLM else ∅   # default 0 = repro; force-fresh bumps it
)
```

- **Recursive inputs are non-negotiable**: keying only on a step's immediate args lets a changed goal
  serve stale candidates (the classic cache-invalidation trap).
- **`source_version`** ties the cache to the underlying data, not just the request — and dovetails
  with the DFT/XC-functional provenance caveat (version the data, version the cache).

**LLM steps cached for repro, with a force-fresh toggle (`llm_salt`):**
- Default salt → `hypothesis` / `synthesis` are cache hits (same goal → same narrative; good for
  audit). Force-fresh bumps the salt so those steps miss.
- Force-fresh `synthesis`: only synthesis misses; the deterministic core still hits — cheap "rewrite
  the explanation."
- Force-fresh `hypothesis`: new hypothesis → new spec → downstream **inputs change**, so the
  deterministic steps re-run **automatically and correctly** (their key changed; not a special path).

**Idempotency short-circuit:** same goal, no force-fresh, all keys hit → do not start a graph run.
Record a lightweight `attempt` pointing at the prior `result_run_id`; skip metering and rate-burn.

**Cross-attempt diff:** attempts are grouped under a **thread**; on completion the new `TriageResult`
is diffed against the previous attempt's — entered / dropped candidates, rank-position deltas,
exclusion-reason deltas ("ZnO: now `below_min`"). Pure set/rank math, no LLM, fully traceable; it is
the banner's "what changed" line.

**Global, shared cache (the public-data dividend):** because the data is public-only, `step_cache`
(including LLM-step outputs) is **shared across all users** — user B's identical goal reuses A's
cached results for free, and B is not rate-limited for a cache hit. Per-user scoping applies only to
`thread` / `attempt` / lab-memory, never the cache. **No TTL** — correctness comes from
`source_version`; entries are age-evicted only to reclaim space.

**New storage shape (introduced by the hosted UI):**

```
thread (id, owner)                       owner = user_id | anon_session
attempt (id, thread_id, ordinal, goal, spec_hash, force_fresh_flags, result_run_id)
step_cache (key, value_blob, source_version, created_at)   # content-addressed, global
triage_run (run_id, json)                # audit export, view=pi|audit
```

`v1`'s resume was **crash-recovery only** (no thread grouping); the hosted UI adds the
`thread` / `attempt` entities.

## Consequences

- The pure core is untouched; `server/` is a thin policy + transport shell over `build_orchestrator`.
- The banner is "free" — it is a live render of the checkpointer's step stream + the `TriageRun`
  export, both of which already exist (ADR 0003).
- The checkpointer **must** move off `MemorySaver` to a durable, shared backend before the app scales
  past one instance, or paused HITL runs are lost on restart/redeploy.
- Cache correctness hinges on two things being right: recursive input hashing and a real
  `source_version` from each adapter. Getting either wrong silently serves stale science.
- LLM-step caching makes runs reproducible by default; the force-fresh toggle is the explicit escape
  hatch for "give me a different idea."

## Alternatives considered

- **Serverless (Lambda):** rejected for v1 — fights stateful, pausable, streaming runs.
- **Separate repos for `server/` and `web/`:** rejected — coordination/contract-drift cost with no
  benefit for a solo demo; monorepo can still deploy each separately.
- **Per-thread (non-shared) LLM cache** for a "my run is mine" feel: rejected — the data is public,
  so global sharing is a pure cost win with no privacy cost.
- **TTL on all cache entries:** rejected in favor of `source_version`-driven invalidation + age-based
  space eviction, so reproducibility is not lost to an arbitrary clock.

## Resolved questions (this ADR)

1. **Force-fresh granularity** → a **global toggle first**, per-step later.
2. **Cache retention** → **no TTL**; correctness via `source_version`; age-evict for space.
3. **Cache sharing** → **global, shared across users, including LLM steps** (public data).
