"""Tests for the deterministic scoring helpers in materials_triage.core.scoring."""

import pytest

from materials_triage.core.schema import (
    BooleanConstraint,
    Candidate,
    Constraint,
    ElementPredicate,
    PredicateRouting,
    PropertyValue,
    Provenance,
    RankingTarget,
)
from materials_triage.core.scoring import (
    apply_hard_filters,
    apply_local_filters,
    desirability_curve,
    drop_missing_excluded,
    normalize,
    resolve_bounds,
    score_desirability,
    score_target,
)


def _candidate(identifier: str, formula: str, **props: float) -> Candidate:
    """Build a candidate whose named properties carry a value in eV and a receipt."""
    return Candidate(
        identifier=identifier,
        formula=formula,
        properties={
            name: PropertyValue(
                value=value,
                unit="eV",
                provenance=Provenance(
                    source="Materials Project", record_id=identifier, method="computational"
                ),
            )
            for name, value in props.items()
        },
    )


def test_apply_local_filters_drops_a_candidate_failing_a_local_boolean():
    """The exclusive-set filter enforces a routing's local boolean: a candidate whose
    is_magnetic value doesn't match the required flag is excluded, the matching one
    survives. (Booleans store as 1.0/0.0.)"""
    magnetic = _candidate("mp-mag", "Fe", is_magnetic=1.0)
    nonmagnetic = _candidate("mp-non", "Cu", is_magnetic=0.0)
    routing = PredicateRouting(
        local_booleans=(BooleanConstraint(property_name="is_magnetic", required=True),)
    )

    survivors, excluded = apply_local_filters([magnetic, nonmagnetic], routing)

    assert [c.identifier for c in survivors] == ["mp-mag"]
    assert excluded[0].candidate.identifier == "mp-non"
    assert excluded[0].property_name == "is_magnetic"


def test_apply_local_filters_drops_a_candidate_failing_a_local_any_predicate():
    """A local 'any' element predicate: a candidate sharing at least one required
    member survives; one sharing none is excluded (element_mismatch). Uses the
    composition on the candidate, which the source returned but couldn't push."""
    has_fe = Candidate(identifier="mp-fe", formula="FeO", elements=frozenset({"Fe", "O"}))
    no_match = Candidate(identifier="mp-cu", formula="CuO", elements=frozenset({"Cu", "O"}))
    routing = PredicateRouting(
        local_element_predicates=(
            ElementPredicate(quantifier="any", members=frozenset({"Fe", "Co"})),
        )
    )

    survivors, excluded = apply_local_filters([has_fe, no_match], routing)

    assert [c.identifier for c in survivors] == ["mp-fe"]
    assert excluded[0].candidate.identifier == "mp-cu"
    assert excluded[0].reason == "element_mismatch"


def test_normalize_maximize_maps_highest_value_to_one():
    """For a 'maximize' target, the largest value is best and maps to 1, the
    smallest to 0, and the rest scale linearly between."""
    assert normalize([1.0, 2.0, 3.0], "maximize") == [0.0, 0.5, 1.0]


def test_normalize_minimize_maps_lowest_value_to_one():
    """For a 'minimize' target, the smallest value is best and maps to 1, the
    largest to 0 — the direction flips the scale."""
    assert normalize([1.0, 2.0, 3.0], "minimize") == [1.0, 0.5, 0.0]


def test_normalize_rejects_unknown_direction():
    """The Literal is only a hint at runtime, so normalize guards the direction
    itself — an unknown one is refused rather than silently treated as minimize."""
    with pytest.raises(ValueError, match="direction"):
        normalize([1.0, 2.0, 3.0], "max")


def test_normalize_degenerate_pool_maps_all_to_neutral_half():
    """When every value is the same the property carries no ranking signal, so
    each maps to a neutral 0.5 rather than dividing by a zero span."""
    assert normalize([2.0, 2.0, 2.0], "maximize") == [0.5, 0.5, 0.5]
    assert normalize([2.0, 2.0, 2.0], "minimize") == [0.5, 0.5, 0.5]


def test_normalize_rejects_empty_pool():
    """There is nothing to scale when no values are given, so normalize refuses
    an empty pool with a clear error rather than failing deep in min()."""
    with pytest.raises(ValueError, match="empty pool"):
        normalize([], "maximize")


def test_desirability_maximize_ramps_linearly_from_lower_to_target():
    """A 'maximize' curve is zero at the lower anchor and one at the target
    (saturation) anchor; with a linear curvature the midpoint is 0.5."""
    assert (
        desirability_curve(3.0, "maximize", lower=2.0, target=4.0, upper=None, curvature=1.0) == 0.5
    )


def test_desirability_maximize_clamps_outside_the_ramp():
    """Below the lower anchor a 'maximize' value earns nothing (0); at or above
    the saturation target it is fully desirable (1) and does not keep climbing."""
    below = desirability_curve(1.0, "maximize", lower=2.0, target=4.0, upper=None, curvature=1.0)
    saturated = desirability_curve(
        9.0, "maximize", lower=2.0, target=4.0, upper=None, curvature=1.0
    )

    assert below == 0.0
    assert saturated == 1.0


def test_desirability_minimize_ramps_down_from_target_to_upper():
    """A 'minimize' curve is one at/below the target (the good low value) and
    falls to zero at the upper anchor; the midpoint is 0.5, and it clamps flat
    outside the ramp."""
    mid = desirability_curve(3.0, "minimize", lower=None, target=2.0, upper=4.0, curvature=1.0)
    good = desirability_curve(1.0, "minimize", lower=None, target=2.0, upper=4.0, curvature=1.0)
    bad = desirability_curve(9.0, "minimize", lower=None, target=2.0, upper=4.0, curvature=1.0)

    assert mid == 0.5
    assert good == 1.0
    assert bad == 0.0


def test_desirability_target_peaks_at_the_sweet_spot_and_falls_both_ways():
    """A 'target' curve is the moderate-is-best case: desirability is 1 at the
    target, 0 at either outer anchor, and rises/falls linearly on each arm, so the
    midpoint of each arm is 0.5. Outside the window it is 0."""
    peak = desirability_curve(5.0, "target", lower=2.0, target=5.0, upper=8.0, curvature=1.0)
    rising_mid = desirability_curve(3.5, "target", lower=2.0, target=5.0, upper=8.0, curvature=1.0)
    falling_mid = desirability_curve(6.5, "target", lower=2.0, target=5.0, upper=8.0, curvature=1.0)
    outside = desirability_curve(9.0, "target", lower=2.0, target=5.0, upper=8.0, curvature=1.0)

    assert peak == 1.0
    assert rising_mid == 0.5
    assert falling_mid == 0.5
    assert outside == 0.0


def test_desirability_curvature_bends_credit_for_off_target_values():
    """Curvature is the exponent on the ramp fraction: >1 is strict (the midpoint
    earns only 0.25, credit comes only near the ideal), <1 is lenient (the
    midpoint earns ~0.71, partial credit accrues fast)."""
    strict = desirability_curve(3.0, "maximize", lower=2.0, target=4.0, upper=None, curvature=2.0)
    lenient = desirability_curve(3.0, "maximize", lower=2.0, target=4.0, upper=None, curvature=0.5)

    assert strict == 0.25
    assert lenient == pytest.approx(0.7071, abs=1e-4)


def test_resolve_bounds_maximize_falls_back_to_pool_extremes():
    """With no anchors supplied, a 'maximize' target borrows the pool's range: the
    lower (zero) anchor is the pool minimum and the saturation anchor is the pool
    maximum, so the curve spans the actual candidates."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    lower, peak, upper = resolve_bounds(target, [2.0, 3.0, 4.0])

    assert lower == 2.0
    assert peak == 4.0


def test_resolve_bounds_minimize_falls_back_to_pool_extremes():
    """For 'minimize', smaller is better: the pool minimum is the fully-desirable
    target and the pool maximum is the zero-desirability upper anchor."""
    target = RankingTarget(property_name="density", direction="minimize", weight=1.0)

    lower, peak, upper = resolve_bounds(target, [2.0, 3.0, 4.0])

    assert peak == 2.0
    assert upper == 4.0


def test_resolve_bounds_target_keeps_peak_and_pools_the_outer_anchors():
    """A 'target' direction always names its sweet spot, so the peak is taken
    verbatim while the omitted outer anchors fall back to the pool extremes."""
    target = RankingTarget(property_name="band_gap", direction="target", weight=1.0, target=3.0)

    lower, peak, upper = resolve_bounds(target, [1.0, 3.0, 5.0])

    assert lower == 1.0
    assert peak == 3.0
    assert upper == 5.0


def test_resolve_bounds_prefers_supplied_anchors_over_the_pool():
    """A spec-supplied anchor is absolute and overrides the pool fallback, so the
    curve does not drift as the candidate set changes."""
    target = RankingTarget(
        property_name="band_gap", direction="maximize", weight=1.0, lower=0.0, target=10.0
    )

    lower, peak, _upper = resolve_bounds(target, [2.0, 3.0, 4.0])

    assert lower == 0.0
    assert peak == 10.0


def test_score_desirability_maps_present_values_through_the_curve_unflagged():
    """Across the pool, each present value is mapped to its desirability by the
    resolved curve — here a 'maximize' over [2, 4] puts the low candidate at 0 and
    the high one at 1 — and neither is flagged because nothing was imputed."""
    low = _candidate("mp-low", "PbO", band_gap=2.0)
    high = _candidate("mp-high", "SnO2", band_gap=4.0)
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    scored = score_desirability([low, high], target)

    assert scored == [(0.0, False), (1.0, False)]


def test_score_desirability_imputes_neutral_half_for_missing_and_flags_it():
    """A candidate lacking the ranked property is imputed a neutral 0.5 (so a
    single gap cannot zero the geometric mean) and flagged, while present
    candidates still score by the curve."""
    low = _candidate("mp-low", "PbO", band_gap=2.0)
    high = _candidate("mp-has", "SnO2", band_gap=4.0)
    blank = _candidate("mp-missing", "NaCl")
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    scored = score_desirability([low, high, blank], target)

    assert scored[0] == (0.0, False)
    assert scored[1] == (1.0, False)
    assert scored[2] == (0.5, True)


def test_desirability_degenerate_span_maps_to_neutral_half():
    """When an anchor span collapses to zero — every candidate shares the value,
    so the property carries no signal — the curve returns a neutral 0.5 rather
    than dividing by zero, matching the normaliser's degenerate behaviour."""
    flat = desirability_curve(4.0, "maximize", lower=4.0, target=4.0, upper=None, curvature=1.0)

    assert flat == 0.5


def test_score_desirability_all_missing_imputes_half_without_evaluating_the_curve():
    """When no candidate has the ranked property there is no pool to anchor the
    curve, so every candidate is imputed the neutral 0.5 and flagged rather than
    the curve being evaluated against undefined bounds."""
    a = _candidate("mp-a", "NaCl")
    b = _candidate("mp-b", "KCl")
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    scored = score_desirability([a, b], target)

    assert scored == [(0.5, True), (0.5, True)]


def test_apply_hard_filters_excludes_candidate_below_min():
    """A candidate whose value falls below a constraint's min is dropped, and the
    exclusion records the property, the below_min reason, the value and the bound."""
    candidate = _candidate("mp-low", "PbO", band_gap=0.4)
    survivors, excluded = apply_hard_filters(
        [candidate], (Constraint(property_name="band_gap", min=1.0),)
    )

    assert survivors == []
    assert len(excluded) == 1
    drop = excluded[0]
    assert drop.candidate.identifier == "mp-low"
    assert drop.property_name == "band_gap"
    assert drop.reason == "below_min"
    assert drop.value == 0.4
    assert drop.bound == 1.0


def test_apply_hard_filters_excludes_candidate_above_max():
    """A candidate whose value exceeds a constraint's max is dropped, recording
    the above_max reason against the bound it broke."""
    candidate = _candidate("mp-high", "SnO2", band_gap=4.2)
    survivors, excluded = apply_hard_filters(
        [candidate], (Constraint(property_name="band_gap", max=3.0),)
    )

    assert survivors == []
    assert len(excluded) == 1
    drop = excluded[0]
    assert drop.reason == "above_max"
    assert drop.value == 4.2
    assert drop.bound == 3.0


def test_apply_hard_filters_keeps_candidates_within_inclusive_bounds():
    """Bounds are inclusive: a value strictly inside survives, and so does one
    sitting exactly on the min or the max."""
    inside = _candidate("mp-inside", "TiO2", band_gap=2.0)
    at_min = _candidate("mp-atmin", "ZnO", band_gap=1.0)
    at_max = _candidate("mp-atmax", "GaN", band_gap=3.0)
    survivors, excluded = apply_hard_filters(
        [inside, at_min, at_max],
        (Constraint(property_name="band_gap", min=1.0, max=3.0),),
    )

    assert [c.identifier for c in survivors] == ["mp-inside", "mp-atmin", "mp-atmax"]
    assert excluded == []


def test_apply_hard_filters_records_first_violation_in_constraint_order():
    """A candidate breaking several constraints records only the first violation
    in constraint order; reversing the order changes which reason is reported."""
    candidate = _candidate("mp-bad", "PbO", band_gap=0.4, density=10.0)
    band_gap_min = Constraint(property_name="band_gap", min=1.0)
    density_max = Constraint(property_name="density", max=5.0)

    _, excluded_a = apply_hard_filters([candidate], (band_gap_min, density_max))
    _, excluded_b = apply_hard_filters([candidate], (density_max, band_gap_min))

    assert excluded_a[0].property_name == "band_gap"
    assert excluded_b[0].property_name == "density"


def test_apply_hard_filters_excludes_candidate_missing_the_constrained_property():
    """A hard constraint can't be verified when the candidate has no value for
    that property, so the candidate is excluded with the missing_data reason
    rather than silently passing the gate."""
    candidate = _candidate("mp-nogap", "TiO2")  # no band_gap at all
    survivors, excluded = apply_hard_filters(
        [candidate], (Constraint(property_name="band_gap", min=1.0),)
    )

    assert survivors == []
    assert len(excluded) == 1
    assert excluded[0].reason == "missing_data"
    assert excluded[0].property_name == "band_gap"
    assert excluded[0].value is None


def test_apply_hard_filters_treats_present_but_missing_value_as_missing_data():
    """A property retrieved but flagged missing (present in the bag, no number)
    also can't be checked, so it is excluded with missing_data like an absent one."""
    candidate = Candidate(
        identifier="mp-flagged",
        formula="TiO2",
        properties={
            "band_gap": PropertyValue(
                value=None,
                unit="eV",
                missing=True,
                provenance=Provenance(
                    source="Materials Project", record_id="mp-flagged", method="computational"
                ),
            )
        },
    )
    survivors, excluded = apply_hard_filters(
        [candidate], (Constraint(property_name="band_gap", min=1.0),)
    )

    assert survivors == []
    assert excluded[0].reason == "missing_data"


def test_drop_missing_excluded_drops_candidate_missing_an_exclude_target():
    """A candidate with no value for a ranking target whose policy is 'exclude'
    is dropped with the missing_data reason; one that has the value survives."""
    has_it = _candidate("mp-has", "GaN", band_gap=2.0)
    lacks_it = _candidate("mp-lacks", "TiO2")  # no band_gap
    target = RankingTarget(
        property_name="band_gap", direction="maximize", weight=1.0, on_missing="exclude"
    )

    survivors, excluded = drop_missing_excluded([has_it, lacks_it], (target,))

    assert [c.identifier for c in survivors] == ["mp-has"]
    assert len(excluded) == 1
    assert excluded[0].candidate.identifier == "mp-lacks"
    assert excluded[0].property_name == "band_gap"
    assert excluded[0].reason == "missing_data"


def test_drop_missing_excluded_keeps_candidate_missing_an_impute_target():
    """A candidate missing a target whose policy is 'impute_medium' is NOT dropped
    here — its gap is the scorer's to impute, so it survives this stage untouched."""
    lacks_it = _candidate("mp-lacks", "TiO2")  # no band_gap
    target = RankingTarget(
        property_name="band_gap", direction="maximize", weight=1.0, on_missing="impute_medium"
    )

    survivors, excluded = drop_missing_excluded([lacks_it], (target,))

    assert [c.identifier for c in survivors] == ["mp-lacks"]
    assert excluded == []


def test_score_target_normalizes_present_values_unflagged():
    """With every candidate carrying the value, score_target normalizes them just
    as normalize() does and flags none — the contributions feed the weighted average."""
    a = _candidate("mp-a", "X", band_gap=1.0)
    b = _candidate("mp-b", "Y", band_gap=2.0)
    c = _candidate("mp-c", "Z", band_gap=3.0)
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = score_target([a, b, c], target)

    assert result == [(0.0, False), (0.5, False), (1.0, False)]


def test_score_target_imputes_neutral_half_for_missing_and_flags_it():
    """A candidate missing the value is imputed a neutral 0.5 and flagged, while
    the present values normalize among themselves — the gap is neither in the
    pool that sets min/max nor credited as if measured."""
    a = _candidate("mp-a", "X", band_gap=1.0)
    gap = _candidate("mp-gap", "Y")  # no band_gap
    c = _candidate("mp-c", "Z", band_gap=3.0)
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = score_target([a, gap, c], target)

    assert result == [(0.0, False), (0.5, True), (1.0, False)]


def test_score_target_all_missing_imputes_half_without_normalizing_empty():
    """When no candidate carries the value the present pool is empty, so the
    scorer imputes 0.5 for all rather than calling normalize on nothing."""
    a = _candidate("mp-a", "X")  # no band_gap
    b = _candidate("mp-b", "Y")  # no band_gap
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)

    result = score_target([a, b], target)

    assert result == [(0.5, True), (0.5, True)]
