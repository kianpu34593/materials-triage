"""Tests for the plain-text renderers in materials_triage.render.

Both views are pure functions over one TriageRun (the audit export): render_pi is
the concise PI summary, render_audit is the full technical trace. Plain text in
v1 (Rich is v2), so the tests assert on substrings of the returned string.
"""

import pytest

from materials_triage.core.hypothesis import ConstraintProposal, Hypothesis
from materials_triage.core.run_trace import Step, TriageRun
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    ExcludedCandidate,
    PropertyValue,
    Provenance,
    ScoredCandidate,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.render import render_audit, render_pi, render_run


def _candidate(identifier: str, formula: str, band_gap: float) -> Candidate:
    prov = Provenance(source="Materials Project", record_id=identifier, method="computational")
    return Candidate(
        identifier=identifier,
        formula=formula,
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=prov)},
    )


def test_render_pi_leads_with_the_summary_and_lists_the_ranked_shortlist():
    """The PI view leads with the synthesis summary and presents the ranked shortlist
    (formula + id + score), best-first — the at-a-glance answer a PI reads first."""
    run = TriageRun(
        run_id="run-1",
        goal="wide-gap oxide for photocatalysis",
        result=TriageResult(
            ranked=(
                ScoredCandidate(candidate=_candidate("mp-1", "ZnO", 3.3), score=0.91),
                ScoredCandidate(candidate=_candidate("mp-2", "TiO2", 3.0), score=0.74),
            )
        ),
        synthesis=Synthesis(
            summary="ZnO leads, with TiO2 a close runner-up.",
            claims=(GroundedClaim(text="ZnO has a ~3.3 eV gap.", record_id="mp-1"),),
        ),
    )

    out = render_pi(run)

    assert "wide-gap oxide for photocatalysis" in out  # the goal
    assert "ZnO leads, with TiO2 a close runner-up." in out  # the summary leads
    assert "ZnO" in out and "mp-1" in out and "0.91" in out  # top candidate
    assert "TiO2" in out and "mp-2" in out  # runner-up
    # The summary appears before the shortlist (leads the view).
    assert out.index("ZnO leads") < out.index("mp-1")


def test_render_pi_surfaces_caveats():
    """A run-level caveat (a hard predicate the source could not enforce, or a capped
    result set) is a first-class honesty signal — the PI view must show it, not bury it."""
    run = TriageRun(
        run_id="run-2",
        goal="nontoxic wide-gap oxide",
        result=TriageResult(
            ranked=(ScoredCandidate(candidate=_candidate("mp-1", "ZnO", 3.3), score=0.9),)
        ),
        caveats=("constraint on 'toxicity' was not applied: Materials Project provides no data",),
    )

    out = render_pi(run)

    assert "toxicity" in out
    assert "caveat" in out.lower()  # labeled as a caveat, not hidden among results


def test_render_pi_states_plainly_when_no_candidates_matched():
    """An empty shortlist must read as an explicit 'nothing matched', never a blank
    section a PI could mistake for an unfinished run."""
    run = TriageRun(run_id="run-3", goal="impossible spec", result=TriageResult())

    out = render_pi(run)

    assert "no candidates" in out.lower()


def test_render_audit_shows_the_full_trace_with_reasons_and_citations():
    """The audit view exposes what the PI view summarizes away: the run id, the spec,
    the hypothesis mechanism, the ranked AND excluded candidates (each drop with its
    structured reason), the cited synthesis claims, and the per-step execution trace."""
    run = TriageRun(
        run_id="run-audit",
        goal="wide-gap oxide",
        spec=TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),)),
        hypothesis=Hypothesis(
            proposals=(
                ConstraintProposal(
                    constraint=Constraint(property_name="band_gap", min=2.0),
                    rationale="wide gap",
                    confidence=0.8,
                ),
            ),
            mechanism="wide gaps lower leakage",
        ),
        result=TriageResult(
            ranked=(ScoredCandidate(candidate=_candidate("mp-1", "ZnO", 3.3), score=0.9),),
            excluded=(
                ExcludedCandidate(
                    candidate=_candidate("mp-9", "PbO", 1.2),
                    property_name="band_gap",
                    reason="below_min",
                    value=1.2,
                    bound=2.0,
                ),
            ),
        ),
        synthesis=Synthesis(
            summary="ZnO leads.",
            claims=(GroundedClaim(text="ZnO has a ~3.3 eV gap.", record_id="mp-1"),),
        ),
        steps=(
            Step(name="retrieve", writes={"candidates": ("...",)}),
            Step(name="rank", writes={"result": "..."}),
        ),
    )

    out = render_audit(run)

    assert "run-audit" in out  # run id for traceability
    assert "wide gaps lower leakage" in out  # hypothesis mechanism
    assert "mp-1" in out and "ZnO" in out  # ranked survivor
    # The excluded candidate appears WITH its structured reason and the bound it broke.
    assert "PbO" in out and "below_min" in out
    assert "1.2" in out and "2.0" in out
    # Synthesis claim with its citation.
    assert "ZnO has a ~3.3 eV gap." in out and "mp-1" in out
    # Per-step execution trace.
    assert "retrieve" in out and "rank" in out


def test_render_run_dispatches_on_view():
    """The CLI calls one entry point with a view name; it dispatches to the matching
    renderer and rejects an unknown view rather than silently picking one."""
    run = TriageRun(
        run_id="run-d",
        goal="g",
        result=TriageResult(
            ranked=(ScoredCandidate(candidate=_candidate("mp-1", "ZnO", 3.3), score=0.9),)
        ),
    )

    assert render_run(run, view="pi") == render_pi(run)
    assert render_run(run, view="audit") == render_audit(run)
    with pytest.raises(ValueError):
        render_run(run, view="bogus")
