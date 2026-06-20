"""Deterministic weighted-average ranking: turn filtered candidates into a
ranked ``TriageResult``.

The ranker composes the scoring helpers — it resolves the ``exclude`` missing
policy, normalises each ranking target onto a comparable [0, 1] scale, and
combines them by their proportional weights into a single composite score. It
invents nothing: every number traces back to a candidate's provenance-tagged
property.
"""

from materials_triage.core.schema import (
    Candidate,
    RankingTarget,
    ScoredCandidate,
    TriageResult,
)
from materials_triage.core.scoring import drop_missing_excluded, score_target


def rank(candidates: list[Candidate], ranking_targets: tuple[RankingTarget, ...]) -> TriageResult:
    """Rank ``candidates`` by the weighted average of their ranking targets.

    Candidates missing an ``exclude``-policy target are dropped first; the rest
    are scored by ``Σ weight × normalized`` and listed best-first.
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
