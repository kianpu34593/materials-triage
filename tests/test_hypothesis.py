"""Tests for the hypothesis-layer data models in materials_triage.core.hypothesis.

These models carry the LLM's proposed bridges from a fuzzy goal to a queryable
spec, each grounded in literature. They are validated structured output: the LLM
is conformed to them, and anything malformed is rejected before it reaches the
deterministic core.
"""

import pytest
from pydantic import ValidationError

from materials_triage.core.hypothesis import (
    BooleanConstraintProposal,
    Citation,
    ConstraintProposal,
    CountConstraintProposal,
    ElementPredicateProposal,
    Hypothesis,
    RankingProposal,
    compile_spec,
)
from materials_triage.core.schema import (
    BooleanConstraint,
    Constraint,
    CountConstraint,
    ElementPredicate,
    RankingTarget,
    TriageSpec,
)


def test_citation_carries_its_source_record_and_title():
    """A Citation is the untrusted-DATA analog of Provenance: it records which
    literature record a hypothesis was grounded in, so synthesis can cite it and
    the output validator can confirm the reference resolves."""
    cite = Citation(
        source="OpenAlex",
        record_id="W2741809807",
        title="Wide-band-gap oxide semiconductors for transparent electronics",
    )

    assert cite.source == "OpenAlex"
    assert cite.record_id == "W2741809807"
    assert cite.title == "Wide-band-gap oxide semiconductors for transparent electronics"


def test_citation_is_immutable():
    """A grounding receipt must not be tamperable once attached to a claim, so a
    Citation is frozen (characterization: mirrors the Provenance convention)."""
    cite = Citation(source="OpenAlex", record_id="W1", title="A paper")

    with pytest.raises(ValidationError):
        cite.record_id = "W2"


def test_citation_rejects_blank_identity():
    """A citation with no resolvable record id is useless to the output validator,
    so a blank id is refused (characterization: mirrors Provenance's min_length)."""
    with pytest.raises(ValidationError):
        Citation(source="OpenAlex", record_id="", title="A paper")


def test_proposal_carries_a_constraint_bridge():
    """A Proposal is one cited bridge from a fuzzy goal to a queryable spec field.
    A constraint-kind proposal embeds the actual Constraint it compiles to, plus
    the reasoning, its literature grounding, and a confidence in (0, 1]."""
    prop = ConstraintProposal(
        constraint=Constraint(property_name="band_gap", min=2.0, max=4.0),
        rationale="'wide-gap semiconductor' maps to roughly 2-4 eV for oxides",
        citations=(Citation(source="OpenAlex", record_id="W1", title="A paper"),),
        confidence=0.7,
    )

    assert prop.kind == "constraint"
    assert prop.constraint.property_name == "band_gap"
    assert prop.rationale.startswith("'wide-gap")
    assert prop.citations[0].record_id == "W1"
    assert prop.confidence == 0.7


def test_proposal_constraint_kind_requires_a_constraint_payload():
    """The kind is a promise about the payload: a constraint-kind proposal that
    carries no Constraint cannot compile to a spec field, so it is rejected at
    construction rather than failing silently later."""
    with pytest.raises(ValidationError):
        ConstraintProposal(
            rationale="missing its payload",
            confidence=0.5,
        )


def test_proposal_carries_a_ranking_target_bridge():
    """A ranking-target-kind proposal embeds the RankingTarget it compiles to —
    the same model the ranker already consumes — so an accepted proposal drops
    straight into the deterministic scoring stage."""
    prop = RankingProposal(
        ranking_target=RankingTarget(property_name="band_gap", direction="maximize", weight=0.6),
        rationale="the goal prefers a wider gap",
        confidence=0.8,
    )

    assert prop.kind == "ranking_target"
    assert prop.ranking_target.direction == "maximize"


def test_proposal_rejects_a_payload_foreign_to_its_kind():
    """The kind must be the single source of truth for the payload: a proposal
    that also carries a payload for a different kind is ambiguous, so it is
    rejected rather than letting the compiler guess which one to use."""
    target = RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)
    with pytest.raises(ValidationError):
        ConstraintProposal(
            constraint=Constraint(property_name="band_gap", min=2.0),
            ranking_target=target,
            rationale="carries two payloads",
            confidence=0.5,
        )


def test_proposal_carries_an_element_predicate_bridge():
    """An element_predicate-kind proposal embeds an ElementPredicate — the
    composition scoping the spec carries — alongside its grounding."""
    prop = ElementPredicateProposal(
        element_predicate=ElementPredicate(quantifier="none", members=frozenset({"Co"})),
        rationale="'cheap' rules out cobalt",
        confidence=0.6,
    )

    assert prop.kind == "element_predicate"
    assert prop.element_predicate.quantifier == "none"


def test_proposal_element_predicate_kind_requires_an_element_predicate_payload():
    """Same discriminator promise as the other kinds: an element_predicate proposal
    with no ElementPredicate is rejected at construction."""
    with pytest.raises(ValidationError):
        ElementPredicateProposal(rationale="empty", confidence=0.5)


def test_hypothesis_carries_its_proposals_and_mechanism():
    """A Hypothesis is the LLM's whole emission: the cited spec-bridge proposals
    (load-bearing — they compile to the spec) plus a mechanistic 'why' narrative."""
    hyp = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0, max=4.0),
                rationale="wide-gap semiconductor",
                confidence=0.7,
            ),
        ),
        mechanism="Wide-gap stable oxides tend to ...",
    )

    assert len(hyp.proposals) == 1
    assert hyp.proposals[0].kind == "constraint"
    assert hyp.mechanism.startswith("Wide-gap")


def test_hypothesis_rejects_empty_proposals():
    """A hypothesis that proposes nothing is meaningless and cannot compile to a
    spec, so an empty proposal tuple is refused (characterization: the model
    declares proposals min_length=1)."""
    with pytest.raises(ValidationError):
        Hypothesis(proposals=(), mechanism="proposed nothing")


def test_hypothesis_schema_requires_each_proposal_kind_to_carry_its_payload():
    """The kind->payload rule must live IN the JSON schema the LLM is handed (a
    discriminated union), not only in a validator the schema hides. Otherwise a
    structured-output model emits e.g. kind='ranking_target' with no
    ranking_target and the whole Hypothesis is rejected at parse time — exactly
    the failure observed against live Bedrock."""
    defs = Hypothesis.model_json_schema()["$defs"]

    assert "constraint" in defs["ConstraintProposal"]["required"]
    assert "boolean_constraint" in defs["BooleanConstraintProposal"]["required"]
    assert "ranking_target" in defs["RankingProposal"]["required"]
    assert "element_predicate" in defs["ElementPredicateProposal"]["required"]


def _constraint_proposal(name="band_gap", **bounds):
    return ConstraintProposal(
        constraint=Constraint(property_name=name, **bounds),
        rationale=f"bound on {name}",
        confidence=0.7,
    )


def test_compile_spec_builds_a_triagespec_with_the_constraint():
    """compile_spec is the seam from accepted proposals to the frozen TriageSpec
    the deterministic core consumes: a lone constraint proposal yields a spec
    carrying exactly that constraint."""
    spec = compile_spec((_constraint_proposal(min=1.0, max=4.0),))

    assert isinstance(spec, TriageSpec)
    assert len(spec.constraints) == 1
    assert spec.constraints[0].property_name == "band_gap"
    assert spec.constraints[0].min == 1.0


def _ranking_proposal(name, weight, direction="maximize"):
    return RankingProposal(
        ranking_target=RankingTarget(property_name=name, direction=direction, weight=weight),
        rationale=f"prefer {direction} {name}",
        confidence=0.7,
    )


def test_compile_spec_defaults_to_the_geometric_mean_ranker():
    """The agent ranks by the non-compensatory weighted geometric mean by default,
    so compile_spec produces a geometric_mean spec — which requires each ranking
    target to announce its desirability ramp bounds (here lower/target for a
    maximize ramp)."""
    bounded = RankingProposal(
        ranking_target=RankingTarget(
            property_name="band_gap", direction="maximize", weight=1.0, lower=1.0, target=3.0
        ),
        rationale="prefer wide gaps",
        confidence=0.7,
    )

    spec = compile_spec((_constraint_proposal(min=1.0), bounded))

    assert spec.ranking_method == "geometric_mean"


def test_compile_spec_normalizes_ranking_weights_to_sum_to_one():
    """The human gate accepts proposals independently, so surviving weights rarely
    sum to 1 — but TriageSpec requires it. compile_spec normalizes them, preserving
    ratios: proposed 0.6/0.2 (sum 0.8) compile to 0.75/0.25."""
    # Weight normalization is ranking-method-agnostic; use arithmetic_mean so the
    # bare targets (no ramp bounds) stay valid and the test stays focused on weights.
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            _ranking_proposal("band_gap", 0.6),
            _ranking_proposal("density", 0.2, direction="minimize"),
        ),
        ranking_method="arithmetic_mean",
    )

    weights = {t.property_name: t.weight for t in spec.ranking_targets}
    assert weights["band_gap"] == pytest.approx(0.75)
    assert weights["density"] == pytest.approx(0.25)


def test_compile_spec_makes_a_lone_ranking_weight_one():
    """A single accepted ranking target must carry the whole weight, whatever the
    LLM proposed (pin: the normalization formula already yields 1.0)."""
    spec = compile_spec(
        (_constraint_proposal(min=1.0), _ranking_proposal("band_gap", 0.4)),
        ranking_method="arithmetic_mean",
    )

    assert spec.ranking_targets[0].weight == pytest.approx(1.0)


def _element_predicate_proposal(quantifier, members):
    return ElementPredicateProposal(
        element_predicate=ElementPredicate(quantifier=quantifier, members=frozenset(members)),
        rationale=f"{quantifier} {members}",
        confidence=0.7,
    )


def test_compile_spec_maps_element_predicates_onto_the_spec():
    """element_predicate proposals scope composition. The unified predicate carries
    the 'any' quantifier (HAS ANY — at least one member present) that require/exclude
    could not express, and compile_spec lands every predicate on the spec verbatim,
    in proposal order."""
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            _element_predicate_proposal("any", {"Fe", "Zn", "Ti"}),
            _element_predicate_proposal("none", {"Pb"}),
        )
    )

    assert spec.element_predicates == (
        ElementPredicate(quantifier="any", members=frozenset({"Fe", "Zn", "Ti"})),
        ElementPredicate(quantifier="none", members=frozenset({"Pb"})),
    )


def test_compile_spec_maps_a_count_constraint_onto_the_spec():
    """A count_constraint proposal compiles to a bound on composition cardinality —
    'simple compositions' means few distinct elements. It is a typed field, not a
    numeric Constraint on a magic property name, so the spec keeps a robust
    cross-check against the element predicates."""
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            CountConstraintProposal(
                count_constraint=CountConstraint(max=3),
                rationale="simple compositions",
                confidence=0.7,
            ),
        )
    )

    assert spec.count == CountConstraint(max=3)


def test_compile_spec_propagates_duplicate_constraint_rejection():
    """compile_spec never silently merges: two constraint proposals on the same
    property is incoherent, and TriageSpec's own validator rejects it — the error
    propagates rather than being papered over."""
    with pytest.raises(ValidationError):
        compile_spec((_constraint_proposal(min=1.0), _constraint_proposal(max=4.0)))


def _boolean_proposal(name, required):
    return BooleanConstraintProposal(
        boolean_constraint=BooleanConstraint(property_name=name, required=required),
        rationale=f"{name} must be {required}",
        confidence=0.7,
    )


def test_compile_spec_maps_boolean_constraints_onto_the_spec():
    """A boolean_constraint proposal compiles to a hard yes/no filter the spec
    carries alongside its numeric constraints — the source-neutral way to express
    facts like is_stable that a min/max bound cannot. Like the numeric Constraint,
    the property name is unrestricted here: which booleans a source can answer is
    the adapter's vocabulary concern, not the spec's."""
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            _boolean_proposal("is_stable", True),
        )
    )

    assert spec.boolean_constraints == (
        BooleanConstraint(property_name="is_stable", required=True),
    )
