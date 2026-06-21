"""Tests for the hypothesis-layer data models in materials_triage.core.hypothesis.

These models carry the LLM's proposed bridges from a fuzzy goal to a queryable
spec, each grounded in literature. They are validated structured output: the LLM
is conformed to them, and anything malformed is rejected before it reaches the
deterministic core.
"""

import pytest
from pydantic import ValidationError

from materials_triage.core.hypothesis import Citation, ElementRule, Hypothesis, Proposal
from materials_triage.core.schema import Constraint, RankingTarget


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
    prop = Proposal(
        kind="constraint",
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
        Proposal(
            kind="constraint",
            constraint=None,
            rationale="missing its payload",
            confidence=0.5,
        )


def test_proposal_carries_a_ranking_target_bridge():
    """A ranking-target-kind proposal embeds the RankingTarget it compiles to —
    the same model the ranker already consumes — so an accepted proposal drops
    straight into the deterministic scoring stage."""
    prop = Proposal(
        kind="ranking_target",
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
        Proposal(
            kind="constraint",
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
    prop = Proposal(
        kind="element_rule",
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
        Proposal(kind="element_rule", element_rule=None, rationale="empty", confidence=0.5)


def test_hypothesis_carries_its_proposals_and_mechanism():
    """A Hypothesis is the LLM's whole emission: the cited spec-bridge proposals
    (load-bearing — they compile to the spec) plus a mechanistic 'why' narrative."""
    hyp = Hypothesis(
        proposals=(
            Proposal(
                kind="constraint",
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
