"""Tests for the ranking-target critic in materials_triage.core.critique.

The hypothesis LLM tends to invent ranking objectives the goal never asked for
(e.g. ranking oxide dielectrics by ``bulk_modulus``). A critic agent votes
keep/drop on each proposed ranking target; ``prune_ranking_proposals`` applies the
verdict deterministically to the proposals, after which ``compile_spec``
renormalizes the surviving weights. These tests cover the pure pruning logic.
"""

from materials_triage.core.critique import (
    BoundFlag,
    RankingCritique,
    TargetVerdict,
    prune_ranking_proposals,
)
from materials_triage.core.hypothesis import (
    ConstraintProposal,
    RankingProposal,
)
from materials_triage.core.schema import Constraint, RankingTarget


def _ranking(property_name: str, weight: float = 0.5) -> RankingProposal:
    return RankingProposal(
        ranking_target=RankingTarget(
            property_name=property_name,
            direction="maximize",
            weight=weight,
            lower=1.0,
            target=3.0,
        ),
        rationale=f"prefer high {property_name}",
        confidence=0.8,
    )


def _constraint() -> ConstraintProposal:
    return ConstraintProposal(
        constraint=Constraint(property_name="band_gap", min=2.0),
        rationale="wide gap",
        confidence=0.8,
    )


def test_prune_drops_a_rejected_ranking_target_and_keeps_the_rest():
    """A keep=False verdict drops that ranking proposal; the kept ranking target and
    every non-ranking proposal survive in original order, and the dropped verdict is
    reported back for the trace."""
    proposals = (_constraint(), _ranking("band_gap"), _ranking("bulk_modulus"))
    critique = RankingCritique(
        verdicts=(
            TargetVerdict(property_name="band_gap", keep=True, reason="core objective"),
            TargetVerdict(property_name="bulk_modulus", keep=False, reason="goal never asked"),
        )
    )

    kept, dropped = prune_ranking_proposals(proposals, critique)

    kept_ranks = [p.ranking_target.property_name for p in kept if p.kind == "ranking_target"]
    assert kept_ranks == ["band_gap"]  # the invented objective is gone
    assert any(p.kind == "constraint" for p in kept)  # non-ranking proposals untouched
    assert [v.property_name for v in dropped] == ["bulk_modulus"]


def test_prune_refuses_to_drop_every_ranking_target():
    """A critic that disowns *all* ranking objectives is not trusted — dropping them
    all would leave the shortlist unordered, so the guard keeps every proposal."""
    proposals = (_constraint(), _ranking("band_gap"), _ranking("density"))
    critique = RankingCritique(
        verdicts=(
            TargetVerdict(property_name="band_gap", keep=False, reason="x"),
            TargetVerdict(property_name="density", keep=False, reason="y"),
        )
    )

    kept, dropped = prune_ranking_proposals(proposals, critique)

    assert kept == proposals  # nothing dropped
    assert dropped == []


def test_ranking_critique_carries_advisory_bound_flags():
    """The critique can carry advisory BoundFlags (concerns about constraint bounds),
    defaulting to none — they are surfaced to the human, never auto-applied."""
    assert RankingCritique(verdicts=()).bound_flags == ()  # default empty
    critique = RankingCritique(
        verdicts=(),
        bound_flags=(
            BoundFlag(property_name="band_gap", concern="12 eV ceiling excludes nothing"),
        ),
    )
    assert critique.bound_flags[0].property_name == "band_gap"
