"""Tests for the synthesis artifact in materials_triage.core.synthesis.

These pin the *shape* the LLM fills (a grounded, cited narrative) and the
grounding helper the output validator (#20) and the synthesis retry loop use.
"""

import pytest

from materials_triage.core.synthesis import (
    GroundedClaim,
    Synthesis,
    ungrounded_record_ids,
)


def test_grounded_claim_binds_text_to_a_record_id():
    """A claim is one sentence of narrative bound to the candidate it cites."""
    claim = GroundedClaim(text="TiO2 has a wide band gap.", record_id="mp-aaaaadyf")
    assert claim.text == "TiO2 has a wide band gap."
    assert claim.record_id == "mp-aaaaadyf"


def test_grounded_claim_rejects_blank_text_or_record_id():
    """An empty claim or a citation with no record id is not grounded — reject it
    at construction so an ungrounded artifact can't form."""
    with pytest.raises(ValueError):
        GroundedClaim(text="   ", record_id="mp-1")
    with pytest.raises(ValueError):
        GroundedClaim(text="real claim", record_id="")


def test_synthesis_carries_a_summary_and_its_cited_claims():
    """The whole LLM emission: a PI-facing summary plus the per-candidate cited
    mechanistic claims behind the shortlist."""
    synthesis = Synthesis(
        summary="Three wide-gap oxides lead the shortlist.",
        claims=(GroundedClaim(text="TiO2 leads on band gap.", record_id="mp-1"),),
    )
    assert synthesis.summary == "Three wide-gap oxides lead the shortlist."
    assert synthesis.claims[0].record_id == "mp-1"


def test_synthesis_defaults_to_no_claims_but_requires_a_summary():
    """A summary is the at-a-glance prose the PI view leads with, so it must be
    present; claims may be empty (a shortlist with no narrative yet)."""
    assert Synthesis(summary="A terse verdict.").claims == ()
    with pytest.raises(ValueError):
        Synthesis(summary="   ")


def test_ungrounded_record_ids_is_empty_when_every_claim_resolves():
    """A fully grounded narrative cites only materials retrieval returned."""
    synthesis = Synthesis(
        summary="ok",
        claims=(
            GroundedClaim(text="a", record_id="mp-1"),
            GroundedClaim(text="b", record_id="mp-2"),
        ),
    )
    assert ungrounded_record_ids(synthesis, {"mp-1", "mp-2", "mp-3"}) == ()


def test_ungrounded_record_ids_reports_misses_in_order_without_duplicates():
    """The misses feed the synthesis retry / validator, so they are reported in
    first-seen order and de-duplicated (a record cited twice is one problem)."""
    synthesis = Synthesis(
        summary="ok",
        claims=(
            GroundedClaim(text="a", record_id="mp-ghost"),
            GroundedClaim(text="b", record_id="mp-1"),
            GroundedClaim(text="c", record_id="mp-ghost"),
            GroundedClaim(text="d", record_id="mp-other"),
        ),
    )
    assert ungrounded_record_ids(synthesis, {"mp-1"}) == ("mp-ghost", "mp-other")
