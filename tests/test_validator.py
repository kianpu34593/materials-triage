"""Tests for the output validator (workflow step 8).

The validator is the final grounding gate: every presented candidate and every
narrative citation must resolve to a material deterministic retrieval returned,
or the run is refused rather than render fabricated output.
"""

import pytest

from materials_triage.agent.validator import UngroundedOutputError, validate_output
from materials_triage.core.schema import (
    Candidate,
    PropertyValue,
    Provenance,
    ScoredCandidate,
    TriageResult,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis


def _candidate(identifier):
    prov = Provenance(source="Materials Project", record_id=identifier)
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=3.3, unit="eV", provenance=prov)},
    )


def test_validate_output_passes_a_fully_grounded_result_and_narrative():
    """A result whose candidates were all retrieved and a narrative citing only
    those candidates passes — validate_output returns None, the run may render."""
    cand = _candidate("mp-1")
    result = TriageResult(ranked=(ScoredCandidate(candidate=cand, score=0.9),))
    synthesis = Synthesis(
        summary="mp-1 leads.", claims=(GroundedClaim(text="wide gap", record_id="mp-1"),)
    )

    assert validate_output(result, synthesis, {"mp-1"}) is None


def test_validate_output_rejects_a_narrative_citing_an_unretrieved_material():
    """A claim citing a material retrieval never returned is a fabrication — the
    validator refuses it rather than letting it reach the scientist."""
    cand = _candidate("mp-1")
    result = TriageResult(ranked=(ScoredCandidate(candidate=cand, score=0.9),))
    synthesis = Synthesis(
        summary="hallucinated", claims=(GroundedClaim(text="fake", record_id="mp-ghost"),)
    )

    with pytest.raises(UngroundedOutputError, match="mp-ghost"):
        validate_output(result, synthesis, {"mp-1"})


def test_validate_output_rejects_a_presented_candidate_not_in_retrieval():
    """Even with no narrative, a ranked candidate whose id is not in retrieved
    provenance is ungrounded and refused."""
    result = TriageResult(ranked=(ScoredCandidate(candidate=_candidate("mp-rogue"), score=0.5),))

    with pytest.raises(UngroundedOutputError, match="mp-rogue"):
        validate_output(result, None, {"mp-1"})
