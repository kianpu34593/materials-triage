# Fast-track learnings (branch `feat/fast-track-wire-guardrails`)

> This branch was **build-fast / fail-fast** and is **not** being adopted into the
> final build. This document is the artifact worth keeping: the behavior we
> observed end-to-end and the design learnings to carry forward. Port this file
> (and the conclusions, not the code) to the real build.

## 0. What the fast track proved

Wiring the existing standalone pieces (gate, trust boundary, prompts, providers)
into the LangGraph pipeline produced a **working end-to-end run** against real
Bedrock + the Materials Project mirror:

```
gate → hypothesis → spec_build(HITL, auto-accepted) → retrieve → filter → rank
     → synthesis → output_validate → render(pi|audit)
```

Verified live (≈10 runs): gate refuses forbidden requests pre-LLM; the LLM builds
a spec; MP returns 100 candidates; deterministic filter/rank produce a
provenance-tagged shortlist; the LLM writes a grounded, cited narrative; both
views render consistently. 196 offline tests pass.

**Patterns that worked and should survive:**
- **Injected seams** (`http_get`, `complete`/`propose`/`synthesize`) → the whole
  pipeline is offline-testable with fakes; real transport built lazily.
- **LLM proposes, deterministic code disposes** → every LLM output is
  validated/grounded before it counts (compile_spec, output validator).
- **Retry-on-nonconformance** → LLMs are ~15% flaky; feed the specific failure
  back and retry, then raise a typed error.

**Bugs the demo caught that tests didn't** (all real-data-shape / integration
issues): vocabulary drift → empty results; PI/audit count mismatch from running
the pipeline twice; MP returns elastic moduli as a Voigt-Reuss-Hill **dict**, not
a float, crashing `PropertyValue`.

---

## 1. Vocabulary binding → it should be DERIVED from the published API schema

**What we did (the stop-gap):** hand-maintained `FIELD_UNITS` (6 names) in the MP
adapter, exposed via `SourceAdapter.property_vocabulary()`, and injected those
names into the hypothesis prompt with a "use ONLY these" rule. This fixed the
empty-results bug (the LLM was free-naming `band_gap_eV` vs the source's
`band_gap`).

**The real insight (yours):** this "vocabulary" is just the **published query
syntax of the REST API** — we shouldn't be hand-maintaining a 6-item subset.

The Materials Project summary endpoint publishes a *large* query surface. There
are **two distinct vocabularies** we currently conflate:

**(a) Queryable filter parameters** (~50 — what you can scope server-side):
`band_gap`, `energy_above_hull`, `formation_energy`, `density`, `volume`,
`k_vrh`/`k_voigt`/`k_reuss` (bulk modulus), `g_vrh`/`g_voigt`/`g_reuss` (shear),
`elements`, `exclude_elements`, `chemsys`, `formula`, `num_elements`,
`num_sites`, `is_stable`, `is_metal`, `is_gap_direct`, `theoretical`,
`crystal_system`, `spacegroup_number`/`spacegroup_symbol`, `magnetic_ordering`,
`total_magnetization`, `e_total`/`e_ionic`/`e_electronic` (dielectric constants),
`n` (refractive index), `poisson_ratio`, `piezoelectric_modulus`, `efermi`,
`possible_species`, `has_props`, … (source: `SummaryRester.search()` docstring).

**(b) Returnable fields** (`SummaryDoc` model): the columns `_fields` can ask for.

**What we use today:** ~6 numeric fields, and we push only `elements` server-side
— everything else is retrieved (100 rows) and filtered client-side.

### The opportunity / extension design

1. **Auto-derive the vocabulary** from the source's published schema instead of
   hardcoding. MP ships an OpenAPI spec (`/openapi.json`) and the `mp-api` client
   exposes the searchable args + `available_fields` programmatically. An adapter's
   `property_vocabulary()` could be *generated* (cached), so:
   - it never drifts from what the API accepts,
   - new fields appear for free,
   - a second source (OQMD, AFLOW) plugs in by pointing at its own schema.
2. **Push filters server-side.** Most of the query maps 1:1 to MP params
   (`band_gap_min`, `energy_above_hull_max`, `exclude_elements`, `num_elements_max`,
   `is_stable`, `is_metal`). Doing this server-side is faster, avoids the
   100-row-then-drop pattern, and (critically, see §4) lets us express
   constraints our numeric-only `Constraint` model can't.

### The catch: our spec is narrower than the API

This is the load-bearing learning. Our spec vocabulary is:
- `Constraint` = inclusive **min/max on one numeric property**, and
- `ElementRule` = require/exclude **specific element symbols**.

But the API can filter on **booleans** (`is_stable`, `is_metal`), **counts**
(`num_elements`), **categoricals** (`crystal_system`, `magnetic_ordering`), and
**element sets** (`exclude_elements`). So even with a derived vocabulary, the spec
can only *use* the numeric slice. **The vocabulary and the spec schema must
co-evolve** — deriving 50 field names is pointless if the spec can express only 6
of them. (Directly causes the unrealistic candidates in §4.)

---

## 2. Prompt fidelity — what actually happened

Three concrete states of the hypothesis prompt, with observed behavior:

**v0 (before fast track):**
```
Propose a materials triage hypothesis for this goal: {goal}
```
→ LLM free-named properties (`band_gap_eV`, `bandgap`, `formation_energy`).
→ **0 results every run** — names didn't match MP's `band_gap` /
`formation_energy_per_atom`, so all 100 candidates dropped as `missing_data`.

**v1 (+ vocabulary clause):**
```
... Use ONLY these retrievable property names in every constraint and ranking
target (units shown for reference, do not append them to the name):
band_gap (eV), formation_energy_per_atom (eV/atom), ... Do not invent or rename
properties — a name outside this list cannot be retrieved and the candidate will
be dropped.
```
→ Names became canonical (`band_gap`, `formation_energy_per_atom`).
→ **Candidates carried data**; exclusions became legitimate `below_min`/`above_max`.

**v2 (+ bound guidance):**
```
... Choose hard constraints that real materials can satisfy — avoid
over-aggressive one-sided thresholds that would exclude every candidate. When the
goal implies a target range rather than an extreme (e.g. a semiconductor wants a
moderate band gap, not the widest possible), set BOTH a min and a max ... and
leave ranking to express 'as high/low as possible' preferences.
```
→ Why this was needed: v1 still failed two ways — (a) the LLM picked
over-aggressive single-sided bounds like `formation_energy_per_atom ≤ -5.0` that
excluded **every** candidate (0 results); (b) a bare `band_gap ≥ 3.0` with a
*maximize* ranking surfaced the widest-gap **insulators** (molecular H₂O ice)
above real semiconductors.
→ v2 reduced empty-result runs and pushed toward two-sided windows, but **did not
fully fix the unrealistic-candidate problem** — that's a spec-expressiveness gap,
not a prompt-wording gap (§4).

**Learning:** prompt wording can nudge spec *quality*, but it cannot create spec
*expressiveness* the schema lacks. Order of leverage: schema expressiveness >
server-side filters > prompt wording.

---

## 3. Synthesis — single-shot, no RAG, no real "evidence" or "caveats"

**What it does today:** one LLM generation over the ranked **facts block**
(candidate ids + their retrieved numbers). It emits a `summary` + one
`GroundedClaim` per candidate, where the "citation" (`record_id`) is just the
**candidate's own MP id**. The grounding check / output validator only verify the
cited id is one we retrieved.

**What's missing:**
- **No literature RAG.** A `LiteratureRAG` (BM25 over OpenAlex/Crossref abstracts)
  exists in the codebase but is **not wired into synthesis**. So the "why / mechanism"
  is the LLM's own reasoning, grounded only to the candidate's numbers — not to any
  external public evidence. The reference query explicitly asked for *"public
  evidence"*; we don't produce any.
- **No caveats.** The reference query asked for *"caveats"*; the standalone caveats
  stage was deleted (folded into missing-data flags), so the narrative doesn't
  surface uncertainty (DFT functional dependence, missing properties, mirror
  weirdness) as first-class caveats.
- **Narrative vs numeric rank can disagree.** Observed: the prose called a
  candidate "first" that the deterministic ranker placed fourth. Grounding passed
  (ids resolve) but ordering fidelity isn't enforced.

### Design opportunity for real synthesis
Wire `LiteratureRAG` into synthesis as the trust-boundary DATA path: retrieve
abstracts per top candidate / per mechanism, wrap them as `<untrusted_data>`, and
require each mechanistic claim to cite a **`Citation`** (OpenAlex id) in addition
to the candidate `record_id`. The output validator then resolves *both* id types
against retrieved provenance. That makes "public evidence" real and keeps the
no-invented-facts guarantee. Add an explicit caveats field the renderer surfaces.

---

## 4. Unrealistic candidates (H₂O, H₃ClO) — root cause & fix

**Reference query:** *"Find promising oxide dielectric candidates for thin-film
experiments. Prefer thermodynamically stable materials, wide band gaps, non-toxic
elements, simple compositions, and public evidence. Return a ranked shortlist with
caveats."*

**What we got:** H₂O (ice) and H₃ClO ranked near the top.

**Why** — every qualifier in the query maps to a filter the **API supports** but
our **spec cannot express**, so nothing prunes them:

| Query phrase | MP filter that expresses it | Our spec today |
|---|---|---|
| "oxide" (O + a metal cation) | `elements` incl. O **and ≥1 metal** | only require/exclude *specific* symbols — can't say "must contain a metal" |
| "thermodynamically stable" | `is_stable=true` / `energy_above_hull≈0` | no boolean; only a numeric `energy_above_hull` bound if the LLM adds it |
| "non-toxic elements" | `exclude_elements=[Pb,Cd,Hg,As,Tl,Be,...]` | possible via ElementRule, but the LLM didn't emit it and there's no toxic-set default |
| "simple compositions" | `num_elements` (≤ 3) | **no count constraint exists** |
| "thin-film dielectric" (not molecular ice) | `is_metal=false` + density floor + non-molecular | no boolean/class constraints |
| "public evidence" | literature RAG citation | not wired (§3) |

H₂O passes a bare `band_gap ≥ 3` + maximize-band_gap spec because its DFT band gap
is large and it's an oxide *by formula*. Pure numeric thresholds can't encode "is a
sensible solid-state dielectric."

**Fix direction (for the real build), in leverage order:**
1. **Expand spec expressiveness** to cover the source's filter classes:
   booleans (`is_stable`, `is_metal`), counts (`num_elements`), and
   element-*class* rules ("contains ≥1 metal cation", "excludes a toxic set").
2. **Default safety/sanity constraints** seeded by the spec-builder: `is_stable`,
   `is_metal=false` for dielectrics, a non-toxic exclude-set, a `num_elements`
   cap — surfaced at the HITL gate for the scientist to confirm/relax.
3. **Push these server-side** (§1) so the candidate set is sane *before* ranking,
   instead of ranking a polluted set and hoping the top-k looks reasonable.
4. **Wire literature RAG** so "public evidence" and mechanism are real (§3).

**Meta-learning:** the quality ceiling is set by **spec expressiveness**, not by
the LLM or the prompt. The fast track maxed out a min/max-only `Constraint` model;
the realistic-candidate problem is the signal that the spec vocabulary must grow
to match the source's published query surface.
