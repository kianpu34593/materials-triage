"""Tests for the SourceAdapter interface in materials_triage.sources.base."""

import pytest

from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RetrievalResult,
    TriageSpec,
)
from materials_triage.core.scoring import apply_hard_filters
from materials_triage.sources.base import SourceAdapter


def test_source_adapter_cannot_be_instantiated_without_retrieve():
    """SourceAdapter is abstract: a subclass that does not implement retrieve
    cannot be instantiated, so every real source is forced to provide one."""

    class IncompleteAdapter(SourceAdapter):
        pass

    with pytest.raises(TypeError):
        IncompleteAdapter()


def test_concrete_adapter_output_flows_through_hard_filters():
    """A concrete adapter implementing retrieve produces a RetrievalResult whose
    candidates drop straight into apply_hard_filters — pinning the contract against
    the real downstream stage."""
    candidate = Candidate(
        identifier="mp-x",
        formula="TiO2",
        properties={
            "band_gap": PropertyValue(
                value=2.0,
                unit="eV",
                provenance=Provenance(
                    source="Materials Project", record_id="mp-x", method="computational"
                ),
            )
        },
    )

    class FixedAdapter(SourceAdapter):
        def retrieve(self, spec: TriageSpec) -> RetrievalResult:
            return RetrievalResult(candidates=(candidate,))

    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))
    result = FixedAdapter().retrieve(spec)
    survivors, excluded = apply_hard_filters(list(result.candidates), spec.constraints)

    assert survivors == [candidate]
    assert excluded == []
