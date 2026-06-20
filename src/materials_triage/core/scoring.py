"""Deterministic scoring helpers: pure functions over property values.

These compute the numbers the ranker combines; they hold no state and touch no
data model. Normalisation maps a property's values onto a comparable [0, 1]
scale so the weighted average can sum unlike units.
"""

from typing import Literal

from materials_triage.core.schema import (
    Candidate,
    Constraint,
    ExcludedCandidate,
    RankingTarget,
)


def normalize(values: list[float], direction: Literal["maximize", "minimize"]) -> list[float]:
    """Min-max normalise ``values`` onto [0, 1], where 1 is best for ``direction``."""
    if direction not in ("maximize", "minimize"):
        raise ValueError(f"direction must be 'maximize' or 'minimize', got {direction!r}")
    if not values:
        raise ValueError("cannot normalize an empty pool of values")
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        # Every value is identical: the property cannot discriminate, so it
        # contributes a neutral 0.5 rather than dividing by a zero span.
        return [0.5 for _ in values]
    if direction == "maximize":
        return [(v - lo) / span for v in values]
    return [(hi - v) / span for v in values]


def apply_hard_filters(
    candidates: list[Candidate], constraints: tuple[Constraint, ...]
) -> tuple[list[Candidate], list[ExcludedCandidate]]:
    """Partition candidates into survivors and exclusions.

    A candidate is dropped on the first constraint it violates (in constraint
    order); the exclusion records that one structured reason.
    """
    survivors: list[Candidate] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        drop: ExcludedCandidate | None = None
        for constraint in constraints:
            prop = candidate.properties.get(constraint.property_name)
            value = prop.value if prop is not None else None
            if value is None:
                # The property is absent or flagged missing, so the hard
                # constraint cannot be verified; exclude rather than pass it.
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=constraint.property_name,
                    reason="missing_data",
                )
                break
            if constraint.min is not None and value < constraint.min:
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=constraint.property_name,
                    reason="below_min",
                    value=value,
                    bound=constraint.min,
                )
                break
            if constraint.max is not None and value > constraint.max:
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=constraint.property_name,
                    reason="above_max",
                    value=value,
                    bound=constraint.max,
                )
                break
        if drop is not None:
            excluded.append(drop)
        else:
            survivors.append(candidate)
    return survivors, excluded


def drop_missing_excluded(
    candidates: list[Candidate], ranking_targets: tuple[RankingTarget, ...]
) -> tuple[list[Candidate], list[ExcludedCandidate]]:
    """Resolve the ``on_missing="exclude"`` policy before ranking.

    A candidate with no value for an ``exclude``-policy ranking target cannot be
    ranked on a property it lacks, so it is dropped with a ``missing_data``
    exclusion rather than guessed at. Targets with other policies are left for
    the scorer to impute. A candidate is dropped on the first such target (in
    target order); survivors keep their original order.
    """
    survivors: list[Candidate] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        drop: ExcludedCandidate | None = None
        for target in ranking_targets:
            if target.on_missing != "exclude":
                continue
            prop = candidate.properties.get(target.property_name)
            value = prop.value if prop is not None else None
            if value is None:
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=target.property_name,
                    reason="missing_data",
                )
                break
        if drop is not None:
            excluded.append(drop)
        else:
            survivors.append(candidate)
    return survivors, excluded


def score_target(candidates: list[Candidate], target: RankingTarget) -> list[tuple[float, bool]]:
    """Score one ranking target across a pool, aligned to ``candidates``.

    Present values are min-max normalised in the target's direction (1 is best);
    a candidate missing the value is imputed the neutral midpoint ``0.5`` and
    flagged. Each entry is ``(contribution, flagged)`` where ``flagged`` marks an
    imputed gap so the weighted average never silently credits missing data.
    """
    values = [
        prop.value if (prop := candidate.properties.get(target.property_name)) else None
        for candidate in candidates
    ]
    present = [v for v in values if v is not None]
    normalized = iter(normalize(present, target.direction)) if present else iter(())
    return [(0.5, True) if v is None else (next(normalized), False) for v in values]
