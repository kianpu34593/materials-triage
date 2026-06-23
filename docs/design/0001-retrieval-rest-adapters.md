# ADR 0001 — Database retrieval via thin REST/JSON adapters

**Status:** Accepted · **Date:** 2026-06-20 · **Scope:** retrieval layer (#15 `SourceAdapter`, #16 Materials Project)

## Context

The pipeline needs ground-truth material properties from public databases (v1: Materials
Project; later OQMD/AFLOW/PubChem/ICSD). Retrieval is **deterministic code, never the LLM** —
it is the sole source of numbers, each tagged with `Provenance`. We had to choose *how* an
adapter reaches a source: a vendor client SDK (e.g. `mp_api`/`pymatgen` `MPRester`), a hosted
copy / bulk data dump, scraping, or the source's public **REST/JSON HTTP API**.

## Decision

Each source is a **thin REST/JSON adapter** behind one uniform interface —
`SourceAdapter.retrieve(spec: TriageSpec) -> list[Candidate]` — calling the source's public
HTTP API through an injected transport (`http_get(url, params, headers) -> dict`). The adapter
unwraps the transport envelope, pins units the payload omits, attaches provenance, and returns
`Candidate`s straight into `apply_hard_filters` → `rank`.

## Rationale (flexibility + locked decisions)

- **Uniform, generalizable seam.** Adding a source = a small adapter (a field→unit table + an
  envelope unwrap), not a new integration style. OQMD/AFLOW/PubChem all expose REST/JSON, so the
  one pattern carries to the deferred stubs. Vendor SDKs differ per source and would fragment this.
- **Zero setup, no DB to host** (locked: "HTTP client over public APIs; only local state is run
  traces + memory" · "generalizable with zero setup"). Bulk dumps would mean syncing gigabytes,
  staleness, and infra; REST is always-current.
- **No vendor lock-in / no dependency weight.** `requests` only — vs. `MPRester` dragging in
  pymatgen + scipy and binding us to one vendor's client.
- **Mockable by construction.** REST reduces to `(url, params, headers) → JSON`, which is exactly
  why the injected-transport seam lets us test all parsing offline and deterministically (see
  the adapter-testing convention). SDKs are far harder to fake cleanly.
- **Capability-safety.** The only retrieval capability that exists is an HTTP client over public
  APIs — no scraper, no private-DB, no paywalled-source tool. Public-data-only is enforced by
  construction.
- **Query-by-spec.** The adapter requests exactly the `_fields` the run reads (union of
  constrained + ranked property names + identity fields, plus `origins` — the per-property
  bridge to the task carrying each value's XC functional), trimming the ~100-field payload.

## Trade-offs (accepted)

- **Not every constraint is pushable server-side.** The adapter scopes the query, but the
  deterministic `apply_hard_filters`/`rank` stages remain the filtering/ranking authority — the
  adapter never silently filters. *(#38, later refinement — partly reversed: the adapter now
  pushes every hard filter MP can express, gated on the schema-derived `PUSHABLE_PARAMS`, and is
  the **single authority** for those pushed filters — there is no redundant local re-check.
  Correctness of the pushed filters is guaranteed by a `live`-marked contract suite, not a
  local backstop.)* The DB-inexpressible complement — the **exclusive set** of predicates a source
  can return data for but not query server-side (retrievable ∩ ¬queryable, e.g. MP's `is_magnetic`
  or an element `any`) — is enforced by a separate `apply_local_filters` stage, fed by the
  adapter's `classify_predicates(spec) -> PredicateRouting` (the adapter owns its retrievable
  `FIELD_UNITS` and queryable `PUSHABLE_PARAMS` surfaces, so it routes each predicate; numeric
  `Constraint`s stay `apply_hard_filters`' job). Predicates the source can neither push nor return
  data for (¬retrievable ∩ ¬queryable) are recorded as loud run-level `caveats` rather than
  silently ignored. This keeps multi-source free — each adapter's own R/Q gives its own exclusive
  set, with no hand-maintained capability declaration.
- **Network reality** — latency, rate limits, pagination. Mitigated by the step-cache (re-runs
  reuse retrieval) and a `_limit` cap; live calls sit behind a deselected `live` test marker.
- **API drift** — a source changing its schema breaks one adapter; the per-source field→unit
  table localizes the blast radius.

## Consequences

- Live network code is isolated to a lazily-imported `requests` transport; everything else is pure.
- The sandboxed Materials Project mirror anonymizes ids (query id ≠ returned id), so adapters
  store the **source-returned** `material_id` as `Candidate.identifier`/`Provenance.record_id`.
- Provenance carries trust metadata (`method`, `xc_functional`). The summary payload omits the
  XC functional, so the MP adapter issues a **second batched `/materials/tasks/` call** —
  resolving each value's producing task (via the doc's `origins`) to its `run_type` and stamping
  it onto provenance. This enrichment is best-effort: a failed/empty tasks call degrades the
  functional to unknown rather than aborting an otherwise-complete retrieval.
- This note seeds the retrieval section of the full design note (#29).
- **Vocabulary surface (#39, later refinement).** The interface gained a second,
  default-empty method — `property_vocabulary() -> Mapping[str, str | None]` — so the
  spec-building stages can discover, from the adapter that will retrieve, exactly which
  property names are fetchable (`None` unit = dimensionless); retrieval itself stays the
  single `retrieve` method. The MP adapter's field→unit table referenced above is no
  longer hand-typed: it is generated from the vendored MP OpenAPI snapshot by
  `tools/gen_mp_vocab.py` into a committed `sources/_mp_fields.py` (the `MP_FIELDS` table:
  units + XC-functional origins), and `FIELD_UNITS`/`_FIELD_ORIGIN` derive from it — keeping
  the surface in lockstep with the schema while the "localizes the blast radius" property above
  still holds. The same generator also emits `PUSHABLE_PARAMS` — the `/summary` GET query-param
  surface (distinct from, and larger than, the retrievable fields) — which gates the server-side
  filter push (#38).
