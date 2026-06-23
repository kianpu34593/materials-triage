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
    PredicateRouting,
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


def desirability_curve(
    value: float,
    direction: Literal["maximize", "minimize", "target"],
    lower: float | None,
    target: float | None,
    upper: float | None,
    curvature: float,
) -> float:
    """Map ``value`` to a Derringer-Suich desirability in [0, 1] over absolute anchors.

    A ``maximize`` curve rises from 0 at ``lower`` to 1 at the ``target``
    saturation anchor; a ``minimize`` curve is 1 at/below ``target`` and falls to
    0 at ``upper``; a ``target`` curve peaks at ``target`` and falls to 0 at both
    outer anchors (moderate-is-best). Each is clamped flat outside its ramp;
    ``curvature`` bends the ramp (1 linear, >1 strict, <1 lenient).
    """
    if direction == "maximize":
        numerator, span = value - lower, target - lower
    elif direction == "minimize":
        numerator, span = upper - value, upper - target
    elif value <= target:
        numerator, span = value - lower, target - lower
    else:
        numerator, span = upper - value, upper - target
    if span == 0:
        # Spec-supplied anchors must strictly ascend, so a zero span now only
        # arises from pool collapse (every candidate shares the value, so the
        # pooled min and max coincide); the curve carries no signal, so return a
        # neutral 0.5 rather than dividing by zero.
        return 0.5
    fraction = min(1.0, max(0.0, numerator / span))
    return fraction**curvature


def resolve_bounds(
    target: RankingTarget, present_values: list[float]
) -> tuple[float | None, float | None, float | None]:
    """Resolve a target's desirability anchors to absolute (lower, target, upper).

    The same-source rule (enforced by ``RankingTarget``): a ramp's two anchors are
    either both supplied by the spec or both omitted, in which case they fall back
    together to the candidate pool's extremes — so the span can never go negative.
    ``maximize`` ramps the pool minimum (floor) up to the maximum (saturation),
    ``minimize`` is the mirror, and ``target`` always names its full window.
    """
    pool_min = min(present_values) if present_values else None
    pool_max = max(present_values) if present_values else None

    if target.direction == "maximize":
        if target.lower is None:
            return pool_min, pool_max, None
        return target.lower, target.target, None
    if target.direction == "minimize":
        if target.upper is None:
            return None, pool_min, pool_max
        return None, target.target, target.upper
    return target.lower, target.target, target.upper


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


def apply_local_filters(
    candidates: list[Candidate], routing: PredicateRouting
) -> tuple[list[Candidate], list[ExcludedCandidate]]:
    """Enforce a source's *exclusive set* — the hard predicates it could neither push
    server-side nor express numerically, so the deterministic layer must apply them.

    Currently the local booleans (a retrievable-but-not-queryable flag like
    ``is_magnetic``, stored as ``1.0``/``0.0``). A candidate whose value is absent is
    dropped ``missing_data``; one whose flag disagrees with ``required`` is dropped
    ``boolean_mismatch``. Numeric ``Constraint``s remain :func:`apply_hard_filters`'
    job; this is the complement.
    """
    survivors: list[Candidate] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        drop: ExcludedCandidate | None = None
        for boolean in routing.local_booleans:
            prop = candidate.properties.get(boolean.property_name)
            value = prop.value if prop is not None else None
            if value is None:
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=boolean.property_name,
                    reason="missing_data",
                )
                break
            if bool(value) != boolean.required:
                drop = ExcludedCandidate(
                    candidate=candidate,
                    property_name=boolean.property_name,
                    reason="boolean_mismatch",
                )
                break
        if drop is None:
            # Element predicates routed here because the source could not push them:
            # "any" (no MP OR-param) holds when the composition shares >=1 member;
            # "none" (an oversized exclude_elements list MP rejects, >60 chars) holds
            # when the composition shares NO member; "all" holds when it contains every
            # member. A violation is an element_mismatch drop.
            for predicate in routing.local_element_predicates:
                overlap = candidate.elements & predicate.members
                violated = {
                    "any": not overlap,
                    "none": bool(overlap),
                    "all": predicate.members - candidate.elements != frozenset(),
                }[predicate.quantifier]
                if violated:
                    drop = ExcludedCandidate(
                        candidate=candidate,
                        property_name="elements",
                        reason="element_mismatch",
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


def score_desirability(
    candidates: list[Candidate], target: RankingTarget
) -> list[tuple[float, bool]]:
    """Score one ranking target across a pool by its desirability curve.

    Anchors are resolved once against the present values, then each present value
    is mapped through :func:`desirability_curve`; a candidate missing the value is
    imputed the neutral midpoint ``0.5`` and flagged so one gap cannot zero the
    geometric mean. Each entry is ``(desirability, flagged)``, aligned to
    ``candidates`` — the same shape as :func:`score_target` so the ranker composes
    either scorer unchanged.
    """
    values = [
        prop.value if (prop := candidate.properties.get(target.property_name)) else None
        for candidate in candidates
    ]
    present = [v for v in values if v is not None]
    lower, peak, upper = resolve_bounds(target, present)
    return [
        (0.5, True)
        if v is None
        else (desirability_curve(v, target.direction, lower, peak, upper, target.curvature), False)
        for v in values
    ]
