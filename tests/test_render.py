"""Tests for the two output renderers (workflow step 9).

render_pi is the concise scientist-facing view; render_audit is the full
replayable trace. Both are pure string builders, so these assert the load-bearing
content appears (narrative, ranked ids+scores, exclusion reasons, step trace).
"""

from materials_triage.core.run_trace import Step, TriageRun
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
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.render import render_audit, render_pi


def _candidate(identifier, band_gap):
    prov = Provenance(source="Materials Project", record_id=identifier)
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=prov)},
    )


def _result():
    keep = _candidate("mp-keep", 4.0)
    drop = _candidate("mp-drop", 1.0)
    return TriageResult(
        ranked=(ScoredCandidate(candidate=keep, score=0.91),),
        excluded=(
            ExcludedCandidate(
                candidate=drop, property_name="band_gap", reason="below_min", value=1.0, bound=2.0
            ),
        ),
    )


def test_render_pi_leads_with_the_narrative_then_the_ranked_shortlist():
    """The PI view shows the grounded summary, the ranked candidate with its score
    and properties, and notes the excluded count — the at-a-glance answer."""
    synthesis = Synthesis(
        summary="mp-keep is the standout wide-gap oxide.",
        claims=(GroundedClaim(text="4.0 eV gap", record_id="mp-keep"),),
    )

    text = render_pi(_result(), synthesis)

    assert "mp-keep is the standout wide-gap oxide." in text
    assert "mp-keep" in text and "0.910" in text
    assert "band_gap=4.0 eV" in text
    assert "1 candidate(s) excluded" in text


def test_render_pi_handles_an_empty_shortlist_without_a_narrative():
    """With nothing surviving and no synthesis, the PI view says so plainly rather
    than rendering a blank or erroring."""
    text = render_pi(TriageResult(ranked=(), excluded=()))

    assert "No candidates survived the hard filters." in text


def test_render_audit_shows_spec_step_trace_exclusions_and_citations():
    """The audit view is the replayable record: the spec, the per-step trace, the
    ranked shortlist, every exclusion with its reason, and the cited narrative."""
    run = TriageRun(
        run_id="run-1",
        goal="wide-gap oxide",
        spec=TriageSpec(
            constraints=(Constraint(property_name="band_gap", min=2.0),),
            ranking_targets=(
                RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
            ),
        ),
        result=_result(),
        synthesis=Synthesis(
            summary="mp-keep leads.",
            claims=(GroundedClaim(text="widest gap", record_id="mp-keep"),),
        ),
        steps=(Step(name="retrieve", writes={"candidates": ()}), Step(name="render", writes={})),
    )

    text = render_audit(run)

    assert "run-1" in text and "wide-gap oxide" in text
    assert "constraint  band_gap: min=2.0" in text
    assert "retrieve: candidates" in text
    assert "render: (pass-through)" in text
    assert "mp-keep" in text and "0.910" in text
    assert "mp-drop" in text and "below_min" in text and "bound=2.0" in text
    assert "[mp-keep] widest gap" in text
