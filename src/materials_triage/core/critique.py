"""Ranking-target critique: a second agent's verdict on the proposed objectives.

The hypothesis LLM tends to *invent* ranking objectives the goal never asked for
(e.g. ranking oxide dielectrics by ``bulk_modulus``), padding the weighted average
with irrelevant signal. A critic agent reviews each proposed ranking target and
the reason given for it, against the goal, and votes keep/drop. This module holds
the critic's structured output (LLM-facing, strict) and the *deterministic* pruning
that applies its verdict to the hypothesis proposals — after which ``compile_spec``
renormalizes the surviving weights to sum to 1.
"""

from pydantic import BaseModel, ConfigDict

from materials_triage.core.hypothesis import Proposal


class TargetVerdict(BaseModel):
    """The critic's verdict on one proposed ranking target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    property_name: str
    keep: bool
    reason: str


class BoundFlag(BaseModel):
    """An *advisory* concern the critic raised about a hard-constraint bound —
    one that looks inactive (excludes nothing), impossible (excludes everything),
    or counter to the goal. Surfaced to the human, never auto-applied: judging a
    bound needs physical-range knowledge, which the LLM only approximates, so its
    opinion is a flag, not a correction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    property_name: str
    concern: str


class RankingCritique(BaseModel):
    """The critic agent's whole emission: a keep/drop verdict per ranking target
    (relevance and redundancy, auto-applied) plus advisory flags on constraint
    bounds (surfaced, not applied)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdicts: tuple[TargetVerdict, ...]
    bound_flags: tuple[BoundFlag, ...] = ()


def prune_ranking_proposals(
    proposals: tuple[Proposal, ...], critique: RankingCritique
) -> tuple[tuple[Proposal, ...], list[TargetVerdict]]:
    """Drop the ranking-target proposals the critic rejected.

    Returns the surviving proposals (original order; non-ranking proposals
    untouched) and the dropped verdicts. Guards against emptying the ranking
    objectives: if the critic would reject *every* ranking target, nothing is
    dropped (a critic that disowns all objectives is not trusted to leave the
    shortlist unordered). ``compile_spec`` renormalizes the survivors' weights."""
    rejected = {v.property_name for v in critique.verdicts if not v.keep}
    ranking_props = [p for p in proposals if p.kind == "ranking_target"]
    would_drop = [p for p in ranking_props if p.ranking_target.property_name in rejected]

    if not would_drop or len(would_drop) >= len(ranking_props):
        return tuple(proposals), []

    dropped_names = {p.ranking_target.property_name for p in would_drop}
    kept = tuple(
        p
        for p in proposals
        if not (p.kind == "ranking_target" and p.ranking_target.property_name in dropped_names)
    )
    dropped = [v for v in critique.verdicts if v.property_name in dropped_names]
    return kept, dropped
