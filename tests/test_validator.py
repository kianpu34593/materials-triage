"""Tests for the output validator in materials_triage.agent.validator (step 8).

The validator is the final grounding gate before render: every presented
candidate and every narrative citation must resolve to a candidate deterministic
retrieval actually returned, or the validator refuses rather than render a
fabricated artifact.
"""

import pytest

from materials_triage.agent.validator import UngroundedOutputError, validate_output
from materials_triage.core.schema import (
    Candidate,
    ExcludedCandidate,
    PropertyValue,
    Provenance,
    ScoredCandidate,
    TriageResult,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis


def _candidate(identifier: str) -> Candidate:
    prov = Provenance(source="Materials Project", record_id=identifier, method="computational")
    return Candidate(
        identifier=identifier,
        formula="TiO2",
        properties={"band_gap": PropertyValue(value=2.0, unit="eV", provenance=prov)},
    )


def test_validate_output_passes_a_fully_grounded_output():
    """Every presented candidate and every citation resolves to a retrieved id, so
    the validator returns cleanly (None)."""
    result = TriageResult(
        ranked=(ScoredCandidate(candidate=_candidate("mp-1"), score=1.0),),
        excluded=(
            ExcludedCandidate(
                candidate=_candidate("mp-2"),
                property_name="band_gap",
                reason="below_min",
                value=1.0,
                bound=2.0,
            ),
        ),
    )
    synthesis = Synthesis(
        summary="mp-1 leads.",
        claims=(GroundedClaim(text="mp-1 has the widest gap.", record_id="mp-1"),),
    )

    assert validate_output(result, synthesis, {"mp-1", "mp-2"}) is None


def test_ungrounded_output_error_is_a_runtime_error():
    """The refusal is an exception type callers can catch distinctly."""
    assert issubclass(UngroundedOutputError, RuntimeError)


def test_validate_output_rejects_a_presented_candidate_not_in_provenance():
    """A ranked (or excluded) candidate whose id retrieval never returned is a
    fabricated material — the validator refuses, naming the offending id."""
    result = TriageResult(
        ranked=(ScoredCandidate(candidate=_candidate("mp-ghost"), score=1.0),),
    )

    with pytest.raises(UngroundedOutputError, match="mp-ghost"):
        validate_output(result, None, {"mp-1"})


def test_validate_output_rejects_a_narrative_citation_not_in_provenance():
    """The candidates are all grounded, but the narrative cites a material retrieval
    never returned — a fabricated citation the validator refuses, naming the id."""
    result = TriageResult(ranked=(ScoredCandidate(candidate=_candidate("mp-1"), score=1.0),))
    synthesis = Synthesis(
        summary="mp-1 leads.",
        claims=(GroundedClaim(text="mp-phantom is comparable.", record_id="mp-phantom"),),
    )

    with pytest.raises(UngroundedOutputError, match="mp-phantom"):
        validate_output(result, synthesis, {"mp-1"})
