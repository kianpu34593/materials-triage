"""Tests for the core data models in materials_triage.core.schema."""

import pytest
from pydantic import ValidationError

from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RankingTarget,
)


def test_provenance_carries_its_source():
    """A Provenance records where a scientific value came from."""
    prov = Provenance(source="Materials Project", record_id="mp-2657")

    assert prov.source == "Materials Project"


def test_provenance_reports_its_record_id():
    """A receipt names the specific record it came from, so a citation can resolve."""
    prov = Provenance(source="Materials Project", record_id="mp-2657")

    assert prov.record_id == "mp-2657"


def test_provenance_rejects_blank_source():
    """A receipt with no issuer is meaningless, so a blank source is refused."""
    with pytest.raises(ValidationError):
        Provenance(source="", record_id="mp-2657")


def test_provenance_is_immutable():
    """Once a value is tagged with its origin, that tag cannot be changed."""
    prov = Provenance(source="Materials Project", record_id="mp-2657")

    with pytest.raises(ValidationError):
        prov.source = "OQMD"


def test_property_value_reports_number_and_source():
    """A retrieved value reports its number and where it came from."""
    pv = PropertyValue(
        value=3.2,
        unit="eV",
        provenance=Provenance(source="Materials Project", record_id="mp-2657"),
    )

    assert pv.value == 3.2
    assert pv.provenance.source == "Materials Project"


def test_missing_property_value_cannot_carry_a_number():
    """A value the database lacks is missing — it must not also report a number."""
    with pytest.raises(ValidationError):
        PropertyValue(
            value=3.2,
            unit="eV",
            missing=True,
            provenance=Provenance(source="Materials Project", record_id="mp-2657"),
        )


def test_present_property_value_must_carry_a_number():
    """A value that isn't marked missing must report an actual number."""
    with pytest.raises(ValidationError):
        PropertyValue(
            value=None,
            unit="eV",
            missing=False,
            provenance=Provenance(source="Materials Project", record_id="mp-2657"),
        )


def test_missing_property_value_still_reports_its_source():
    """A value the database lacks still records where we looked for it."""
    pv = PropertyValue(
        value=None,
        unit="eV",
        missing=True,
        provenance=Provenance(source="Materials Project", record_id="mp-2657"),
    )

    assert pv.missing is True
    assert pv.value is None
    assert pv.provenance.source == "Materials Project"


def test_candidate_identifies_material_and_exposes_named_property():
    """A candidate is keyed by the id the source returned, and serves its
    properties by name for the filter and ranker to read."""
    candidate = Candidate(
        identifier="mp-aaaaadyf",
        formula="TiO2",
        properties={
            "band_gap": PropertyValue(
                value=1.7719,
                unit="eV",
                provenance=Provenance(source="Materials Project", record_id="mp-aaaaadyf"),
            )
        },
    )

    assert candidate.identifier == "mp-aaaaadyf"
    assert candidate.properties["band_gap"].value == 1.7719


def test_candidate_distinguishes_absent_from_missing_property():
    """Never-retrieved and retrieved-but-empty are different states: an absent
    property is simply not in the bag, while a missing one is present and flagged."""
    candidate = Candidate(
        identifier="mp-aaaaadyf",
        formula="TiO2",
        properties={
            "band_gap": PropertyValue(
                value=None,
                unit="eV",
                missing=True,
                provenance=Provenance(source="Materials Project", record_id="mp-aaaaadyf"),
            )
        },
    )

    # retrieved-but-missing: present in the bag, flagged, no number
    assert "band_gap" in candidate.properties
    assert candidate.properties["band_gap"].missing is True

    # absent: never retrieved, so not in the bag at all
    assert "formation_energy_per_atom" not in candidate.properties


def test_constraint_gates_a_property_with_a_bound():
    """A hard constraint names the property it gates and the bound to enforce."""
    constraint = Constraint(property_name="band_gap", min=3.0)

    assert constraint.property_name == "band_gap"
    assert constraint.min == 3.0


def test_constraint_must_bound_something():
    """A constraint with neither a min nor a max gates nothing — it's incoherent."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap")


def test_constraint_rejects_impossible_band():
    """A min above the max admits nothing — an impossible window is refused."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap", min=5.0, max=3.0)


def test_constraint_rejects_infinite_min():
    """An infinite lower bound is not a real limit — it gates nothing, so refuse it."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap", min=float("inf"))


def test_constraint_rejects_infinite_max():
    """An infinite upper bound is not a real limit — it gates nothing, so refuse it."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap", max=float("inf"))


def test_constraint_rejects_negative_infinite_bound():
    """A -inf bound is no real limit either — refuse it like +inf."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap", min=float("-inf"))


def test_constraint_rejects_nan_bound():
    """NaN breaks every ordering comparison, so it can never be a coherent bound."""
    with pytest.raises(ValidationError):
        Constraint(property_name="band_gap", max=float("nan"))


def test_ranking_target_names_property_with_direction_and_weight():
    """A ranking target tells the ranker which property to score, which way is
    better, and how much it counts in the weighted average."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=0.5)

    assert target.property_name == "band_gap"
    assert target.direction == "maximize"
    assert target.weight == 0.5


def test_ranking_target_requires_positive_weight():
    """A zero or negative weight contributes nothing to the weighted average."""
    with pytest.raises(ValidationError):
        RankingTarget(property_name="band_gap", direction="maximize", weight=0.0)


def test_ranking_target_defaults_on_missing_to_flag_only():
    """Absent a choice, a missing value is ranked-but-flagged — never dropped
    or guessed — the project's honest default."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=0.5)

    assert target.on_missing == "flag_only"
