"""Tests for the core data models in materials_triage.core.schema."""

import pytest
from pydantic import ValidationError

from materials_triage.core.schema import Provenance


def test_provenance_carries_its_source():
    """A Provenance records where a scientific value came from."""
    prov = Provenance(source="Materials Project")

    assert prov.source == "Materials Project"


def test_provenance_rejects_blank_source():
    """A receipt with no issuer is meaningless, so a blank source is refused."""
    with pytest.raises(ValidationError):
        Provenance(source="")


def test_provenance_is_immutable():
    """Once a value is tagged with its origin, that tag cannot be changed."""
    prov = Provenance(source="Materials Project")

    with pytest.raises(ValidationError):
        prov.source = "OQMD"
