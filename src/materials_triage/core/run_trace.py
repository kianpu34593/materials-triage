"""The audit trace: TriageRun / Step models and the checkpoint-history exporter.

Per ADR 0003 the durable audit report is a read-only view DERIVED from the
orchestrator's LangGraph checkpoint history, not a second store. ``export_run``
walks ``get_state_history()`` and folds it into a ``TriageRun``: the final domain
artifacts (spec, hypothesis, result, exclusions) as named fields, plus an ordered
per-step trace where each ``Step`` records only what that step wrote (the delta
between consecutive state snapshots). One write path (the checkpointer); this is
the one derived read-model.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from materials_triage.core.hypothesis import Hypothesis
from materials_triage.core.schema import (
    Candidate,
    ExcludedCandidate,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.synthesis import Synthesis


class Step(BaseModel):
    """One executed workflow step in the audit trace: its name and the channel
    writes it contributed (the delta it added to the shared state). Pass-through
    steps carry an empty ``writes``."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    writes: dict[str, Any] = Field(default_factory=dict)


class TriageRun(BaseModel):
    """The audit-shaped record of one orchestrator run: the final domain
    artifacts as named fields (so the renderers read them directly) plus the
    ordered per-step trace (so the audit can show what each step did)."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    goal: str = ""
    spec: TriageSpec | None = None
    hypothesis: Hypothesis | None = None
    candidates: tuple[Candidate, ...] = ()
    filter_excluded: tuple[ExcludedCandidate, ...] = ()
    rank_excluded: tuple[ExcludedCandidate, ...] = ()
    result: TriageResult | None = None
    synthesis: Synthesis | None = None
    steps: tuple[Step, ...] = ()


def export_run(orchestrator, config: dict) -> TriageRun:
    """Derive the audit-shaped TriageRun from a completed run's checkpoint history.

    Walks the state snapshots in execution order. Each snapshot's ``.next`` names
    the step that runs to produce the following snapshot, so the write made by a
    step is the delta between consecutive snapshots' ``.values``. The final
    snapshot holds the accumulated state the top-level artifacts are read from.
    """
    snapshots = list(reversed(list(orchestrator.get_state_history(config))))
    if not snapshots:
        raise ValueError("no checkpoint history for the given config; nothing to export")

    steps: list[Step] = []
    for earlier, later in zip(snapshots, snapshots[1:], strict=False):
        if not earlier.next:
            continue
        node = earlier.next[0]
        if node == "__start__":
            continue
        writes = {
            key: value
            for key, value in later.values.items()
            if key not in earlier.values or earlier.values[key] != value
        }
        steps.append(Step(name=node, writes=writes))

    final = snapshots[-1].values
    run_id = final.get("run_id") or config["configurable"]["thread_id"]
    return TriageRun(
        run_id=run_id,
        goal=final.get("goal", ""),
        spec=final.get("spec"),
        hypothesis=final.get("hypothesis"),
        candidates=tuple(final.get("candidates", ())),
        filter_excluded=tuple(final.get("filter_excluded", ())),
        rank_excluded=tuple(final.get("rank_excluded", ())),
        result=final.get("result"),
        synthesis=final.get("synthesis"),
        steps=tuple(steps),
    )


def write_run(run: TriageRun, runs_dir: Path | str) -> Path:
    """Persist the audit artifact to ``<runs_dir>/<run_id>.json``.

    Serializes via pydantic's JSON (which handles the nested domain models),
    deliberately NOT the checkpointer's serde: this file is our own durable,
    human-/tool-readable record, independent of LangGraph's checkpoint format.
    Creates ``runs_dir`` if needed and returns the written path.

    ``run_id`` is caller-supplied input, so it is validated to a single safe
    filename segment before being joined: a value containing a path separator or
    ``..`` is rejected (rather than silently escaping ``runs_dir``).
    """
    run_id = run.run_id
    if run_id in (".", "..") or run_id != Path(run_id).name:
        raise ValueError(f"run_id {run_id!r} is not a safe filename segment")
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run_id}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path
