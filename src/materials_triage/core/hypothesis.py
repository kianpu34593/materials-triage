"""Hypothesis-layer data models for the triage pipeline.

The LLM proposes bridges from a scientist's fuzzy goal to a queryable
``TriageSpec``; these models are the contract that proposal must conform to.
Literature grounding rides along as ``Citation`` (the untrusted-DATA analog of
``Provenance``) so synthesis can cite it and the output validator can confirm
every reference resolves. Validation here is the gate: malformed LLM output is
rejected before it can reach the deterministic core.
"""

import math
from collections.abc import Container
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from materials_triage.core.schema import (
    BooleanConstraint,
    Constraint,
    CountConstraint,
    ElementPredicate,
    RankingTarget,
    TriageSpec,
)


class Citation(BaseModel):
    """A literature reference a hypothesis was grounded in.

    The untrusted-DATA counterpart of :class:`~materials_triage.core.schema.Provenance`:
    it names the specific public record (e.g. an OpenAlex work or a DOI) so a
    claim can cite it and the output validator can confirm it resolves.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class _ProposalBase(BaseModel):
    """Fields shared by every proposal kind: the grounding the human gate and
    output validator judge a bridge by. ``extra="forbid"`` rejects any field
    foreign to the kind, so a hallucinated or mis-nested payload is refused rather
    than silently ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rationale: str = Field(min_length=1)
    citations: tuple[Citation, ...] = ()
    confidence: float = Field(gt=0.0, le=1.0)


class ConstraintProposal(_ProposalBase):
    """A cited bridge that compiles to a hard ``Constraint``."""

    kind: Literal["constraint"] = "constraint"
    constraint: Constraint


class BooleanConstraintProposal(_ProposalBase):
    """A cited bridge that compiles to a hard ``BooleanConstraint``."""

    kind: Literal["boolean_constraint"] = "boolean_constraint"
    boolean_constraint: BooleanConstraint


class CountConstraintProposal(_ProposalBase):
    """A cited bridge that compiles to a hard ``CountConstraint``."""

    kind: Literal["count_constraint"] = "count_constraint"
    count_constraint: CountConstraint


class RankingProposal(_ProposalBase):
    """A cited bridge that compiles to a soft ``RankingTarget``."""

    kind: Literal["ranking_target"] = "ranking_target"
    ranking_target: RankingTarget


class ElementPredicateProposal(_ProposalBase):
    """A cited bridge that compiles to a composition ``ElementPredicate``."""

    kind: Literal["element_predicate"] = "element_predicate"
    element_predicate: ElementPredicate


#: One cited bridge from a fuzzy goal to a queryable spec field. A discriminated
#: union on ``kind`` so the "this kind requires this payload" rule lives in the
#: JSON schema the LLM is handed — not in a hidden validator — which is what makes
#: structured output reliably emit the right payload. A deterministic compile step
#: assembles the accepted proposals into a ``TriageSpec``.
Proposal = Annotated[
    ConstraintProposal
    | BooleanConstraintProposal
    | CountConstraintProposal
    | RankingProposal
    | ElementPredicateProposal,
    Field(discriminator="kind"),
]


class Hypothesis(BaseModel):
    """The LLM's whole hypothesis-step emission.

    ``proposals`` are the load-bearing, cited bridges that compile to a
    ``TriageSpec``; ``mechanism`` is the grounded mechanistic "why" narrative.
    A hypothesis that proposes nothing is meaningless, so at least one proposal
    is required. Cross-proposal coherence (e.g. duplicate constraints, ranking
    weights) is enforced downstream when the proposals compile to a spec.
    """

    model_config = ConfigDict(frozen=True)

    proposals: tuple[Proposal, ...] = Field(min_length=1)
    mechanism: str = ""


def compile_spec(
    proposals: tuple[Proposal, ...],
    *,
    ranking_method: Literal["arithmetic_mean", "geometric_mean"] = "geometric_mean",
) -> TriageSpec:
    """Compile accepted proposals into the frozen TriageSpec the core consumes.

    The deterministic seam between the LLM layer and the pipeline: dispatch each
    proposal on its kind, then construct a TriageSpec — whose own validators
    enforce cross-proposal coherence (>=1 constraint, unique properties, no
    contradictory element predicates), so this function never has to.

    The agent's default ranker is the non-compensatory weighted **geometric mean**
    (a single unacceptable property zeros a candidate, so a strong score elsewhere
    can't compensate). ``ranking_method`` is overridable for callers that want the
    compensatory ``arithmetic_mean``; note the geometric ranker requires every
    ranking target to announce its desirability ramp bounds (TriageSpec enforces it).
    """
    constraints = tuple(p.constraint for p in proposals if p.kind == "constraint")
    booleans = tuple(p.boolean_constraint for p in proposals if p.kind == "boolean_constraint")
    targets = tuple(p.ranking_target for p in proposals if p.kind == "ranking_target")
    predicates = tuple(p.element_predicate for p in proposals if p.kind == "element_predicate")
    # The spec holds a single composition-cardinality bound; take the first count
    # proposal (the human gate reviews the compiled spec, so a stray second is caught).
    counts = [p.count_constraint for p in proposals if p.kind == "count_constraint"]
    return TriageSpec(
        constraints=constraints,
        boolean_constraints=booleans,
        ranking_targets=_normalize_weights(targets),
        element_predicates=predicates,
        count=counts[0] if counts else None,
        ranking_method=ranking_method,
    )


def drop_unrankable_targets(
    proposals: tuple[Proposal, ...],
    unrankable: Container[str],
) -> tuple[tuple[Proposal, ...], tuple[str, ...]]:
    """Drop ranking-target proposals naming a non-rankable property.

    A boolean flag (``is_magnetic``, ``is_stable``, ``is_metal``, ``is_gap_direct``) is a
    hard *filter*, never a ranking target: every candidate that survives the filter holds
    the same value, so scoring it through a desirability curve flattens the whole pool to
    1.0 — a meaningless rank. The source marks such properties non-rankable
    (``unrankable``); this removes any ranking target that names one while leaving its use
    as a filter predicate untouched. Returns the surviving proposals (original order) and
    the dropped property names (for a run caveat). Unlike the critic's prune, this never
    guards against emptying the ranking set — a boolean target is structurally invalid and
    must go even if it was the only one (no rank beats a meaningless one)."""
    dropped = tuple(
        p.ranking_target.property_name
        for p in proposals
        if p.kind == "ranking_target" and p.ranking_target.property_name in unrankable
    )
    if not dropped:
        return proposals, ()
    survivors = tuple(
        p
        for p in proposals
        if not (p.kind == "ranking_target" and p.ranking_target.property_name in unrankable)
    )
    return survivors, dropped


def _normalize_weights(targets: tuple[RankingTarget, ...]) -> tuple[RankingTarget, ...]:
    """Rescale ranking weights to sum to 1 (TriageSpec requires it), preserving
    ratios — the human gate accepts proposals independently, so the surviving
    weights almost never sum to 1 on their own.
    """
    total = math.fsum(t.weight for t in targets)
    return tuple(t.model_copy(update={"weight": t.weight / total}) for t in targets)
