"""Lab memory: the orchestrator's cross-run persistence bucket.

Per ADR 0003 this is a thin wrapper over a LangGraph ``BaseStore`` — distinct
from the per-run checkpointer. The checkpointer holds one run's live state; lab
memory holds durable artifacts that outlive any run, namely finalized specs, so a
later (separate) run can recall one as a seed. v1 has no mandatory profile: the
spec is LLM-built and remembered here.

The store keeps JSON-able dicts, so a ``TriageSpec`` is serialized with pydantic
on save and validated back on recall — our own JSON, not a binary serde.
"""

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from materials_triage.core.schema import TriageSpec


class LabMemory:
    """Save and recall finalized specs across runs.

    ``key`` is caller-chosen (e.g. a scientist or session id); the same key in a
    later run recalls the saved spec. Backed by an injected ``BaseStore`` (default
    in-process ``InMemoryStore``), mirroring the checkpointer's v1 default.
    """

    NAMESPACE = ("lab_memory", "specs")

    def __init__(self, store: BaseStore | None = None):
        self._store = store or InMemoryStore()

    def save(self, key: str, spec: TriageSpec, goal: str = "") -> str:
        """Remember ``spec`` (with the ``goal`` it served) under ``key``; returns the key."""
        self._store.put(
            self.NAMESPACE,
            key,
            {"goal": goal, "spec": spec.model_dump(mode="json")},
        )
        return key

    def recall(self, key: str) -> TriageSpec | None:
        """Return the spec saved under ``key`` as a seed, or None if there is none."""
        item = self._store.get(self.NAMESPACE, key)
        if item is None:
            return None
        return TriageSpec.model_validate(item.value["spec"])
