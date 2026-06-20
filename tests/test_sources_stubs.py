"""Tests for the deferred-source stub adapters in materials_triage.sources.stubs."""

import pytest

from materials_triage.core.schema import Constraint, TriageSpec
from materials_triage.sources.base import SourceAdapter
from materials_triage.sources.stubs import (
    AflowAdapter,
    IcsdAdapter,
    OqmdAdapter,
    PubChemAdapter,
)


@pytest.mark.parametrize("adapter_cls", [OqmdAdapter, AflowAdapter, PubChemAdapter, IcsdAdapter])
def test_stub_adapter_instantiates_but_refuses_retrieval(adapter_cls):
    """A stub is a real SourceAdapter that constructs (proving the interface is
    pluggable) yet raises NotImplementedError on retrieve — it never pretends to
    have data the v1 slice does not source."""
    assert issubclass(adapter_cls, SourceAdapter)
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))
    with pytest.raises(NotImplementedError):
        adapter_cls().retrieve(spec)
