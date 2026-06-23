"""Tests for the audit-trace exporter in materials_triage.core.run_trace.

Per ADR 0003 the durable audit report (`runs/<id>.json`, what `view=audit`
renders) is a read-only export DERIVED from the orchestrator's checkpoint
history — not a second write path. export_run walks the run's state history and
produces a TriageRun: the final domain artifacts plus an ordered, per-step trace
of what each workflow step wrote.
"""

import pytest

from materials_triage.agent.orchestrator import WORKFLOW_STEPS, build_orchestrator
from materials_triage.core.hypothesis import ConstraintProposal, Hypothesis
from materials_triage.core.run_trace import Step, TriageRun, export_run, write_run
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PredicateRouting,
    PropertyValue,
    Provenance,
    RankingTarget,
    RetrievalResult,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.retrieval.rag import LiteraturePassage
from materials_triage.sources.base import SourceAdapter


class _FakeAdapter(SourceAdapter):
    def __init__(self, candidates, routing=None, caveats=()):
        self._candidates = candidates
        self._routing = routing or PredicateRouting()
        self._caveats = tuple(caveats)

    def retrieve(self, spec):
        return RetrievalResult(candidates=tuple(self._candidates), caveats=self._caveats)

    def classify_predicates(self, spec):
        return self._routing


def _candidate(identifier, band_gap):
    provenance = Provenance(
        source="Materials Project", record_id=identifier, method="computational"
    )
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=provenance)},
    )


class _StubSynthesisProvider:
    """Returns a fixed Synthesis, so a full run reaches the synthesis step offline."""

    def __init__(self, synthesis):
        self._synthesis = synthesis

    def synthesize(self, prompt):
        return self._synthesis


def test_export_run_carries_the_synthesis_narrative():
    """The synthesis step's grounded narrative is a top-level TriageRun field, so the
    renderers (the PI view leads with the summary) read it directly from the export —
    not by digging through the per-step trace."""
    synthesis = Synthesis(
        summary="ZnO leads for a wide-gap photocatalyst.",
        claims=(GroundedClaim(text="ZnO has a ~3 eV gap.", record_id="mp-1"),),
    )
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    orchestrator = build_orchestrator(
        adapter=adapter, synthesis_provider=_StubSynthesisProvider(synthesis)
    )
    config = {"configurable": {"thread_id": "export-synth"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "run_id": "run-s", "spec": spec}, config)

    run = export_run(orchestrator, config)

    assert run.synthesis == synthesis


class _RagProvider:
    """A hypothesis provider that extracts fixed keywords and returns a compiling
    hypothesis, so a run reaches retrieval with literature persisted."""

    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def extract_keywords(self, goal):
        return "wide band gap oxide"

    def propose(self, prompt):
        return self._hypothesis


class _FakeRag:
    def __init__(self, passages):
        self._passages = passages

    def search(self, query, k=10):
        return list(self._passages)


def test_export_run_carries_the_retrieved_literature():
    """The literature the hypothesis step retrieved (and synthesis reused) is a
    top-level TriageRun field, so the audit view can show what the run was grounded in."""
    passage = LiteraturePassage(
        provenance=Provenance(source="OpenAlex", record_id="W1", method="literature"),
        title="Wide-gap oxides",
        text="ZnO shows a wide band gap.",
    )
    hypothesis = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
        ),
        mechanism="m",
    )
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    orchestrator = build_orchestrator(
        adapter=adapter, provider=_RagProvider(hypothesis), rag=_FakeRag([passage])
    )
    config = {"configurable": {"thread_id": "export-lit"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "run_id": "run-l", "spec": spec}, config)

    run = export_run(orchestrator, config)

    assert run.literature == (passage,)


def test_export_run_carries_caveats_into_the_trace():
    """The audit TriageRun carries the run's caveats, so both views can surface a hard
    predicate that went unenforced (the source could neither push nor return data for
    it) — the honesty signal, not a silent drop."""
    routing = PredicateRouting(
        caveats=("constraint on 'toxicity' was not applied: Materials Project provides no data",)
    )
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)], routing=routing)
    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "caveat-export"}}
    orchestrator.invoke({"goal": "nontoxic oxide", "spec": spec}, config)

    run = export_run(orchestrator, config)

    assert any("toxicity" in c for c in run.caveats)


def test_export_run_unions_retrieval_and_routing_caveats():
    """Retrieval's own caveats (e.g. a capped result set) and the filter stage's
    routing caveats land in the same trace channel, neither clobbering the other —
    so the audit shows every honesty signal the run produced."""
    routing = PredicateRouting(
        caveats=("constraint on 'toxicity' was not applied: Materials Project provides no data",)
    )
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))
    adapter = _FakeAdapter(
        [_candidate("mp-1", 3.0)],
        routing=routing,
        caveats=("result set capped at 10000 candidates; ranking over a subset",),
    )
    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "caveat-union"}}
    orchestrator.invoke({"goal": "nontoxic oxide", "spec": spec}, config)

    run = export_run(orchestrator, config)

    assert any("capped" in c for c in run.caveats)
    assert any("toxicity" in c for c in run.caveats)


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
