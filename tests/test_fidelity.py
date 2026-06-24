"""Tests for the spec-fidelity gate in materials_triage.core.fidelity.

``reconcile_spec`` runs AFTER ``compile_spec``: it detects the hard facets a goal
states plainly ("oxide" -> require O, "non-toxic" -> exclude toxic elements,
"simple composition" -> cap the element count) and seeds any the compiled spec
dropped, so fidelity to the request is a code guarantee, not an LLM hope. Each
decision is recorded as a FacetFinding for the audit trace and the human gate.
Pure domain logic — these tests call it directly with constructed specs.
"""

import pytest

from materials_triage.core.fidelity import reconcile_spec
from materials_triage.core.schema import (
    Constraint,
    CountConstraint,
    ElementPredicate,
    TriageSpec,
)


def _required_elements(spec: TriageSpec) -> set[str]:
    """Every element an 'all' predicate forces to be present (the require set)."""
    return {e for p in spec.element_predicates if p.quantifier == "all" for e in p.members}


def test_oxide_goal_seeds_a_require_oxygen_predicate_when_the_spec_dropped_it():
    """The LLM commonly drops "oxide" from the spec. The fidelity gate detects the
    anion family and seeds an 'all' predicate requiring O, recording it as seeded."""
    # The LLM kept the band-gap filter but dropped the "oxide" composition rule.
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    reconciled, findings = reconcile_spec("wide-gap oxide for photocatalysis", spec)

    assert "O" in _required_elements(reconciled)
    assert any(f.action == "seeded" and f.facet == "oxide" for f in findings)


def _excluded_elements(spec: TriageSpec) -> set[str]:
    """Every element a 'none' predicate forbids (the exclude set)."""
    return {e for p in spec.element_predicates if p.quantifier == "none" for e in p.members}


def test_nontoxic_goal_seeds_an_exclusion_of_the_toxic_set_with_a_caveat():
    """ "non-toxic" seeds a 'none' predicate excluding the committed toxic element set,
    and records a caveat that the denylist is element-level only (oxidation-state /
    leachability toxicity isn't resolvable from public DFT data)."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    reconciled, findings = reconcile_spec("a non-toxic semiconductor", spec)

    excluded = _excluded_elements(reconciled)
    assert {"Pb", "Hg", "Cd", "As"} <= excluded  # the heavy-metal core of the set
    toxic_finding = next(f for f in findings if f.facet == "non-toxic")
    assert toxic_finding.action == "seeded"
    assert toxic_finding.caveat  # the oxidation-state caveat is recorded


def test_simple_composition_goal_seeds_a_count_cap():
    """A "binary"/"simple composition" cue caps the distinct-element count, seeding a
    CountConstraint the LLM omitted."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    reconciled, findings = reconcile_spec("a simple binary semiconductor", spec)

    assert reconciled.count is not None
    assert reconciled.count.max == 2  # "binary" -> at most 2 distinct elements
    assert any(f.facet == "simple composition" and f.action == "seeded" for f in findings)


def test_already_satisfied_facet_is_recorded_and_not_re_seeded():
    """When the compiled spec already requires the anion, the gate records
    'already_satisfied' and does not add a duplicate predicate."""
    spec = TriageSpec(
        element_predicates=(ElementPredicate(quantifier="all", members=frozenset({"O"})),)
    )

    reconciled, findings = reconcile_spec("wide-gap oxide", spec)

    assert reconciled.element_predicates == spec.element_predicates  # unchanged
    assert any(f.facet == "oxide" and f.action == "already_satisfied" for f in findings)
    assert not any(f.action == "seeded" for f in findings)


def test_explicitly_required_toxic_element_is_not_also_excluded():
    """A "non-toxic arsenide" both requires As (anion family) and asks to exclude
    toxic elements (which includes As). The explicit request wins: As is required and
    is NOT in the exclusion, so the seeded spec is coherent and constructs cleanly."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))

    reconciled, _ = reconcile_spec("a non-toxic arsenide", spec)

    assert "As" in _required_elements(reconciled)
    assert "As" not in _excluded_elements(reconciled)
    # The result already constructed without raising TriageSpec's contradiction check.


def test_seeding_a_count_cap_preserves_an_existing_count_min():
    """When the LLM proposed a hard 'at least N elements' min, seeding the simple-
    composition max must MERGE into the existing constraint, not replace it — the min
    survives so the hard requirement is not silently dropped."""
    spec = TriageSpec(count=CountConstraint(min=2))

    reconciled, findings = reconcile_spec("a simple ternary semiconductor", spec)

    assert reconciled.count is not None
    assert reconciled.count.min == 2  # the proposed 'at least 2' survives
    assert reconciled.count.max == 3  # "ternary" -> at most 3 distinct elements
    assert any(f.facet == "simple composition" and f.action == "seeded" for f in findings)


def test_simplicity_cap_skipped_when_existing_min_exceeds_target():
    """A self-contradictory goal — 'binary' (target 2) plus an explicit 'at least 3
    elements' min — must NOT crash building CountConstraint(min=3, max=2). The explicit
    min wins: the cap is skipped and a contradiction caveat is recorded for the user."""
    spec = TriageSpec(count=CountConstraint(min=3))

    reconciled, findings = reconcile_spec("a binary material with at least 3 elements", spec)

    assert reconciled.count is not None
    assert reconciled.count.min == 3  # the explicit minimum is preserved
    assert reconciled.count.max is None  # the contradictory cap is NOT seeded
    skipped = [f for f in findings if f.facet == "simple composition"]
    assert skipped and skipped[0].action == "skipped"
    assert skipped[0].caveat  # a non-empty contradiction caveat flows to the user


def test_seeded_spec_that_violates_coherence_rules_raises():
    """A seeded toxic 'none' whose members cover an existing 'any' predicate makes that
    'any' unsatisfiable. The seeder re-validates through TriageSpec, so this incoherent
    spec raises rather than being built silently."""
    spec = TriageSpec(
        element_predicates=(ElementPredicate(quantifier="any", members=frozenset({"Pb", "Hg"})),)
    )

    with pytest.raises(ValueError):
        reconcile_spec("a non-toxic semiconductor", spec)


# --- reconcile_energetics: deterministic energetics domain rules ---


def test_reconcile_energetics_drops_formation_energy_ranking_target():
    """formation_energy_per_atom is not comparable across chemistries, so a ranking
    target on it is dropped and the remaining weights renormalize to sum to 1."""
    from materials_triage.core.fidelity import reconcile_energetics
    from materials_triage.core.schema import RankingTarget

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(
                property_name="band_gap", direction="maximize", weight=0.5, lower=1.0, target=3.0
            ),
            RankingTarget(
                property_name="formation_energy_per_atom",
                direction="minimize",
                weight=0.5,
                target=-2.0,
                upper=0.0,
            ),
        ),
        ranking_method="geometric_mean",
    )

    new_spec, findings = reconcile_energetics(spec)

    names = {t.property_name for t in new_spec.ranking_targets}
    assert names == {"band_gap"}  # formation energy dropped
    assert new_spec.ranking_targets[0].weight == 1.0  # renormalized
    assert any("formation_energy_per_atom" in f.caveat for f in findings)


def test_reconcile_energetics_drops_hull_when_is_stable_required():
    """is_stable=True makes an energy_above_hull constraint redundant -> dropped."""
    from materials_triage.core.fidelity import reconcile_energetics
    from materials_triage.core.schema import BooleanConstraint

    spec = TriageSpec(
        constraints=(
            Constraint(property_name="band_gap", min=2.0),
            Constraint(property_name="energy_above_hull", max=0.05),
        ),
        boolean_constraints=(BooleanConstraint(property_name="is_stable", required=True),),
    )

    new_spec, findings = reconcile_energetics(spec)

    names = {c.property_name for c in new_spec.constraints}
    assert names == {"band_gap"}  # hull constraint dropped
    assert any("redundant with the required is_stable" in f.caveat for f in findings)


def test_reconcile_energetics_keeps_hull_when_is_stable_not_required():
    """Without is_stable=True, an energy_above_hull constraint is a legitimate
    metastability filter and is left untouched."""
    from materials_triage.core.fidelity import reconcile_energetics

    spec = TriageSpec(
        constraints=(Constraint(property_name="energy_above_hull", max=0.05),),
    )

    new_spec, findings = reconcile_energetics(spec)

    assert {c.property_name for c in new_spec.constraints} == {"energy_above_hull"}
    assert findings == []


def test_reconcile_energetics_drops_formation_energy_constraint():
    """A hard constraint on formation_energy_per_atom is dropped too (cross-system
    filtering by it is equally unsound)."""
    from materials_triage.core.fidelity import reconcile_energetics

    spec = TriageSpec(
        constraints=(
            Constraint(property_name="band_gap", min=2.0),
            Constraint(property_name="formation_energy_per_atom", max=-1.0),
        ),
    )

    new_spec, _ = reconcile_energetics(spec)

    assert {c.property_name for c in new_spec.constraints} == {"band_gap"}


def test_reconcile_energetics_guards_against_emptying_the_ranking_set():
    """If the only ranking target is the energetics one, it is KEPT (a skip) rather
    than dropped, so the run still has something to rank by."""
    from materials_triage.core.fidelity import reconcile_energetics
    from materials_triage.core.schema import RankingTarget

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(
                property_name="formation_energy_per_atom",
                direction="minimize",
                weight=1.0,
                target=-2.0,
                upper=0.0,
            ),
        ),
        ranking_method="geometric_mean",
    )

    new_spec, findings = reconcile_energetics(spec)

    assert {t.property_name for t in new_spec.ranking_targets} == {"formation_energy_per_atom"}
    assert any(f.action == "skipped" for f in findings)
