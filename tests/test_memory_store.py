"""Tests for lab memory in materials_triage.memory.store.

Lab memory is the orchestrator's CROSS-run persistence bucket (a LangGraph
BaseStore, distinct from the per-run checkpointer): finalized specs are saved so
a later, separate run can recall one as a seed. v1 has no mandatory profile —
the spec is LLM-built and remembered here.
"""

from langgraph.store.memory import InMemoryStore

from materials_triage.core.schema import Constraint, RankingTarget, TriageSpec
from materials_triage.memory.store import LabMemory


def _spec():
    return TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )


def test_lab_memory_recalls_a_saved_spec_across_separate_runs():
    """The point of lab memory: a spec saved in one run is recallable in a later,
    separate run. Two LabMemory instances sharing the same backing store stand in
    for two runs; the recalled spec equals the saved one (round-tripped through
    the store's JSON, not a binary serde)."""
    backing = InMemoryStore()
    spec = _spec()

    LabMemory(backing).save("scientist-1", spec, goal="wide-gap oxide dielectric")
    recalled = LabMemory(backing).recall("scientist-1")

    assert recalled == spec


def test_lab_memory_recall_returns_none_when_nothing_is_saved():
    """A first-time user (no remembered spec) recalls None — the spec-build step
    then falls back to built-in defaults rather than erroring."""
    assert LabMemory(InMemoryStore()).recall("unknown-scientist") is None
