"""Tests for the LangGraph orchestrator in materials_triage.agent.orchestrator.

The orchestrator runs the nine-step triage workflow as a deterministic, linear,
traced state machine (ADR 0003) — a compiled LangGraph ``StateGraph``, not an
agentic loop. These tests exercise it through its public ``build_orchestrator``
factory and the compiled graph's observable structure / behavior.
"""

from langgraph.checkpoint.memory import MemorySaver

from materials_triage.agent.orchestrator import WORKFLOW_STEPS, build_orchestrator
from materials_triage.core.hypothesis import Citation, ConstraintProposal, Hypothesis
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    ExcludedCandidate,
    PropertyValue,
    Provenance,
    RankingTarget,
    ScoredCandidate,
    TriageResult,
    TriageSpec,
)
from materials_triage.sources.base import SourceAdapter


def test_orchestrator_compiles_with_a_checkpointer_and_wires_the_steps_linearly():
    """Tracer bullet: the workflow's nine named steps compile into a graph backed
    by a checkpointer (the substrate for #9 trace + resume) and wired in a single
    fixed linear edge order START -> gate -> ... -> render -> END. This pins the
    skeleton the later slices fill in, and that execution is a static pipeline —
    not an autonomous loop."""
    compiled = build_orchestrator()

    # A checkpointer is present (v1: MemorySaver) — without it there is no trace
    # to export and no resume.
    assert isinstance(compiled.checkpointer, MemorySaver)

    # The nine canonical steps are all present as nodes.
    drawable = compiled.get_graph()
    node_ids = set(drawable.nodes)
    assert set(WORKFLOW_STEPS) <= node_ids

    # The edges form exactly the linear chain START -> steps... -> END.
    actual_edges = {(e.source, e.target) for e in drawable.edges}
    expected_chain = list(
        zip(
            ("__start__", *WORKFLOW_STEPS),
            (*WORKFLOW_STEPS, "__end__"),
            strict=True,
        )
    )
    for edge in expected_chain:
        assert edge in actual_edges, f"missing linear edge {edge}"


def test_state_channels_round_trip_domain_objects_through_the_checkpointer():
    """Slice 2 (load-bearing per ADR 0003): the graph's state has one typed
    channel per step output, and the checkpointer round-trips the *rich* domain
    objects without flattening them. If a channel were missing or lossy, the
    audit export would silently drop provenance, missing-data flags, exclusion
    reasons, or citations — so this asserts all four survive a run."""
    provenance = Provenance(source="Materials Project", record_id="mp-aaaaadyf")
    candidate = Candidate(
        identifier="mp-aaaaadyf",
        formula="ZnO",
        properties={
            "band_gap": PropertyValue(value=3.3, unit="eV", provenance=provenance),
            # A requested-but-absent property: flagged missing, no number.
            "bulk_modulus": PropertyValue(
                value=None, unit="GPa", missing=True, provenance=provenance
            ),
        },
    )
    excluded = ExcludedCandidate(
        candidate=candidate,
        property_name="band_gap",
        reason="below_min",
        value=3.3,
        bound=4.0,
    )
    result = TriageResult(
        ranked=(ScoredCandidate(candidate=candidate, score=0.8, contributions={"band_gap": 0.8}),),
        excluded=(excluded,),
    )
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=4.0),))
    hypothesis = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=4.0),
                rationale="wide-gap dielectric",
                citations=(Citation(source="OpenAlex", record_id="W1", title="A paper"),),
                confidence=0.7,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )

    # Write the rich objects straight to the channels and read them back, so
    # this isolates the checkpointer's serde round-trip from any node behavior.
    orchestrator = build_orchestrator()
    config = {"configurable": {"thread_id": "round-trip"}}
    orchestrator.update_state(
        config,
        {
            "goal": "find a wide-gap oxide dielectric",
            "run_id": "run-1",
            "spec": spec,
            "hypothesis": hypothesis,
            "candidates": (candidate,),
            "excluded": (excluded,),
            "result": result,
        },
    )

    values = orchestrator.get_state(config).values
    assert values["goal"] == "find a wide-gap oxide dielectric"
    assert values["run_id"] == "run-1"
    # Provenance survives on a present value.
    assert values["candidates"][0].properties["band_gap"].provenance.record_id == "mp-aaaaadyf"
    # The missing-data flag survives (value still None).
    assert values["candidates"][0].properties["bulk_modulus"].missing is True
    assert values["candidates"][0].properties["bulk_modulus"].value is None
    # The structured exclusion reason survives.
    assert values["excluded"][0].reason == "below_min"
    assert values["excluded"][0].bound == 4.0
    # A literature citation on a hypothesis proposal survives.
    assert values["hypothesis"].proposals[0].citations[0].record_id == "W1"
    # The ranked result (score + contributions) survives.
    assert values["result"].ranked[0].score == 0.8
    assert values["result"].ranked[0].contributions["band_gap"] == 0.8


class _FakeAdapter(SourceAdapter):
    """An offline retrieval seam: returns a fixed candidate list, ignoring the
    spec, so the deterministic core can be exercised without any network."""

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


def test_deterministic_core_runs_retrieve_filter_rank_end_to_end():
    """Slice 3: with a spec and an injected (fake) retrieval source — and NO LLM
    — invoking the graph runs retrieve -> filter -> rank and lands a real
    TriageResult in state: survivors ranked best-first, and every hard-filter
    drop carried in the result with its structured reason."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )
    keep_high = _candidate("mp-high", 4.0)
    keep_low = _candidate("mp-low", 3.0)
    drop = _candidate("mp-drop", 1.0)  # below the band_gap >= 2.0 hard filter
    adapter = _FakeAdapter([keep_high, keep_low, drop])

    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "core"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    # Retrieval populated the candidates channel with all three.
    assert {c.identifier for c in final["candidates"]} == {"mp-high", "mp-low", "mp-drop"}

    result = final["result"]
    assert isinstance(result, TriageResult)
    # Survivors are ranked best-first by band_gap (4.0 before 3.0).
    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-high", "mp-low"]
    # The hard-filter drop is carried in the result with its reason.
    drops = {ex.candidate.identifier: ex.reason for ex in result.excluded}
    assert drops == {"mp-drop": "below_min"}
