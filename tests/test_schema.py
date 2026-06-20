"""Tests for the core data models in materials_triage.core.schema."""

import pytest
from pydantic import ValidationError

from materials_triage.core.schema import (
    Candidate,
    Constraint,
    ExcludedCandidate,
    PropertyValue,
    Provenance,
    RankingTarget,
    ScoredCandidate,
    TriageResult,
    TriageSpec,
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


def test_ranking_target_rejects_weight_above_one():
    """Weights are proportional shares, so a single weight cannot exceed 1."""
    with pytest.raises(ValidationError):
        RankingTarget(property_name="band_gap", direction="maximize", weight=1.5)


def test_ranking_target_defaults_on_missing_to_flag_only():
    """Absent a choice, a missing value is ranked-but-flagged — never dropped
    or guessed — the project's honest default."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=0.5)

    assert target.on_missing == "flag_only"


def test_triagespec_requires_at_least_one_constraint():
    """A spec with no hard filter is not a triage — without any constraint,
    nothing is gated, so the spec is refused at construction."""
    with pytest.raises(ValidationError, match="at least one constraint"):
        TriageSpec()


def test_triagespec_assembles_a_fully_populated_request():
    """A spec carrying every kind of rule — filters, ranking, composition —
    constructs and exposes each one for the pipeline to read."""
    spec = TriageSpec(
        constraints=(
            Constraint(property_name="band_gap", min=1.0, max=3.0),
            Constraint(property_name="energy_above_hull", max=0.05),
        ),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=0.6),
            RankingTarget(property_name="density", direction="minimize", weight=0.4),
        ),
        required_elements=frozenset({"Ti", "O"}),
        excluded_elements=frozenset({"Pb"}),
        max_nelements=4,
    )

    assert len(spec.constraints) == 2
    assert len(spec.ranking_targets) == 2
    assert spec.required_elements == frozenset({"Ti", "O"})
    assert spec.excluded_elements == frozenset({"Pb"})
    assert spec.max_nelements == 4


def test_triagespec_allows_same_property_as_constraint_and_ranking_target():
    """A dual-role property is legitimate — gate band gap as a hard filter and
    also prefer higher band gap in ranking — so per-list dedup must not reject
    a property that appears once in each list."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )

    assert spec.constraints[0].property_name == "band_gap"
    assert spec.ranking_targets[0].property_name == "band_gap"


def test_triagespec_rejects_unknown_required_element():
    """Composition rules name real chemical elements; a symbol that isn't on the
    periodic table is an authoring error, so the spec refuses it."""
    with pytest.raises(ValidationError, match="Xx"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            required_elements=frozenset({"Fe", "Xx"}),
        )


def test_triagespec_rejects_more_required_elements_than_max_nelements():
    """Demanding more distinct elements than the cap allows admits nothing —
    the two rules contradict, so the spec is refused."""
    with pytest.raises(ValidationError, match="max_nelements"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            required_elements=frozenset({"Li", "Fe", "O"}),
            max_nelements=2,
        )


def test_triagespec_rejects_nonpositive_max_nelements():
    """A material has at least one element, so a cap below one admits nothing
    and is refused."""
    with pytest.raises(ValidationError):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            max_nelements=0,
        )


def test_triagespec_rejects_element_required_and_excluded():
    """An element cannot be both demanded and forbidden — that admits nothing —
    so a spec listing one in both sets is refused."""
    with pytest.raises(ValidationError, match="Fe"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            required_elements=frozenset({"Fe"}),
            excluded_elements=frozenset({"Fe"}),
        )


def test_triagespec_rejects_duplicate_constraint_property():
    """Two constraints on the same property are an authoring mistake — the
    bound should be one constraint, not two — so the spec refuses the pair."""
    with pytest.raises(ValidationError, match="band_gap"):
        TriageSpec(
            constraints=(
                Constraint(property_name="band_gap", min=1.0),
                Constraint(property_name="band_gap", max=5.0),
            )
        )


def test_triagespec_requires_ranking_weights_to_sum_to_one():
    """Ranking weights are proportional shares, so across a spec they must add
    up to one — a set that doesn't is an incomplete normalisation, so reject it."""
    with pytest.raises(ValidationError, match="sum to 1"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            ranking_targets=(
                RankingTarget(property_name="density", direction="minimize", weight=0.5),
                RankingTarget(property_name="bulk_modulus", direction="maximize", weight=0.3),
            ),
        )


def test_triagespec_accepts_ranking_weights_that_sum_to_one():
    """A correctly-normalised set of shares is accepted."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        ranking_targets=(
            RankingTarget(property_name="density", direction="minimize", weight=0.7),
            RankingTarget(property_name="bulk_modulus", direction="maximize", weight=0.3),
        ),
    )

    assert len(spec.ranking_targets) == 2


def test_triagespec_rejects_duplicate_ranking_target_property():
    """Two ranking targets on the same property would double-count it in the
    weighted average — an authoring mistake — so the spec refuses the pair."""
    with pytest.raises(ValidationError, match="density"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            ranking_targets=(
                RankingTarget(property_name="density", direction="minimize", weight=0.5),
                RankingTarget(property_name="density", direction="maximize", weight=0.5),
            ),
        )


def test_scored_candidate_carries_candidate_score_and_contributions():
    """A ranked survivor pairs the material with the composite score it earned
    and the per-target contributions that produced it, so the audit can show
    the math."""
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
    scored = ScoredCandidate(
        candidate=candidate,
        score=0.82,
        contributions={"band_gap": 0.82},
    )

    assert scored.candidate.identifier == "mp-aaaaadyf"
    assert scored.score == 0.82
    assert scored.contributions["band_gap"] == 0.82


def test_scored_candidate_rejects_non_finite_score():
    """A NaN or infinite score breaks every ordering comparison, so the ranker
    can never assign one — the model refuses it."""
    candidate = Candidate(identifier="mp-aaaaadyf", formula="TiO2")
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            ScoredCandidate(candidate=candidate, score=bad)


def test_scored_candidate_records_which_properties_were_flagged_missing():
    """Ranking writes down the properties it flagged as missing, so the renderer
    reads the flags off the result instead of re-deriving them — the run is
    self-describing. By default nothing is flagged."""
    candidate = Candidate(identifier="mp-aaaaadyf", formula="TiO2")

    flagged = ScoredCandidate(
        candidate=candidate, score=0.5, flagged_missing=frozenset({"bulk_modulus"})
    )
    plain = ScoredCandidate(candidate=candidate, score=0.5)

    assert flagged.flagged_missing == frozenset({"bulk_modulus"})
    assert plain.flagged_missing == frozenset()


def test_excluded_candidate_records_structured_drop_reason():
    """A dropped candidate records why — the property, the machine-readable
    reason, the offending value and the bound it violated — so the audit
    explains the exclusion without re-reading the spec."""
    candidate = Candidate(
        identifier="mp-aaaaadyf",
        formula="TiO2",
        properties={
            "band_gap": PropertyValue(
                value=0.4,
                unit="eV",
                provenance=Provenance(source="Materials Project", record_id="mp-aaaaadyf"),
            )
        },
    )
    excluded = ExcludedCandidate(
        candidate=candidate,
        property_name="band_gap",
        reason="below_min",
        value=0.4,
        bound=1.0,
    )

    assert excluded.candidate.identifier == "mp-aaaaadyf"
    assert excluded.property_name == "band_gap"
    assert excluded.reason == "below_min"
    assert excluded.value == 0.4
    assert excluded.bound == 1.0


def test_excluded_candidate_rejects_reason_inconsistent_with_bound():
    """The reason names the direction of the violation, so it must agree with
    the numbers: 'below_min' cannot hold when the value is not below the bound."""
    candidate = Candidate(identifier="mp-aaaaadyf", formula="TiO2")
    with pytest.raises(ValidationError):
        ExcludedCandidate(
            candidate=candidate,
            property_name="band_gap",
            reason="below_min",
            value=5.0,
            bound=1.0,
        )


def test_excluded_candidate_above_max_requires_value_above_bound():
    """The symmetric direction: 'above_max' holds only when the value exceeds
    the bound; a coherent one is accepted and an incoherent one refused."""
    candidate = Candidate(identifier="mp-aaaaadyf", formula="TiO2")

    ok = ExcludedCandidate(
        candidate=candidate,
        property_name="band_gap",
        reason="above_max",
        value=4.0,
        bound=3.0,
    )
    assert ok.reason == "above_max"

    with pytest.raises(ValidationError):
        ExcludedCandidate(
            candidate=candidate,
            property_name="band_gap",
            reason="above_max",
            value=1.0,
            bound=3.0,
        )


def test_triage_result_holds_ranked_survivors_and_excluded_set():
    """The result the renderers read carries the ranked survivors and the
    dropped candidates side by side."""
    survivor = Candidate(identifier="mp-survivor", formula="TiO2")
    dropped = Candidate(identifier="mp-dropped", formula="PbO")
    result = TriageResult(
        ranked=(ScoredCandidate(candidate=survivor, score=0.9),),
        excluded=(
            ExcludedCandidate(
                candidate=dropped,
                property_name="band_gap",
                reason="below_min",
                value=0.4,
                bound=1.0,
            ),
        ),
    )

    assert result.ranked[0].candidate.identifier == "mp-survivor"
    assert result.excluded[0].candidate.identifier == "mp-dropped"


def test_triage_result_rejects_ranked_out_of_score_order():
    """Rank is read off position, so that only means something if the survivors
    are stored best-first; an out-of-order ranked tuple is refused."""
    a = Candidate(identifier="mp-a", formula="TiO2")
    b = Candidate(identifier="mp-b", formula="ZnO")
    with pytest.raises(ValidationError):
        TriageResult(
            ranked=(
                ScoredCandidate(candidate=a, score=0.3),
                ScoredCandidate(candidate=b, score=0.9),
            )
        )


def test_triage_result_assembles_a_full_outcome():
    """A complete result — several ranked survivors carrying contributions and
    missing flags, alongside several dropped candidates with reasons — assembles
    and exposes each part for the renderers."""
    top = Candidate(identifier="mp-top", formula="TiO2")
    second = Candidate(identifier="mp-second", formula="ZnO")
    dropped_low = Candidate(identifier="mp-low", formula="PbO")
    dropped_high = Candidate(identifier="mp-high", formula="SnO2")
    result = TriageResult(
        ranked=(
            ScoredCandidate(
                candidate=top,
                score=0.91,
                contributions={"band_gap": 0.6, "density": 0.31},
            ),
            ScoredCandidate(
                candidate=second,
                score=0.74,
                contributions={"band_gap": 0.5, "density": 0.24},
                flagged_missing=frozenset({"bulk_modulus"}),
            ),
        ),
        excluded=(
            ExcludedCandidate(
                candidate=dropped_low,
                property_name="band_gap",
                reason="below_min",
                value=0.4,
                bound=1.0,
            ),
            ExcludedCandidate(
                candidate=dropped_high,
                property_name="band_gap",
                reason="above_max",
                value=4.2,
                bound=3.0,
            ),
        ),
    )

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-top", "mp-second"]
    assert result.ranked[1].flagged_missing == frozenset({"bulk_modulus"})
    assert {ec.reason for ec in result.excluded} == {"below_min", "above_max"}
