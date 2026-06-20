"""Deterministic scoring helpers: pure functions over property values.

These compute the numbers the ranker combines; they hold no state and touch no
data model. Normalisation maps a property's values onto a comparable [0, 1]
scale so the weighted average can sum unlike units.
"""

from typing import Literal

from materials_triage.core.schema import Candidate, Constraint, ExcludedCandidate


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
