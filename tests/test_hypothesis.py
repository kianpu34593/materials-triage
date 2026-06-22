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
    ElementRule,
    ElementRuleProposal,
    Hypothesis,
    RankingProposal,
    compile_spec,
)
from materials_triage.core.schema import (
    BooleanConstraint,
    Constraint,
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


def test_element_rule_carries_its_mode_and_elements():
    """An ElementRule is the element_rule payload: one scoping decision — require
    or exclude a set of element symbols — that compiles to a TriageSpec element
    set. One mode per rule, so each is independently cited and gated."""
    rule = ElementRule(mode="require", elements=frozenset({"Zn", "O"}))

    assert rule.mode == "require"
    assert rule.elements == frozenset({"Zn", "O"})


def test_element_rule_rejects_non_element_symbols():
    """The LLM can hallucinate a symbol; an ElementRule validates against the
    canonical element set at construction so a bogus 'Xx' never reaches the spec."""
    with pytest.raises(ValidationError):
        ElementRule(mode="require", elements=frozenset({"Zn", "Xx"}))


def test_proposal_carries_an_element_rule_bridge():
    """An element_rule-kind proposal embeds an ElementRule — the composition
    scoping the spec will push to retrieval — alongside its grounding."""
    prop = ElementRuleProposal(
        element_rule=ElementRule(mode="exclude", elements=frozenset({"Co"})),
        rationale="'cheap' rules out cobalt",
        confidence=0.6,
    )

    assert prop.kind == "element_rule"
    assert prop.element_rule.mode == "exclude"


def test_proposal_element_rule_kind_requires_an_element_rule_payload():
    """Same discriminator promise as the other kinds: an element_rule proposal
    with no ElementRule is rejected at construction."""
    with pytest.raises(ValidationError):
        ElementRuleProposal(rationale="empty", confidence=0.5)


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
    assert "ranking_target" in defs["RankingProposal"]["required"]
    assert "element_rule" in defs["ElementRuleProposal"]["required"]


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


def test_compile_spec_normalizes_ranking_weights_to_sum_to_one():
    """The human gate accepts proposals independently, so surviving weights rarely
    sum to 1 — but TriageSpec requires it. compile_spec normalizes them, preserving
    ratios: proposed 0.6/0.2 (sum 0.8) compile to 0.75/0.25."""
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            _ranking_proposal("band_gap", 0.6),
            _ranking_proposal("density", 0.2, direction="minimize"),
        )
    )

    weights = {t.property_name: t.weight for t in spec.ranking_targets}
    assert weights["band_gap"] == pytest.approx(0.75)
    assert weights["density"] == pytest.approx(0.25)


def test_compile_spec_makes_a_lone_ranking_weight_one():
    """A single accepted ranking target must carry the whole weight, whatever the
    LLM proposed (pin: the normalization formula already yields 1.0)."""
    spec = compile_spec((_constraint_proposal(min=1.0), _ranking_proposal("band_gap", 0.4)))

    assert spec.ranking_targets[0].weight == pytest.approx(1.0)


def _element_proposal(mode, elements):
    return ElementRuleProposal(
        element_rule=ElementRule(mode=mode, elements=frozenset(elements)),
        rationale=f"{mode} {elements}",
        confidence=0.7,
    )


def test_compile_spec_maps_element_rules_to_required_and_excluded():
    """element_rule proposals scope composition: require-mode rules union into
    required_elements, exclude-mode into excluded_elements, so retrieval and the
    hard filter see exactly the requested scoping."""
    spec = compile_spec(
        (
            _constraint_proposal(min=1.0),
            _element_proposal("require", {"Zn", "O"}),
            _element_proposal("exclude", {"Pb"}),
        )
    )

    assert spec.required_elements == frozenset({"Zn", "O"})
    assert spec.excluded_elements == frozenset({"Pb"})


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
