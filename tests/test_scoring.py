"""Tests for the deterministic scoring helpers in materials_triage.core.scoring."""

import pytest

from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RankingTarget,
)
from materials_triage.core.scoring import (
    apply_hard_filters,
    drop_missing_excluded,
    normalize,
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
                provenance=Provenance(source="Materials Project", record_id=identifier),
            )
            for name, value in props.items()
        },
    )


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
                provenance=Provenance(source="Materials Project", record_id="mp-flagged"),
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
