"""Deterministic ranking: turn filtered candidates into a ranked ``TriageResult``.

The rankers compose the scoring helpers — they resolve the ``exclude`` missing
policy, map each ranking target onto a comparable [0, 1] scale, and combine the
targets into a single composite score. Two strategies are offered, selected per
run by ``TriageSpec.ranking_method``: :func:`rank_arithmetic_mean` (the
compensatory weighted average) and :func:`rank_geometric_mean` (the
non-compensatory weighted geometric mean of desirability curves). Both invent
nothing: every number traces back to a candidate's provenance-tagged property.
"""

import math

from materials_triage.core.schema import (
    Candidate,
    RankingTarget,
    ScoredCandidate,
    TriageResult,
)
from materials_triage.core.scoring import (
    drop_missing_excluded,
    score_desirability,
    score_target,
)


def rank_arithmetic_mean(
    candidates: list[Candidate], ranking_targets: tuple[RankingTarget, ...]
) -> TriageResult:
    """Rank ``candidates`` by the weighted arithmetic mean of their ranking targets.

    Candidates missing an ``exclude``-policy target are dropped first; the rest
    are scored by ``Σ weight × normalized`` (a weighted arithmetic mean, since the
    weights sum to 1) and listed best-first. This is the compensatory ranker — a
    strong target can offset a weak one; the non-compensatory alternative is
    :func:`rank_geometric_mean`.
    """
    survivors, excluded = drop_missing_excluded(candidates, ranking_targets)
    columns = [score_target(survivors, target) for target in ranking_targets]

    scored: list[ScoredCandidate] = []
    for index, candidate in enumerate(survivors):
        # Transpose the per-target columns into this candidate's row of
        # (target, contribution, flagged) so weight and flag read by name.
        row = [(target, *columns[col][index]) for col, target in enumerate(ranking_targets)]
        contributions = {
            target.property_name: target.weight * contribution
            for target, contribution, _flagged in row
        }
        flagged_missing = frozenset(
            target.property_name for target, _contribution, flagged in row if flagged
        )
        score = sum(contributions.values())
        scored.append(
            ScoredCandidate(
                candidate=candidate,
                score=score,
                contributions=contributions,
                flagged_missing=flagged_missing,
            )
        )

    scored.sort(key=lambda sc: sc.score, reverse=True)
    return TriageResult(ranked=tuple(scored), excluded=tuple(excluded))


def rank_geometric_mean(
    candidates: list[Candidate], ranking_targets: tuple[RankingTarget, ...]
) -> TriageResult:
    """Rank ``candidates`` by the weighted geometric mean of their desirabilities.

    Candidates missing an ``exclude``-policy target are dropped first; the rest
    are scored by ``Π dᵢ^wᵢ`` — the weighted geometric mean of each target's
    desirability curve — and listed best-first. Unlike
    :func:`rank_arithmetic_mean` this is non-compensatory: a single zero
    desirability zeros the whole score, so a strong target cannot rescue a
    candidate that fails another. ``contributions`` records each target's raw
    desirability ``dᵢ`` (the multiplicative factors), not an additive share.
    """
    survivors, excluded = drop_missing_excluded(candidates, ranking_targets)
    columns = [score_desirability(survivors, target) for target in ranking_targets]

    scored: list[ScoredCandidate] = []
    for index, candidate in enumerate(survivors):
        # Transpose the per-target columns into this candidate's row of
        # (target, desirability, flagged) so weight and flag read by name.
        row = [(target, *columns[col][index]) for col, target in enumerate(ranking_targets)]
        contributions = {target.property_name: d for target, d, _flagged in row}
        flagged_missing = frozenset(target.property_name for target, _d, flagged in row if flagged)
        score = math.prod(d**target.weight for target, d, _flagged in row)
        scored.append(
            ScoredCandidate(
                candidate=candidate,
                score=score,
                contributions=contributions,
                flagged_missing=flagged_missing,
            )
        )

    scored.sort(key=lambda sc: sc.score, reverse=True)
    return TriageResult(ranked=tuple(scored), excluded=tuple(excluded))
