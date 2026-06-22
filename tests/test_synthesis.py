"""Tests for the synthesis artifact and its grounding check.

The LLM writes the prose, but every claim must cite a retrieved candidate — the
load-bearing "no invented facts" rule. These exercise the Synthesis model's
shape rules and ungrounded_record_ids, the grounding check the output validator
(step 8) and the synthesis retry loop both use.
"""

import pytest
from pydantic import ValidationError

from materials_triage.core.synthesis import (
    GroundedClaim,
    Synthesis,
    ungrounded_record_ids,
)


def test_synthesis_rejects_an_empty_summary():
    """A synthesis with no summary is not a usable narrative — reject it at the
    schema so the synthesis node retries rather than rendering blank prose."""
    with pytest.raises(ValidationError):
        Synthesis(summary="   ")


def test_grounded_claim_requires_text_and_a_record_id():
    """A claim must carry both its sentence and the record_id it is grounded in;
    a blank record_id would make the grounding check meaningless."""
    with pytest.raises(ValidationError):
        GroundedClaim(text="wide gap", record_id="")


def test_ungrounded_record_ids_flags_claims_citing_unretrieved_materials():
    """The grounding check returns exactly the cited record_ids that are NOT in
    the retrieved set (de-duplicated, input order) — these are the fabrications
    the validator rejects. Claims citing retrieved materials are grounded."""
    synthesis = Synthesis(
        summary="Two candidates stand out.",
        claims=(
            GroundedClaim(text="real", record_id="mp-real"),
            GroundedClaim(text="made up", record_id="mp-fake"),
            GroundedClaim(text="made up again", record_id="mp-fake"),
        ),
    )

    assert ungrounded_record_ids(synthesis, {"mp-real", "mp-other"}) == ("mp-fake",)


def test_ungrounded_record_ids_empty_when_fully_grounded():
    """A narrative citing only retrieved materials is fully grounded — no flags."""
    synthesis = Synthesis(
        summary="Grounded.",
        claims=(GroundedClaim(text="real", record_id="mp-real"),),
    )

    assert ungrounded_record_ids(synthesis, {"mp-real"}) == ()
