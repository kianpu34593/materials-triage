"""Tests for the LangGraph orchestrator in materials_triage.agent.orchestrator.

The orchestrator runs the nine-step triage workflow as a deterministic, linear,
traced state machine (ADR 0003) — a compiled LangGraph ``StateGraph``, not an
agentic loop. These tests exercise it through its public ``build_orchestrator``
factory and the compiled graph's observable structure / behavior.
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from materials_triage.agent.orchestrator import (
    DEFAULT_MAX_HYPOTHESIS_ATTEMPTS,
    WORKFLOW_STEPS,
    HypothesisConformanceError,
    SpecCompilationError,
    build_orchestrator,
)
from materials_triage.core.hypothesis import (
    Citation,
    ConstraintProposal,
    Hypothesis,
    RankingProposal,
)
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
            "filter_excluded": (excluded,),
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
    assert values["filter_excluded"][0].reason == "below_min"
    assert values["filter_excluded"][0].bound == 4.0
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


def test_filter_and_ranking_exclusions_live_in_separate_authoritative_channels():
    """Slice 3b: exclusions are split by STAGE into two single-writer channels —
    `filter_excluded` (the hard-filter node) and `rank_excluded` (the ranking
    node's on_missing='exclude' drops) — so neither undercounts and no node
    reads-then-writes the same channel (resume-safe). `result.excluded` remains
    the union for presentation. The audit exporter reads the stage channels."""
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=0.5),
            # An exclude-policy target: a candidate missing `density` is dropped
            # at the ranking stage (a missing_data drop that is NOT a hard-filter drop).
            RankingTarget(
                property_name="density",
                direction="minimize",
                weight=0.5,
                on_missing="exclude",
            ),
        ),
    )

    def candidate(identifier, band_gap, density=None):
        provenance = Provenance(source="Materials Project", record_id=identifier)
        properties = {"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=provenance)}
        if density is not None:
            properties["density"] = PropertyValue(
                value=density, unit="g/cm^3", provenance=provenance
            )
        return Candidate(identifier=identifier, formula="ZnO", properties=properties)

    keep = candidate("mp-keep", band_gap=4.0, density=5.0)  # passes both stages
    filter_drop = candidate("mp-filterdrop", band_gap=1.0, density=5.0)  # below_min
    rank_drop = candidate("mp-rankdrop", band_gap=3.0)  # passes filter, no density
    adapter = _FakeAdapter([keep, filter_drop, rank_drop])

    orchestrator = build_orchestrator(adapter=adapter)
    config = {"configurable": {"thread_id": "stage-split"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    # Each stage channel is authoritative for exactly its own stage's drops.
    filter_drops = {ex.candidate.identifier: ex.reason for ex in final["filter_excluded"]}
    rank_drops = {ex.candidate.identifier: ex.reason for ex in final["rank_excluded"]}
    assert filter_drops == {"mp-filterdrop": "below_min"}
    assert rank_drops == {"mp-rankdrop": "missing_data"}

    # result.excluded is the union of both stages (the presentation model).
    assert {ex.candidate.identifier for ex in final["result"].excluded} == {
        "mp-filterdrop",
        "mp-rankdrop",
    }
    # Only the fully-scored candidate survives to the ranking.
    assert [sc.candidate.identifier for sc in final["result"].ranked] == ["mp-keep"]


def _valid_hypothesis():
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide-gap dielectric",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


class _FlakyProvider:
    """An offline LLM-provider seam that raises a real pydantic ValidationError
    (malformed structured output) for the first ``fail_times`` calls, then
    returns a valid Hypothesis. Records every prompt it was handed."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.prompts = []

    def propose(self, prompt):
        self.prompts.append(prompt)
        if len(self.prompts) <= self.fail_times:
            # The exact failure mode measured against live Bedrock: the model
            # emits output the Hypothesis schema rejects.
            Hypothesis(proposals=(), mechanism="malformed")  # raises ValidationError
        return _valid_hypothesis()


def test_hypothesis_node_retries_malformed_llm_output_then_succeeds():
    """Slice 4: the hypothesis node conforms the LLM to the Hypothesis schema by
    RETRYING on a pydantic ValidationError (the measured ~15% malformed-output
    rate) and feeding the rejection back into the next prompt — no human in the
    loop. After two bad attempts and one good one, a valid Hypothesis lands in
    state and the provider was called three times."""
    provider = _FlakyProvider(fail_times=2)
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "retry"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide dielectric", "spec": spec}, config)

    assert isinstance(final["hypothesis"], Hypothesis)
    assert len(final["hypothesis"].proposals) == 1
    assert len(provider.prompts) == 3  # two rejected attempts + one accepted
    # The rejection was fed back: the retry prompt differs from the first.
    assert provider.prompts[1] != provider.prompts[0]


def test_hypothesis_node_raises_a_wrapped_error_when_retries_are_exhausted():
    """If the LLM never conforms within the cap, the node raises a
    HypothesisConformanceError (not a raw pydantic ValidationError leaking out),
    preserving the last ValidationError as its cause for the human/audit."""
    provider = _FlakyProvider(fail_times=99)  # never succeeds
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "exhausted"}}
    with pytest.raises(HypothesisConformanceError) as excinfo:
        orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert len(provider.prompts) == DEFAULT_MAX_HYPOTHESIS_ATTEMPTS  # capped, no infinite loop


class _BrokenProvider:
    """A provider whose call fails for a NON-schema reason (transport/throttle)."""

    def __init__(self):
        self.calls = 0

    def propose(self, prompt):
        self.calls += 1
        raise RuntimeError("bedrock unavailable")


def test_hypothesis_node_does_not_retry_non_validation_errors():
    """Retry is only for malformed structured output. A transport/throttle error
    is not a conformance problem, so it propagates immediately — no wasted retries
    and no masking an infra failure as a schema failure."""
    provider = _BrokenProvider()
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "broken"}}
    with pytest.raises(RuntimeError, match="bedrock unavailable"):
        orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert provider.calls == 1  # not retried


class _StubProvider:
    """A provider that returns a fixed Hypothesis (no flakiness) — used to drive
    the spec_build step from a known hypothesis."""

    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def propose(self, prompt):
        return self._hypothesis


def _hypothesis_with_unnormalized_weights():
    """A constraint plus two ranking proposals whose weights (0.6, 0.2) do NOT
    sum to 1, so compile_spec rescales them to 0.75 / 0.25."""
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="band_gap", direction="maximize", weight=0.6
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="density", direction="minimize", weight=0.2
                ),
                rationale="prefer lighter",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


def test_spec_build_pauses_for_human_confirmation_with_normalized_weights():
    """Slice 5 (HITL): with a hypothesis and no pre-resolved spec, spec_build
    compiles the recommended TriageSpec and PAUSES via interrupt(), surfacing it
    for human confirmation — explicitly flagging that the ranking weights were
    rescaled (0.6/0.2 -> 0.75/0.25), the weight-normalization confirmation debt.
    Nothing is committed to the spec channel until the human resumes."""
    provider = _StubProvider(_hypothesis_with_unnormalized_weights())

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "hitl-pause"}}
    result = orchestrator.invoke({"goal": "wide-gap oxide"}, config)  # NO spec supplied

    assert "__interrupt__" in result  # the run paused for the human
    payload = result["__interrupt__"][0].value
    assert payload["weights_were_normalized"] is True
    weights = {t.property_name: t.weight for t in payload["recommended_spec"].ranking_targets}
    assert weights["band_gap"] == pytest.approx(0.75)
    assert weights["density"] == pytest.approx(0.25)

    # The recommendation is not yet committed — the human still has to confirm.
    assert orchestrator.get_state(config).values.get("spec") is None


def test_spec_build_resume_accept_commits_recommended_spec_and_continues():
    """Resuming the paused run by echoing the recommended spec back (accept)
    commits it to state and lets the pipeline continue past spec_build to
    completion."""
    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "hitl-accept"}}
    paused = orchestrator.invoke({"goal": "wide-gap oxide"}, config)  # pauses at spec_build
    recommended = paused["__interrupt__"][0].value["recommended_spec"]

    final = orchestrator.invoke(Command(resume=recommended), config)  # accept as-is

    spec = final["spec"]
    assert isinstance(spec, TriageSpec)
    weights = {t.property_name: t.weight for t in spec.ranking_targets}
    assert weights["band_gap"] == pytest.approx(0.75)
    # The run continued past the gate to the end (a result was produced).
    assert isinstance(final["result"], TriageResult)


def test_spec_build_resume_with_an_edit_uses_the_humans_spec():
    """Resuming with an edited TriageSpec uses the human's version, not the
    recommendation — the human is authoritative over the final spec."""
    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "hitl-edit"}}
    orchestrator.invoke({"goal": "wide-gap oxide"}, config)

    edited = TriageSpec(constraints=(Constraint(property_name="band_gap", min=5.0),))
    final = orchestrator.invoke(Command(resume=edited), config)

    assert final["spec"].constraints[0].min == 5.0
    assert final["spec"].ranking_targets == ()  # the human dropped the ranking targets


def test_spec_build_wraps_an_incoherent_compile_spec_error():
    """If the proposals are individually valid but don't compile to a coherent
    TriageSpec (here: two constraints on the same property), spec_build raises a
    wrapped SpecCompilationError preserving the pydantic ValidationError — no raw
    validation dump leaks, and the pipeline never pauses on an uncompilable spec."""
    incoherent = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="lower bound",
                confidence=0.8,
            ),
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", max=9.0),
                rationale="upper bound (duplicate property)",
                confidence=0.8,
            ),
        ),
        mechanism="m",
    )
    provider = _StubProvider(incoherent)
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "hitl-bad"}}

    with pytest.raises(SpecCompilationError) as excinfo:
        orchestrator.invoke({"goal": "wide-gap oxide"}, config)

    assert isinstance(excinfo.value.__cause__, ValidationError)
