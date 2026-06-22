# Ultimate design — Materials-Triage agent

> **Status:** target design for the real build. The fast-track branch
> (`feat/fast-track-wire-guardrails`) was throwaway; this is where we're going.
> Companion to [`fast-track-learnings.md`](fast-track-learnings.md) (what we
> observed) — this doc is **where we're going and why**.

## The one-line reframe

The fast-track build is **open-loop, single-objective, single-source,
single-endpoint, and assumes the database is the world.** The ultimate version is
**closed-loop, multi-objective, multi-source, capability-aware, and honest about
what it doesn't know.** Every section below is one axis of that shift.

## Load-bearing principle (unchanged)

The LLM never invents scientific facts. Tools supply every number (with
provenance); deterministic code filters and ranks; the LLM only builds the spec,
proposes hypotheses, and writes grounded narrative. Everything here *extends* this
without breaking it.

The extension that makes it real: a **provenance / trust hierarchy**, attached to
every value and respected by ranking and rendering:

```
experimental  >  hybrid/GW computed  >  PBE-DFT computed  >  literature-extracted  >  LLM-hypothesized
```

Two values from different tiers are never silently cross-ranked; the tier is
always carried and surfaced.

---

## Part A — Learnings from the fast build (carry forward)

1. **Vocabulary should be *derived* from the source's published API schema**, not a
   hand-maintained subset. MP publishes ~50 filters across two vocabularies
   (queryable filters vs returnable fields); we used ~6.
2. **The spec schema must co-evolve with the vocabulary.** Today `Constraint` =
   min/max on one numeric property + specific-symbol element rules. The API also
   filters on booleans (`is_stable`), counts (`num_elements`), categoricals,
   element-*sets*. Deriving 50 filters is useless if the spec can express 6.
3. **Prompt wording nudges spec *quality*, not *expressiveness*.** Leverage order:
   **schema > server-side filters > prompt wording.**
4. **Unrealistic candidates (H₂O) = a spec-expressiveness gap, not a prompt bug.**
   Fix = make the spec *say what the scientist meant* ("require a metal cation").
   Filters are **request-derived, never hardcoded bans** — a query that wants
   water still gets water.
5. **Synthesis is single-shot:** no literature RAG, no "public evidence," no
   caveats — all of which a realistic query asks for.
6. **DFT honesty:** MP PBE band gaps are underestimated ~30–50% and aren't
   cross-functional comparable; provenance must tag the functional.

*(Prompt-fidelity before/after evidence lives in `fast-track-learnings.md` §2.)*

---

## Part B — The database is not the world (coverage gaps)

A correct lead from the literature may be **absent from the structured DB** (never
computed, property missing, or experimental-only). The fast-track rule ("DB
supplies every number; literature is narrative only") conflates *"not in the DB"*
with *"not a candidate"* and silently drops correct answers. Resolution, from
conservative to ambitious:

| Level | Behavior |
|---|---|
| **Detect & flag (v1)** | A hypothesized/literature-strong material with no DB record is surfaced in a **"known to literature, unverified by database"** tier, *with its citation* — not dropped. Fits "missing data is first-class." |
| **Literature as a provenance tier** | Extracted numeric claims become values tagged `source=literature, confidence=lower`, **segregated** from DFT values (never one ranking axis), always cited, ideally human-confirmed. Extraction must be *located/quoted*, never generated. |
| **Gap as acquisition target (closed loop)** | "Not in the DB" becomes a signal of *what to compute or measure next* — queue a DFT calc, or propose an experiment. The coverage gap is the discovery frontier. |

---

## Part C — Ultimate-version directions

### C1. Failure handling = three classes

| Class | Example | Response |
|---|---|---|
| **Transient infra** | HTTP 429/503, timeout | retry with exponential backoff + jitter (GETs are idempotent) |
| **Fatal / config** | 401 auth, 400 bad query | **fail fast** with an actionable message (the 401 we hit) |
| **Semantic-degenerate** | 0 survivors · all `missing_data` · all tied | **re-plan loop** |

The semantic case is the architecture change. After `rank`, a quality gate
inspects the result; if degenerate it routes **back to `spec_build`** with a
**deterministic diagnosis** (the filter stage knows the binding constraint:
*"`formation_energy ≤ -5.0` eliminated 100/100 — relax it"*), capped at N
iterations. Needs **conditional edges + bounded cycles**, not a static chain. In
HITL mode it asks before relaxing; in one-shot it relaxes with a logged caveat;
if still empty it returns an honest **"no candidates satisfy these constraints."**

### C2. Full database observability (the Swiss-army-knife)

- **Schema introspection** — derive the capability surface (filters, fields,
  units, ID scheme) from the published spec (`/openapi.json`, client docstrings).
- **Multi-endpoint routing** — MP is not just `/summary`: route a dielectric query
  to the **dielectric** endpoint (`e_total`, `e_ionic`, `n`), elasticity to
  **elasticity**, etc. Know the catalog; route to the right organ.
- **Predicate pushdown** — filter server-side, not "fetch 100, drop client-side."
- **Rich provenance** — capture the DFT functional, the originating task, and
  uncertainty per value.

### C3. Ranking beyond weighted-sum

Weighted-sum (current) can't reach non-convex Pareto points, is
normalization-sensitive, and is compensatory. Alternatives:

- **Pareto / non-dominated ranking** — return the trade-off frontier (no weights);
  honest when trade-offs are the point.
- **Desirability functions (Derringer–Suich)** — per-property `d∈[0,1]` with
  **target-window** shapes, combined by geometric mean (non-compensatory). The
  principled fix for "a dielectric wants a *moderate* gap, not the widest" — what
  put H₂O on top.
- **Uncertainty-aware** — rank by probability of satisfying constraints / being
  non-dominated given DFT + missing-data error; flag low-confidence orderings.

They compose: Pareto front, then order within it by desirability.

### C4. The closed loop (= Lila's thesis)

The current pipeline is the **inner loop** (one triage turn). Wrap it in an
**outer optimization loop**: rank with current info → identify the highest-value
*uncertainty* → "gather" (another endpoint, literature, or a proposed
experiment) → update a surrogate → re-rank → repeat. The state substrate already
exists (`TriageRun` trace + lab memory); the outer loop is an **addable
controller** ranking by an acquisition function. Architect for it now; build it
later.

### C5. Other essentials (ranked by leverage)

1. **Evaluation harness** — gold queries with expected behavior (refusals,
   candidate classes, grounding, spec-fidelity), run offline with a mocked LLM.
   The top differentiator: turns *asserted* quality into *measured* quality.
2. **Honesty / uncertainty first-class** — DFT caveats, confidence on rankings,
   computed-vs-experimental, and a real **"insufficient data"** outcome.
3. **Wire the literature RAG into synthesis + a caveats field** — real OpenAlex
   citations per claim (validated by the output validator) and surfaced caveats.
4. **Chemistry-aware composition reasoning** — oxidation states / charge balance
   (`possible_species`), element classes (metals, toxic set, earth-abundance).
5. **Conversational HITL refinement** — "why was X excluded?", "show the next 10",
   "relax stability", "weight band gap higher" with live re-ranking.
6. **Reproducibility / replay** — content-addressed caching of LLM + API responses.
7. **Defensive parsing** of messy real data (the VRH-dict crash is the archetype).

---

## Part D — Doc-only: cross-source merge (know how, don't build)

The basic→advanced ladder (the bio **multi-omics** problem in materials form):

1. **Discover the schema.** Per source: OpenAPI / client / data dictionary.
   **Prefer the standard where it exists — OPTIMADE is the materials-DB lingua
   franca** (one query API across MP/OQMD/AFLOW/…); fall back to per-source adapters.
2. **Map to a common ontology + units.** Source field → canonical property + unit +
   method; resolve naming/convention/unit differences.
3. **Reconcile identity (the hard rung).** Is `mp-1234` the same material as
   `oqmd-5678`? Match on reduced formula + structure (spacegroup/lattice via
   `pymatgen StructureMatcher`). Record linkage. *Bio analog: gene-ID mapping
   across Ensembl/RefSeq/UniProt.*
4. **Merge with provenance-aware conflict resolution.** Same property, different
   values → don't average; apply the trust hierarchy, keep all values with
   provenance, present the spread **as uncertainty**. Disagreement is signal.
5. **Multi-modal integration (= multi-omics).** Join *complementary* modalities per
   material — structure (ICSD) + electronic (MP) + synthesis recipes (text-mined) +
   experimental. Each DB is a "modality"; coverage is sparse/uneven; you join on
   reconciled identity and reason over partial, heterogeneous, trust-weighted data.
   Same hard problems as multi-omics: **identity reconciliation, missingness,
   source/batch effects, trust-weighting.**

---

## Part E — System-design prep (cost / latency / caching)

> Not a build item — **interview system-design prep**, to tackle after the build.

- **Caching** — content-addressed cache of API + LLM responses (TTL); enables
  replay/reproducibility; resume already avoids re-paying the LLM via step-cache.
- **Latency** — predicate pushdown (smaller payloads), batching, parallel
  source/endpoint fan-out, streaming partial results.
- **Cost** — token budgets; cheaper models for extraction vs synthesis; cache-hit
  rate; model-tier routing.
- **Resilience** — rate limits, backoff, quotas, circuit breakers.

---

## Part F — Take-home prioritization

**Build** (high impact, tractable):
- semantic re-plan loop (C1) — turns a 0-result dead-end into graceful recovery;
- richer spec + a few server-side filters (C2) — visibly fixes H₂O;
- a small **eval harness** (C5.1) — the top differentiator;
- DFT caveat + uncertainty + RAG-wired synthesis with real citations (C5.2–3);
- **one** better ranker — Pareto *or* desirability (C3).

**Articulate (doc only):**
- the outer optimization loop (C4) · cross-source merge (Part D) · full
  multi-endpoint routing · content-addressed replay.

**Winning narrative:** *facts from tools not the LLM; honest about uncertainty,
DFT limits, and database coverage gaps; one clean triage turn that's explicitly
the inner loop of a closed discovery loop; and an eval harness proving it.*
