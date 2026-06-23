"""Tests for the core data models in materials_triage.core.schema."""

import pytest
from pydantic import ValidationError

from materials_triage.core.schema import (
    BooleanConstraint,
    Candidate,
    Constraint,
    CountConstraint,
    ElementPredicate,
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
    prov = Provenance(source="Materials Project", record_id="mp-2657", method="computational")

    assert prov.source == "Materials Project"


def test_provenance_reports_its_record_id():
    """A receipt names the specific record it came from, so a citation can resolve."""
    prov = Provenance(source="Materials Project", record_id="mp-2657", method="computational")

    assert prov.record_id == "mp-2657"


def test_provenance_rejects_blank_source():
    """A receipt with no issuer is meaningless, so a blank source is refused."""
    with pytest.raises(ValidationError):
        Provenance(source="", record_id="mp-2657", method="computational")


def test_provenance_is_immutable():
    """Once a value is tagged with its origin, that tag cannot be changed."""
    prov = Provenance(source="Materials Project", record_id="mp-2657", method="computational")

    with pytest.raises(ValidationError):
        prov.source = "OQMD"


def test_provenance_records_its_derivation_method():
    """A value's origin can record how it was derived (e.g. a DFT computation)."""
    prov = Provenance(
        source="Materials Project",
        record_id="mp-2657",
        method="computational",
    )

    assert prov.method == "computational"


def test_provenance_requires_a_derivation_method():
    """A value's origin must state how it was derived — method is not optional."""
    with pytest.raises(ValidationError):
        Provenance(source="Materials Project", record_id="mp-2657")


def test_provenance_accepts_literature_as_a_method():
    """A value sourced from a published abstract declares the 'literature' method."""
    prov = Provenance(source="openalex", record_id="W123", method="literature")

    assert prov.method == "literature"


def test_provenance_records_the_xc_functional_it_was_computed_with():
    """A DFT value can record which exchange-correlation functional produced it."""
    prov = Provenance(
        source="Materials Project",
        record_id="mp-2657",
        method="computational",
        xc_functional="r2SCAN",
    )

    assert prov.xc_functional == "r2SCAN"


def test_provenance_leaves_xc_functional_unknown_by_default():
    """The functional is honestly unknown unless the source states it — not assumed."""
    prov = Provenance(source="openalex", record_id="W123", method="literature")

    assert prov.xc_functional is None


def test_property_value_reports_number_and_source():
    """A retrieved value reports its number and where it came from."""
    pv = PropertyValue(
        value=3.2,
        unit="eV",
        provenance=Provenance(
            source="Materials Project", record_id="mp-2657", method="computational"
        ),
    )

    assert pv.value == 3.2
    assert pv.provenance.source == "Materials Project"


def test_property_value_accepts_a_dimensionless_unit():
    """Some real, retrieved values are genuinely dimensionless (refractive index,
    dielectric constant, Poisson ratio). PropertyValue is a deterministic-layer
    model — the adapter fills it from trusted API data, the LLM never builds it —
    so unit=None honestly means 'no unit', not a relaxed LLM contract."""
    pv = PropertyValue(
        value=2.05,
        unit=None,
        provenance=Provenance(
            source="Materials Project", record_id="mp-2657", method="computational"
        ),
    )

    assert pv.value == 2.05
    assert pv.unit is None


def test_missing_property_value_cannot_carry_a_number():
    """A value the database lacks is missing — it must not also report a number."""
    with pytest.raises(ValidationError):
        PropertyValue(
            value=3.2,
            unit="eV",
            missing=True,
            provenance=Provenance(
                source="Materials Project", record_id="mp-2657", method="computational"
            ),
        )


def test_present_property_value_must_carry_a_number():
    """A value that isn't marked missing must report an actual number."""
    with pytest.raises(ValidationError):
        PropertyValue(
            value=None,
            unit="eV",
            missing=False,
            provenance=Provenance(
                source="Materials Project", record_id="mp-2657", method="computational"
            ),
        )


def test_missing_property_value_still_reports_its_source():
    """A value the database lacks still records where we looked for it."""
    pv = PropertyValue(
        value=None,
        unit="eV",
        missing=True,
        provenance=Provenance(
            source="Materials Project", record_id="mp-2657", method="computational"
        ),
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
                provenance=Provenance(
                    source="Materials Project", record_id="mp-aaaaadyf", method="computational"
                ),
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
                provenance=Provenance(
                    source="Materials Project", record_id="mp-aaaaadyf", method="computational"
                ),
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


def test_ranking_target_defaults_on_missing_to_impute_medium():
    """Absent a choice, a missing value is kept-and-flagged — imputed at the
    neutral midpoint so it still ranks, never silently dropped — the project's
    honest default."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=0.5)

    assert target.on_missing == "impute_medium"


def test_element_predicate_carries_its_quantifier_and_members():
    """An ElementPredicate is one quantified composition filter: a quantifier over
    a set of element symbols. 'any' means HAS-ANY — at least one member present —
    which a require (HAS-ALL) / exclude (HAS-NONE) pair cannot express."""
    pred = ElementPredicate(quantifier="any", members=frozenset({"Fe", "Zn", "Ti"}))

    assert pred.quantifier == "any"
    assert pred.members == frozenset({"Fe", "Zn", "Ti"})


def test_element_predicate_rejects_non_element_symbols():
    """An ElementPredicate validates its members against the canonical element set
    at construction, so a hallucinated symbol like 'Xx' never reaches the spec —
    the same guard the require/exclude path always had, now on the unified
    quantified predicate."""
    with pytest.raises(ValidationError, match="Xx"):
        ElementPredicate(quantifier="any", members=frozenset({"Fe", "Xx"}))


def test_triagespec_requires_at_least_one_hard_filter():
    """A spec with no hard filter is not a triage — without any gating rule,
    nothing is gated, so the spec is refused at construction."""
    with pytest.raises(ValidationError, match="at least one hard filter"):
        TriageSpec()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"boolean_constraints": (BooleanConstraint(property_name="is_stable", required=True),)},
        {
            "element_predicates": (
                ElementPredicate(quantifier="any", members=frozenset({"Fe", "Co"})),
            )
        },
        {"count": CountConstraint(max=3)},
    ],
)
def test_triagespec_accepts_any_kind_of_hard_filter(kwargs):
    """Any hard-filter kind satisfies the gate, not only a numeric constraint —
    a request whose only gate is a boolean, an element predicate, or a count cap
    is a valid triage and must construct without a numeric bound."""
    spec = TriageSpec(**kwargs)

    assert spec.constraints == ()


def test_triagespec_rejects_duplicate_boolean_property():
    """A boolean property required both True and False is an incoherent filter
    that drops everything, so it is refused at construction — mirroring the
    numeric-constraint dedup."""
    with pytest.raises(ValidationError, match="constrained more than once"):
        TriageSpec(
            boolean_constraints=(
                BooleanConstraint(property_name="is_stable", required=True),
                BooleanConstraint(property_name="is_stable", required=False),
            )
        )


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
        element_predicates=(
            ElementPredicate(quantifier="all", members=frozenset({"Ti", "O"})),
            ElementPredicate(quantifier="none", members=frozenset({"Pb"})),
        ),
        count=CountConstraint(max=4),
    )

    assert len(spec.constraints) == 2
    assert len(spec.ranking_targets) == 2
    assert spec.element_predicates == (
        ElementPredicate(quantifier="all", members=frozenset({"Ti", "O"})),
        ElementPredicate(quantifier="none", members=frozenset({"Pb"})),
    )
    assert spec.count == CountConstraint(max=4)


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


def test_triagespec_rejects_more_required_elements_than_count_cap():
    """Demanding more distinct elements than the count cap allows admits nothing —
    the two rules contradict, so the spec is refused. (Only "all"-quantifier members
    must all be present, so they are what the cap counts against — the cross-check a
    typed count field keeps robust instead of string-matching a property name.)"""
    with pytest.raises(ValidationError, match="count constraint caps"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            element_predicates=(
                ElementPredicate(quantifier="all", members=frozenset({"Li", "Fe", "O"})),
            ),
            count=CountConstraint(max=2),
        )


def test_count_constraint_bounds_composition_cardinality():
    """A count constraint bounds how many distinct elements a composition may have,
    as an inclusive min and/or max — the source-neutral way to say 'simple'."""
    count = CountConstraint(max=3)

    assert count.max == 3
    assert count.min is None


def test_count_constraint_must_bound_something():
    """A count constraint with neither a min nor a max bounds nothing — incoherent."""
    with pytest.raises(ValidationError):
        CountConstraint()


def test_count_constraint_rejects_min_above_max():
    """A min above the max admits no count, so an impossible window is refused."""
    with pytest.raises(ValidationError):
        CountConstraint(min=4, max=2)


def test_count_constraint_rejects_nonpositive_bound():
    """A material has at least one element, so a bound below one admits nothing."""
    with pytest.raises(ValidationError):
        CountConstraint(max=0)


def test_triagespec_rejects_element_required_and_excluded():
    """An element cannot be both demanded and forbidden — that admits nothing —
    so a spec listing one in both sets is refused."""
    with pytest.raises(ValidationError, match="Fe"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            element_predicates=(
                ElementPredicate(quantifier="all", members=frozenset({"Fe"})),
                ElementPredicate(quantifier="none", members=frozenset({"Fe"})),
            ),
        )


def test_triagespec_rejects_any_predicate_fully_excluded():
    """An "any" predicate is satisfiable only if some member can be present, so when
    every member is also forbidden by a "none" predicate the filter admits nothing
    and the spec is refused."""
    with pytest.raises(ValidationError, match="Fe"):
        TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=1.0),),
            element_predicates=(
                ElementPredicate(quantifier="any", members=frozenset({"Fe"})),
                ElementPredicate(quantifier="none", members=frozenset({"Fe"})),
            ),
        )


def test_triagespec_allows_any_predicate_partially_excluded():
    """An "any" predicate stays satisfiable while one member is still permitted, so
    excluding only some of its members is allowed."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        element_predicates=(
            ElementPredicate(quantifier="any", members=frozenset({"Fe", "Zn"})),
            ElementPredicate(quantifier="none", members=frozenset({"Fe"})),
        ),
    )
    assert len(spec.element_predicates) == 2


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
                provenance=Provenance(
                    source="Materials Project", record_id="mp-aaaaadyf", method="computational"
                ),
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
                provenance=Provenance(
                    source="Materials Project", record_id="mp-aaaaadyf", method="computational"
                ),
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


def test_excluded_candidate_missing_data_has_no_value():
    """A candidate dropped because the constrained property is missing records
    the 'missing_data' reason and carries no value (there was none to record)."""
    candidate = Candidate(identifier="mp-x", formula="TiO2")
    drop = ExcludedCandidate(candidate=candidate, property_name="band_gap", reason="missing_data")

    assert drop.reason == "missing_data"
    assert drop.value is None
    assert drop.bound is None


def test_excluded_candidate_missing_data_rejects_a_value():
    """If a value exists the candidate isn't missing data, so pairing a value
    with 'missing_data' is incoherent and refused."""
    candidate = Candidate(identifier="mp-x", formula="TiO2")
    with pytest.raises(ValidationError):
        ExcludedCandidate(
            candidate=candidate,
            property_name="band_gap",
            reason="missing_data",
            value=0.5,
        )
