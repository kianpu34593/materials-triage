# ADR 0002 — Literature grounding from abstracts/metadata only (no full text)

**Status:** Accepted · **Date:** 2026-06-21 · **Scope:** literature RAG (#17), and the LLM
hypothesis (step 3) and synthesis (step 7) stages it feeds

## Context

The literature RAG grounds two LLM stages: **hypothesis** (propose candidate families /
property ranges *before* database retrieval) and **synthesis** (write the cited mechanistic
"why" *after* ranking). The natural objection is that real grounding — especially a *mechanism*
— lives in a paper's introduction, methods, and results, not its abstract. So: does a RAG built
only on titles + abstracts actually support traceable, defensible hypotheses and mechanistic
reasoning, or do we need body text?

The choices were: (a) **abstracts + metadata only** (OpenAlex `abstract_inverted_index`,
title, authors, venue, year, DOI); (b) **abstracts + open-access full text** for the legally-free
subset (OpenAlex `open_access.oa_url`, PMC, Unpaywall) with PDF/XML parsing and chunking;
(c) full text including paywalled sources (rejected outright — see below).

## Decision

**v1 grounds on abstracts + metadata only.** No full text — not even open-access full text.
A `LiteraturePassage` carries the reconstructed abstract as its sole body text; works with no
abstract are kept, flagged missing, and ranked on their title. Citations resolve to the *work*
(via `Provenance(source="openalex", record_id=<work id>)`), not a section or page.

Crucially, this pairs with a **claim-framing rule** enforced downstream by the output validator
(#20): the LLM may assert only what the retrieved abstract *states*, framed with the correct
epistemic verb — "Zhang et al. **report** / **attribute** …" — never "X is established" or a
number the abstract did not contain.

## Rationale (why abstract-only is sufficient *for this architecture*)

- **The RAG is not the fact layer — Materials Project is.** Every number comes from MP, tagged
  with `Provenance`, filtered/ranked by deterministic code (ADR 0001). The RAG never supplies or
  verifies a quantitative result, so it never needs a results section. It supplies *direction*
  (which families, and why) and *cited narrative*, both of which abstracts carry.
- **A hypothesis is a falsifiable proposal, verified downstream.** Step 3 uses the abstract to
  scope ("perovskite oxides are promising OER catalysts [cite]"); the deterministic retrieve →
  filter → rank then **confirms or refutes** it against MP numbers. We never claim the abstract
  *proves* anything — so abstract-level grounding is the correct epistemic level for a hypothesis.
- **Materials/chemistry abstracts are unusually evidence-dense.** Unlike many fields, a materials
  abstract typically states the headline metric *and* the claimed mechanism in 1–3 sentences
  (e.g. "…overpotential of 290 mV at 10 mA cm⁻², attributed to surface oxygen vacancies"). The
  mechanistic *claim* — what synthesis cites — is in the abstract, even if the supporting
  evidence chain is not.
- **The honesty discipline converts the limit into correct behavior.** Because synthesis may
  assert only what the abstract states (validator #20), a mechanism becomes a *cited literature
  claim* ("authors attribute activity to e_g occupancy [W123]"), not an extracted fact. This is
  the right level for a triage shortlist and is fully traceable.
- **Capability-safety / public-data-only (load-bearing project thesis).** Paywalled full text
  would require a scraper or a paywalled-source tool — exactly the capability the design forbids
  *by construction*. Keeping retrieval to public abstracts/metadata means no such tool exists,
  so the safety guarantee holds structurally, not by policy.
- **Zero-setup, deterministic, mockable.** Abstracts come from one keyless public API (OpenAlex)
  as JSON; passage = whole abstract means **no chunking**, no embedding store, no PDF/XML
  pipeline. BM25 over title+abstract stays a pure, fixture-testable function (mirrors ADR 0001's
  injected-transport seam).

## Trade-offs (accepted)

- **Mechanistic depth is shallow.** We get the authors' *claimed* mechanism, not the derivation
  or supporting data. The product's "why" is "the literature proposes X," appropriate for triage
  but not a substitute for reading the paper. Surfaced honestly via claim-framing.
- **No measurement-condition nuance** from methods/results — but that is MP's responsibility
  here, not the RAG's.
- **Coverage gap.** Roughly 20–40% of *recent, well-indexed* materials articles (and a larger
  share across all of OpenAlex) have a `null` abstract — chiefly publisher redistribution
  limits (historically Elsevier), plus content types that legitimately have none (editorials,
  errata, datasets). Those works contribute title-only and are flagged missing, never dropped
  silently or fabricated.
- **Citation granularity is the work, not a section/page.** Acceptable for a shortlist.

## Alternatives considered

- **Open-access full text (OA only).** Legally clean (no paywall breach) and would give real
  intro/results for the OA subset. Rejected for v1 as a large scope increase — PDF/XML parsing,
  ragged formats, chunking (reopening a decision abstract-only avoids), and a much larger
  untrusted-text surface for the trust boundary (#19) — for uneven coverage. **Revisit post-v1**
  if mechanistic depth proves insufficient in eval; it slots behind the same `AbstractFetcher`
  seam without changing the public RAG interface.
- **Full text incl. paywalled.** Rejected outright: violates public-data-only and the
  no-scraper capability-safety guarantee.

## Consequences

- `LiteraturePassage.text` holds the whole abstract, isolated and never concatenated into
  instructions (wrapping-ready for the #19 trust boundary); `missing` is first-class.
- Synthesis prompts (#22) must enforce epistemic claim-framing; the output validator (#20) must
  reject any literature claim not resolvable to a retrieved abstract — this ADR is the rationale
  those stages cite.
- The depth ceiling is a known eval risk: the eval harness (#28) should include cases that probe
  whether abstract-grounded mechanism is "good enough," giving an objective trigger to revisit
  the OA-full-text alternative.
- Feeds the literature section of the full design note (#29).
