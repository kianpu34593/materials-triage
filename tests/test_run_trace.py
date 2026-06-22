"""Tests for the audit-trace exporter in materials_triage.core.run_trace.

Per ADR 0003 the durable audit report (`runs/<id>.json`, what `view=audit`
renders) is a read-only export DERIVED from the orchestrator's checkpoint
history — not a second write path. export_run walks the run's state history and
produces a TriageRun: the final domain artifacts plus an ordered, per-step trace
of what each workflow step wrote.
"""

import pytest

from materials_triage.agent.orchestrator import WORKFLOW_STEPS, build_orchestrator
from materials_triage.core.run_trace import Step, TriageRun, export_run, write_run
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RankingTarget,
    TriageResult,
    TriageSpec,
)
from materials_triage.sources.base import SourceAdapter


class _FakeAdapter(SourceAdapter):
    def __init__(self, candidates):
        self._candidates = candidates

    def retrieve(self, spec):
        return list(self._candidates)


def _candidate(identifier, band_gap):
    provenance = Provenance(source="Materials Project", record_id=identifier)
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=provenance)},
    )


def test_export_run_builds_a_triagerun_with_final_artifacts_and_per_step_writes():
    """A completed deterministic run exports to a TriageRun carrying the final
    artifacts (spec, result) AND an ordered per-step trace whose deltas attribute
    each write to the step that made it (retrieve -> candidates, rank -> result)."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )
    adapter = _FakeAdapter([_candidate("mp-keep", 4.0), _candidate("mp-drop", 1.0)])
    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "export-1"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "run_id": "run-1", "spec": spec}, config)

    run = export_run(orchestrator, config)

    assert isinstance(run, TriageRun)
    assert run.run_id == "run-1"
    assert run.goal == "wide-gap oxide"
    # Final artifacts are top-level and structured.
    assert run.spec == spec
    assert [sc.candidate.identifier for sc in run.result.ranked] == ["mp-keep"]
    assert {ex.candidate.identifier for ex in run.result.excluded} == {"mp-drop"}

    # The per-step trace covers every workflow step, in execution order.
    assert [s.name for s in run.steps] == list(WORKFLOW_STEPS)
    assert all(isinstance(s, Step) for s in run.steps)
    by_name = {s.name: s for s in run.steps}
    # Each write is attributed to the step that produced it.
    assert "candidates" in by_name["retrieve"].writes
    assert "filter_excluded" in by_name["filter"].writes
    assert "result" in by_name["rank"].writes
    # Pass-through steps wrote nothing.
    assert by_name["gate"].writes == {}
    # Final result object matches what the rank step wrote.
    assert isinstance(by_name["rank"].writes["result"], TriageResult)


def test_write_run_persists_the_triagerun_as_json_under_its_run_id(tmp_path):
    """The durable audit artifact is `<runs_dir>/<run_id>.json`. write_run
    serializes the TriageRun (via pydantic JSON, not the checkpointer's serde),
    creating the directory; the structured final artifacts survive a reload."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )
    adapter = _FakeAdapter([_candidate("mp-keep", 4.0), _candidate("mp-drop", 1.0)])
    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "export-write"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "run_id": "run-1", "spec": spec}, config)
    run = export_run(orchestrator, config)

    runs_dir = tmp_path / "runs"
    path = write_run(run, runs_dir)

    assert path == runs_dir / "run-1.json"
    assert path.exists()

    reloaded = TriageRun.model_validate_json(path.read_text())
    assert reloaded.run_id == "run-1"
    assert reloaded.goal == "wide-gap oxide"
    assert reloaded.spec == spec
    assert [sc.candidate.identifier for sc in reloaded.result.ranked] == ["mp-keep"]
    assert {ex.candidate.identifier for ex in reloaded.result.excluded} == {"mp-drop"}


def test_write_run_rejects_a_run_id_that_would_escape_the_runs_dir(tmp_path):
    """run_id is caller-supplied and used as a filename, so a value containing a
    path separator or '..' is rejected rather than writing outside runs_dir."""
    for unsafe in ("../escape", "a/b", ".."):
        with pytest.raises(ValueError, match="not a safe filename segment"):
            write_run(TriageRun(run_id=unsafe), tmp_path / "runs")
    # A normal run_id still writes the expected file.
    path = write_run(TriageRun(run_id="run-ok"), tmp_path / "runs")
    assert path == tmp_path / "runs" / "run-ok.json"
