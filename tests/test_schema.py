"""Tests for the core data models in materials_triage.core.schema."""

from materials_triage.core.schema import Provenance


def test_provenance_carries_its_source():
    """A Provenance records where a scientific value came from."""
    prov = Provenance(source="Materials Project")

    assert prov.source == "Materials Project"
