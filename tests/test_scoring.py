"""Tests for the deterministic scoring helpers in materials_triage.core.scoring."""

import pytest

from materials_triage.core.schema import Candidate, Constraint, PropertyValue, Provenance
from materials_triage.core.scoring import apply_hard_filters, normalize


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
