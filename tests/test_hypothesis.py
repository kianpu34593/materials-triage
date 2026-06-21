"""Tests for the hypothesis-layer data models in materials_triage.core.hypothesis.

These models carry the LLM's proposed bridges from a fuzzy goal to a queryable
spec, each grounded in literature. They are validated structured output: the LLM
is conformed to them, and anything malformed is rejected before it reaches the
deterministic core.
"""

import pytest
from pydantic import ValidationError

from materials_triage.core.hypothesis import Citation


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
