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
    InputRefused,
    SpecCompilationError,
    _output_validate_node,
    build_orchestrator,
    resume_run,
)
from materials_triage.agent.validator import UngroundedOutputError
from materials_triage.core.hypothesis import (
    Citation,
    ConstraintProposal,
    ElementPredicateProposal,
    Hypothesis,
    RankingProposal,
)
from materials_triage.core.run_trace import export_run
from materials_triage.core.schema import (
    BooleanConstraint,
    Candidate,
    Constraint,
    ElementPredicate,
    ExcludedCandidate,
    PredicateRouting,
    PropertyValue,
    Provenance,
    RankingTarget,
    RetrievalResult,
    ScoredCandidate,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.retrieval.rag import LiteraturePassage
from materials_triage.sources.base import SourceAdapter


def test_hypothesis_node_binds_the_adapters_vocabulary_into_the_prompt():
    """End to end through ``build_orchestrator``: the adapter's ``property_vocabulary``
    reaches the prompt the LLM actually receives, so spec-building is constrained to
    the source's real surface (the load-bearing lever for shortlist quality)."""
    provider = _CompileFlakyProvider(fail_times=0)  # records every prompt; returns a compiling one
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)], vocabulary={"band_gap": "eV"})
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(adapter=adapter, provider=provider)
    config = {"configurable": {"thread_id": "vocab-bind"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert provider.prompts  # the node called the provider
    assert "band_gap" in provider.prompts[0]
    assert "Retrievable properties" in provider.prompts[0]


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


def test_gate_refuses_a_forbidden_goal_and_records_no_run():
    """Workflow step 1: the input gate. A goal naming a forbidden capability
    (here scraping a paywalled source) is stopped at the gate — the run raises
    ``InputRefused`` carrying the gate's reason/category and never reaches the
    later steps, so no ``TriageResult`` is produced. (CLAUDE.md: a forbidden
    request is logged and refused, *not* recorded as a TriageRun.)"""
    orchestrator = build_orchestrator()
    config = {"configurable": {"thread_id": "gate-refuse"}}

    with pytest.raises(InputRefused) as excinfo:
        orchestrator.invoke({"goal": "scrape a paywalled database for wide-gap oxides"}, config)

    assert excinfo.value.category == "paywalled"
    assert excinfo.value.reason  # a human-readable reason travels with the refusal
    # The run never produced a result — it stopped at the gate.
    assert orchestrator.get_state(config).values.get("result") is None


def test_state_channels_round_trip_domain_objects_through_the_checkpointer():
    """Slice 2 (load-bearing per ADR 0003): the graph's state has one typed
    channel per step output, and the checkpointer round-trips the *rich* domain
    objects without flattening them. If a channel were missing or lossy, the
    audit export would silently drop provenance, missing-data flags, exclusion
    reasons, or citations — so this asserts all four survive a run."""
    provenance = Provenance(
        source="Materials Project", record_id="mp-aaaaadyf", method="computational"
    )
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

    def __init__(self, candidates, routing=None, caveats=(), vocabulary=None):
        self._candidates = candidates
        self._routing = routing or PredicateRouting()
        self._caveats = tuple(caveats)
        self._vocabulary = vocabulary or {}

    def retrieve(self, spec):
        return RetrievalResult(candidates=tuple(self._candidates), caveats=self._caveats)

    def classify_predicates(self, spec):
        return self._routing

    def property_vocabulary(self):
        return self._vocabulary


def _candidate(identifier, band_gap):
    provenance = Provenance(
        source="Materials Project", record_id=identifier, method="computational"
    )
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


def test_filter_node_records_routing_caveats_in_state():
    """Make it loud: the filter node surfaces the routing's caveats — predicates the
    source could neither push nor enforce locally (¬R∩¬Q) — into a `caveats` channel,
    so the run records that a constraint went unapplied instead of silently dropping
    everything or ignoring it."""
    routing = PredicateRouting(
        caveats=("constraint on 'toxicity' was not applied: Materials Project provides no data",)
    )
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)], routing=routing)
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))

    final = build_orchestrator(adapter=adapter).invoke(
        {"goal": "nontoxic oxide", "spec": spec}, {"configurable": {"thread_id": "caveats"}}
    )

    assert any("toxicity" in c for c in final["caveats"])


def test_filter_node_enforces_the_adapters_local_bucket():
    """The filter node enforces the source's exclusive set (predicates it couldn't
    push), not just numeric constraints: a candidate failing a routed-local boolean
    (`is_magnetic`) is dropped with `boolean_mismatch`, in the same `filter_excluded`
    channel as numeric drops."""
    prov = Provenance(source="Materials Project", record_id="x", method="computational")

    def candidate(identifier, is_magnetic):
        return Candidate(
            identifier=identifier,
            formula="Fe2O3",
            properties={
                "is_magnetic": PropertyValue(value=is_magnetic, unit=None, provenance=prov)
            },
        )

    magnetic = candidate("mp-mag", 1.0)
    nonmagnetic = candidate("mp-non", 0.0)
    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_magnetic", required=True),)
    )
    routing = PredicateRouting(
        local_booleans=(BooleanConstraint(property_name="is_magnetic", required=True),)
    )
    adapter = _FakeAdapter([magnetic, nonmagnetic], routing=routing)

    final = build_orchestrator(adapter=adapter).invoke(
        {"goal": "magnetic oxide", "spec": spec}, {"configurable": {"thread_id": "local-filter"}}
    )

    survivors = {c.identifier for c in final["survivors"]}
    filter_drops = {ex.candidate.identifier: ex.reason for ex in final["filter_excluded"]}
    assert survivors == {"mp-mag"}
    assert filter_drops == {"mp-non": "boolean_mismatch"}


def test_rank_node_selects_the_method_recorded_on_the_spec():
    """The ranker is chosen by `spec.ranking_method`: a 'geometric_mean' spec runs
    the non-compensatory geometric mean, so a balanced candidate outranks one that
    aces a target but zeroes another — an order the default weighted-sum would not
    produce (it ties them). This proves the dispatch reads the spec, not a
    hard-coded ranker."""
    prov = Provenance(source="Materials Project", record_id="x", method="computational")

    def _two_prop(identifier, gap, density):
        return Candidate(
            identifier=identifier,
            formula="ZnO",
            properties={
                "band_gap": PropertyValue(value=gap, unit="eV", provenance=prov),
                "density": PropertyValue(value=density, unit="g/cm^3", provenance=prov),
            },
        )

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=0.0),),
        ranking_targets=(
            RankingTarget(
                property_name="band_gap", direction="maximize", weight=0.5, lower=0.0, target=10.0
            ),
            RankingTarget(
                property_name="density", direction="minimize", weight=0.5, target=0.0, upper=10.0
            ),
        ),
        ranking_method="geometric_mean",
    )
    spike = _two_prop("mp-spike", 10.0, 10.0)  # perfect gap, worst density -> zeroed
    balanced = _two_prop("mp-balanced", 5.0, 5.0)  # middling on both
    adapter = _FakeAdapter([spike, balanced])

    orchestrator = build_orchestrator(adapter=adapter)
    final = orchestrator.invoke(
        {"goal": "balanced oxide", "spec": spec}, {"configurable": {"thread_id": "desir"}}
    )

    ranked = final["result"].ranked
    assert [sc.candidate.identifier for sc in ranked] == ["mp-balanced", "mp-spike"]
    assert ranked[-1].score == 0.0


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
        provenance = Provenance(
            source="Materials Project", record_id=identifier, method="computational"
        )
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


def _ranking_hypothesis(*, bounded):
    """A shape-valid Hypothesis whose ranking target either announces its ramp
    bounds (compiles under the geometric default) or omits them (does not)."""
    target = (
        RankingTarget(
            property_name="band_gap", direction="maximize", weight=1.0, lower=1.0, target=3.0
        )
        if bounded
        else RankingTarget(property_name="band_gap", direction="maximize", weight=1.0)
    )
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
            RankingProposal(ranking_target=target, rationale="prefer wider", confidence=0.8),
        ),
        mechanism="m",
    )


class _CompileFlakyProvider:
    """Returns a SHAPE-valid but non-COMPILING hypothesis (a ranking target missing
    the ramp bounds the geometric default requires) for the first ``fail_times``
    calls, then a compilable one. Records every prompt it was handed."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.prompts = []

    def propose(self, prompt):
        self.prompts.append(prompt)
        return _ranking_hypothesis(bounded=len(self.prompts) > self.fail_times)


def test_hypothesis_node_retries_when_proposals_do_not_compile():
    """A shape-valid Hypothesis can still fail to compile — here a ranking target
    missing the ramp bounds the default geometric ranker requires. The node
    trial-compiles inside the retry loop and feeds the compile error back, so a
    transient omission self-corrects instead of failing terminally later in
    spec_build with no feedback to the LLM."""
    provider = _CompileFlakyProvider(fail_times=1)
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "compile-retry"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert isinstance(final["hypothesis"], Hypothesis)
    assert len(provider.prompts) == 2  # one non-compiling attempt + one good
    assert provider.prompts[1] != provider.prompts[0]  # the compile error was fed back


def test_hypothesis_node_raises_when_proposals_never_compile():
    """If the proposals never compile within the cap, the node raises the same
    wrapped HypothesisConformanceError (cause = the compile ValidationError), not a
    terminal SpecCompilationError downstream."""
    provider = _CompileFlakyProvider(fail_times=99)  # never compiles
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "compile-exhausted"}}
    with pytest.raises(HypothesisConformanceError) as excinfo:
        orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert len(provider.prompts) == DEFAULT_MAX_HYPOTHESIS_ATTEMPTS


class _RagProvider:
    """A provider that records every propose() prompt, extracts a fixed keyword
    string from the goal (recording the goal it saw), and returns a compiling
    hypothesis — so the node's RAG-grounding path can be observed offline."""

    def __init__(self, keywords):
        self._keywords = keywords
        self.prompts = []
        self.extracted_goals = []

    def extract_keywords(self, goal):
        self.extracted_goals.append(goal)
        return self._keywords

    def propose(self, prompt):
        self.prompts.append(prompt)
        return _ranking_hypothesis(bounded=True)


class _FakeRag:
    """An offline LiteratureRAG seam: records the query it was handed and returns a
    fixed passage list, so the node's literature grounding is testable without OpenAlex."""

    def __init__(self, passages):
        self._passages = passages
        self.queries = []

    def search(self, query, k=10):
        self.queries.append(query)
        return list(self._passages)


class _StubSynthesisProvider:
    """An offline synthesis LLM seam: records every prompt and returns a fixed
    Synthesis (or one per call, to drive the grounding-retry path)."""

    def __init__(self, *syntheses):
        self._syntheses = list(syntheses)
        self.prompts = []

    def synthesize(self, prompt):
        self.prompts.append(prompt)
        return self._syntheses[min(len(self.prompts) - 1, len(self._syntheses) - 1)]


def test_output_validate_rejects_a_presented_candidate_without_provenance():
    """Workflow step 8 is the final grounding gate: if the output presents a material
    not in retrieved provenance, the validator refuses rather than render it. This path
    is unreachable through the happy graph (survivors are always a subset of retrieved),
    so it is the defensive backstop — exercised here directly with inconsistent state."""
    ghost = ScoredCandidate(candidate=_candidate("mp-ghost", 3.0), score=1.0)
    state = {
        "goal": "wide-gap oxide",
        "result": TriageResult(ranked=(ghost,)),
        "candidates": (_candidate("mp-1", 3.0),),  # mp-ghost was never retrieved
        "synthesis": None,
    }

    with pytest.raises(UngroundedOutputError):
        _output_validate_node(state)


def test_output_validate_accepts_a_fully_grounded_output():
    """The gate passes silently (no state change) when every presented candidate and
    citation resolves to a retrieved record id — it must not reject valid output."""
    grounded = ScoredCandidate(candidate=_candidate("mp-1", 3.0), score=1.0)
    state = {
        "goal": "wide-gap oxide",
        "result": TriageResult(ranked=(grounded,)),
        "candidates": (_candidate("mp-1", 3.0),),
        "synthesis": Synthesis(
            summary="ZnO leads.",
            claims=(GroundedClaim(text="ZnO ~3 eV.", record_id="mp-1"),),
        ),
    }

    assert _output_validate_node(state) == {}


def test_synthesis_node_lands_a_grounded_synthesis_in_state():
    """Workflow step 7: with a synthesis provider injected, the node turns the ranked
    result into a grounded Synthesis and lands it in state — every claim cites a
    retrieved candidate (here mp-1), so the narrative is non-fabricated by construction."""
    synthesis = Synthesis(
        summary="ZnO leads for a wide-gap photocatalyst.",
        claims=(GroundedClaim(text="ZnO has a ~3 eV gap.", record_id="mp-1"),),
    )
    syn_provider = _StubSynthesisProvider(synthesis)
    provider = _StubProvider(_valid_hypothesis())
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(
        adapter=adapter, provider=provider, synthesis_provider=syn_provider
    )
    config = {"configurable": {"thread_id": "synth-ok"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert final["synthesis"] is synthesis
    assert syn_provider.prompts  # the node called the synthesis provider


def test_synthesis_node_caps_the_citable_shortlist_to_top_k():
    """build_orchestrator's top_k flows into the synthesis prompt, so even when many
    candidates survive, the LLM is only handed the top_k as citable — the live fix for
    the huge-citable-set hallucination."""
    synthesis = Synthesis(
        summary="ZnO leads.",
        claims=(GroundedClaim(text="ZnO ~3 eV.", record_id="mp-0"),),
    )
    syn_provider = _StubSynthesisProvider(synthesis)
    adapter = _FakeAdapter([_candidate(f"mp-{i}", 3.0) for i in range(6)])
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(adapter=adapter, synthesis_provider=syn_provider, top_k=3)
    config = {"configurable": {"thread_id": "synth-topk"}}
    orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    # 6 survived, but only 3 were listed as citable in the prompt the LLM received.
    assert syn_provider.prompts[0].count("score=") == 3
    assert "3 of 6" in syn_provider.prompts[0]


class _FlakySynthesisProvider:
    """Raises a pydantic ValidationError (malformed Synthesis) for the first
    ``fail_times`` calls, then returns a grounded Synthesis. Records every prompt."""

    def __init__(self, fail_times, good):
        self.fail_times = fail_times
        self._good = good
        self.prompts = []

    def synthesize(self, prompt):
        self.prompts.append(prompt)
        if len(self.prompts) <= self.fail_times:
            Synthesis(summary="   ", claims=())  # raises ValidationError (blank summary)
        return self._good


def test_synthesis_node_retries_malformed_llm_output_then_succeeds():
    """Like the hypothesis step, synthesis conforms the LLM to its schema by RETRYING
    on a pydantic ValidationError (the measured ~15% malformed-output rate), not by
    crashing the run. After one malformed attempt and one good one, the Synthesis lands."""
    good = Synthesis(
        summary="ZnO leads.",
        claims=(GroundedClaim(text="ZnO has a ~3 eV gap.", record_id="mp-1"),),
    )
    syn_provider = _FlakySynthesisProvider(fail_times=1, good=good)
    provider = _StubProvider(_valid_hypothesis())
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(
        adapter=adapter, provider=provider, synthesis_provider=syn_provider
    )
    config = {"configurable": {"thread_id": "synth-malformed"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert final["synthesis"] is good
    assert len(syn_provider.prompts) == 2  # one malformed attempt + one good


def test_synthesis_node_retries_when_a_claim_is_ungrounded_then_succeeds():
    """The synthesis LLM may cite a material that was not retrieved. The node catches
    that with the grounding check, feeds the offending record_id back, and retries —
    so a transient hallucinated citation self-corrects instead of reaching the output
    validator. After one ungrounded attempt and one good one, the grounded Synthesis
    lands and the bad id appears in the retry prompt."""
    bad = Synthesis(
        summary="An invented lead.",
        claims=(GroundedClaim(text="MgX has a huge gap.", record_id="mp-999"),),
    )
    good = Synthesis(
        summary="ZnO leads.",
        claims=(GroundedClaim(text="ZnO has a ~3 eV gap.", record_id="mp-1"),),
    )
    syn_provider = _StubSynthesisProvider(bad, good)
    provider = _StubProvider(_valid_hypothesis())
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(
        adapter=adapter, provider=provider, synthesis_provider=syn_provider
    )
    config = {"configurable": {"thread_id": "synth-retry"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert final["synthesis"] is good
    assert len(syn_provider.prompts) == 2  # one ungrounded attempt + one corrected
    assert "mp-999" in syn_provider.prompts[1]  # the ungrounded id was fed back


def test_synthesis_node_degrades_gracefully_when_it_cannot_ground():
    """If every attempt cites an unretrieved material, the node exhausts its retries
    and DEGRADES rather than crashing the run: it omits the narrative (synthesis stays
    None) and surfaces a loud caveat, so the deterministic ranked shortlist — the
    product's core value — is still delivered. The output validator passes because no
    ungrounded narrative reaches it."""
    ungrounded = Synthesis(
        summary="Always invented.",
        claims=(GroundedClaim(text="bogus", record_id="mp-404"),),
    )
    syn_provider = _StubSynthesisProvider(ungrounded)
    provider = _StubProvider(_valid_hypothesis())
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(
        adapter=adapter, provider=provider, synthesis_provider=syn_provider
    )
    config = {"configurable": {"thread_id": "synth-degrade"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    # The run completed (no crash) and still produced the ranked shortlist.
    assert final["synthesis"] is None
    assert [sc.candidate.identifier for sc in final["result"].ranked] == ["mp-1"]
    # A loud caveat explains the omitted narrative.
    assert any("synthesis" in c.lower() for c in final["synthesis_caveats"])


def test_hypothesis_node_grounds_the_prompt_in_rag_literature_via_extracted_keywords():
    """Workflow step 3 is LLM + RAG: when a ``rag`` seam is injected, the node first
    asks the provider to distill the goal into search keywords, searches the literature
    with *those* keywords (not the raw goal), and grounds the hypothesis prompt in the
    returned (untrusted) abstracts — so the LLM proposes candidates anchored in the
    literature it was handed, not from its own memory."""
    passage = LiteraturePassage(
        provenance=Provenance(source="OpenAlex", record_id="W1", method="literature"),
        title="Wide-gap oxides",
        text="TiO2 shows a wide band gap suited to photocatalysis.",
    )
    rag = _FakeRag([passage])
    provider = _RagProvider(keywords="wide band gap oxide photocatalyst")
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)], vocabulary={"band_gap": "eV"})
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(adapter=adapter, provider=provider, rag=rag)
    config = {"configurable": {"thread_id": "rag-ground"}}
    orchestrator.invoke({"goal": "wide-gap oxide for photocatalysis", "spec": spec}, config)

    assert provider.extracted_goals == ["wide-gap oxide for photocatalysis"]
    # The literature was searched with the EXTRACTED keywords, not the raw goal.
    assert rag.queries == ["wide band gap oxide photocatalyst"]
    # The returned abstract was grounded into the prompt the LLM actually received.
    assert "TiO2 shows a wide band gap suited to photocatalysis." in provider.prompts[0]


def test_hypothesis_node_persists_retrieved_literature_for_reuse_downstream():
    """The passages the hypothesis step retrieves are persisted in run state so the
    synthesis step can ground its narrative in the SAME papers — retrieved once, never
    re-searched. (Without this they would be used in the prompt and discarded.)"""
    passage = LiteraturePassage(
        provenance=Provenance(source="OpenAlex", record_id="W1", method="literature"),
        title="Wide-gap oxides",
        text="TiO2 shows a wide band gap.",
    )
    rag = _FakeRag([passage])
    provider = _RagProvider(keywords="wide band gap oxide")
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)], vocabulary={"band_gap": "eV"})
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    orchestrator = build_orchestrator(adapter=adapter, provider=provider, rag=rag)
    config = {"configurable": {"thread_id": "lit-persist"}}
    final = orchestrator.invoke({"goal": "wide-gap oxide", "spec": spec}, config)

    assert final["literature"] == (passage,)
    assert len(rag.queries) == 1  # searched once for the whole run, not per stage


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
                    property_name="band_gap",
                    direction="maximize",
                    weight=0.6,
                    lower=1.0,
                    target=3.0,
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="density",
                    direction="minimize",
                    weight=0.2,
                    target=2.0,
                    upper=8.0,
                ),
                rationale="prefer lighter",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


class _FakeCritic:
    """A ranking critic returning a fixed RankingCritique, ignoring the prompt."""

    def __init__(self, critique):
        self._critique = critique
        self.calls = 0

    def critique(self, prompt):
        self.calls += 1
        return self._critique


class _RaisingCritic:
    """A critic whose call always fails — to test soft-degradation."""

    def critique(self, prompt):
        raise RuntimeError("bedrock unavailable")


def test_hypothesis_node_prunes_off_goal_ranking_targets_via_the_critic():
    """When a RankingCritic is injected, the hypothesis node runs it on the proposed
    ranking targets and drops the ones it rejects, so an invented objective (density)
    never reaches the compiled spec the human is shown."""
    from materials_triage.core.critique import RankingCritique, TargetVerdict

    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    critic = _FakeCritic(
        RankingCritique(
            verdicts=(
                TargetVerdict(property_name="band_gap", keep=True, reason="core"),
                TargetVerdict(property_name="density", keep=False, reason="goal never asked"),
            )
        )
    )
    orchestrator = build_orchestrator(provider=provider, critic=critic)
    config = {"configurable": {"thread_id": "critic-prune"}}

    result = orchestrator.invoke({"goal": "wide-gap oxide"}, config)

    recommended = result["__interrupt__"][0].value["recommended_spec"]
    targets = {t.property_name for t in recommended.ranking_targets}
    assert targets == {"band_gap"}  # density pruned; band_gap survives, reweighted to 1.0
    assert critic.calls == 1


def test_critic_bound_flags_surface_as_advisory_caveats_in_the_run():
    """Advisory bound flags (#6) the critic raises are surfaced to the human as caveats
    in the exported run — never auto-applied to the spec, just a note. They appear even
    when no ranking target was pruned."""
    from materials_triage.core.critique import BoundFlag, RankingCritique, TargetVerdict

    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    critic = _FakeCritic(
        RankingCritique(
            verdicts=(
                TargetVerdict(property_name="band_gap", keep=True, reason="core"),
                TargetVerdict(property_name="density", keep=True, reason="core"),
            ),
            bound_flags=(
                BoundFlag(property_name="band_gap", concern="a 12 eV ceiling excludes nothing"),
            ),
        )
    )
    orchestrator = build_orchestrator(provider=provider, critic=critic)
    config = {"configurable": {"thread_id": "critic-boundflag"}}
    paused = orchestrator.invoke({"goal": "wide-gap oxide"}, config)
    recommended = paused["__interrupt__"][0].value["recommended_spec"]
    orchestrator.invoke(Command(resume=recommended), config)

    run = export_run(orchestrator, config)

    assert any("12 eV ceiling" in c for c in run.caveats)
    # advisory only: the band_gap constraint bound was NOT rewritten
    assert {t.property_name for t in run.spec.ranking_targets} == {"band_gap", "density"}


def test_hypothesis_node_soft_degrades_when_the_critic_fails():
    """The critic is best-effort grounding, never fatal: if its call raises, the run
    keeps the un-pruned proposals rather than failing."""
    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    orchestrator = build_orchestrator(provider=provider, critic=_RaisingCritic())
    config = {"configurable": {"thread_id": "critic-degrade"}}

    result = orchestrator.invoke({"goal": "wide-gap oxide"}, config)

    recommended = result["__interrupt__"][0].value["recommended_spec"]
    targets = {t.property_name for t in recommended.ranking_targets}
    assert targets == {"band_gap", "density"}  # nothing pruned; run survived


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


def _hypothesis_dropping_the_oxide_facet():
    """A hypothesis that kept the band-gap filter but omitted the "oxide" composition
    rule the goal states — the common LLM fidelity failure the gate must repair."""
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


def test_spec_build_seeds_dropped_facets_before_the_human_gate():
    """The fidelity gate runs inside spec_build, after compile_spec and before the
    interrupt: a goal that says "oxide" but whose hypothesis dropped it gets a
    require-O predicate seeded into the recommended spec the human is shown."""
    provider = _StubProvider(_hypothesis_dropping_the_oxide_facet())
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "fidelity-seed"}}

    result = orchestrator.invoke({"goal": "wide-gap oxide for photocatalysis"}, config)

    recommended = result["__interrupt__"][0].value["recommended_spec"]
    required = {
        e for p in recommended.element_predicates if p.quantifier == "all" for e in p.members
    }
    assert "O" in required  # the dropped "oxide" facet was seeded back in


def test_fidelity_caveat_reaches_the_exported_run():
    """A seeded facet whose enforcement is element-level only (the non-toxic
    oxidation-state caveat) is written to the single-writer `spec_caveats` channel and
    unioned into the exported run's caveats, so the honesty signal reaches the user."""
    provider = _StubProvider(_hypothesis_dropping_the_oxide_facet())
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "fidelity-caveat"}}
    paused = orchestrator.invoke({"goal": "a non-toxic oxide"}, config)
    recommended = paused["__interrupt__"][0].value["recommended_spec"]
    orchestrator.invoke(Command(resume=recommended), config)

    run = export_run(orchestrator, config)

    assert any("oxidation-state" in c for c in run.caveats)


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
    """A hypothesis that reaches spec_build WITHOUT passing the hypothesis node's
    trial-compile — here seeded directly into state with no provider, as on a resume —
    still gets a wrapped SpecCompilationError if its proposals don't compile (here two
    constraints on the same property). No raw validation dump leaks, and the pipeline
    never pauses on an uncompilable spec. (When a provider IS present, the hypothesis
    node trial-compiles and retries first, so this terminal wrapper is the defensive
    backstop for the bypass path.)"""
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
    orchestrator = build_orchestrator()  # no provider → hypothesis node passes the seed through
    config = {"configurable": {"thread_id": "hitl-bad"}}

    with pytest.raises(SpecCompilationError) as excinfo:
        orchestrator.invoke({"goal": "wide-gap oxide", "hypothesis": incoherent}, config)

    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_spec_build_wraps_a_reconcile_coherence_violation():
    """A compilable hypothesis can still become incoherent AFTER the fidelity gate
    seeds a hard facet: 'non-toxic' plus an explicit any:{Pb,Hg} makes that 'any'
    predicate fully covered by the seeded toxic exclusion. reconcile_spec re-validates
    through TriageSpec, so the seeded ValidationError must surface as an attributable
    SpecCompilationError (the same class as a compile_spec failure), not a raw pydantic
    dump that crashes the run."""
    hypothesis = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=1.0),
                rationale="a real constraint so the spec compiles",
                confidence=0.8,
            ),
            ElementPredicateProposal(
                element_predicate=ElementPredicate(
                    quantifier="any", members=frozenset({"Pb", "Hg"})
                ),
                rationale="must contain a heavy metal",
                confidence=0.8,
            ),
        ),
        mechanism="m",
    )
    orchestrator = build_orchestrator()  # no provider → hypothesis node passes the seed through
    config = {"configurable": {"thread_id": "reconcile-incoherent"}}

    with pytest.raises(SpecCompilationError) as excinfo:
        orchestrator.invoke(
            {"goal": "a non-toxic material containing Pb or Hg", "hypothesis": hypothesis},
            config,
        )

    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_spec_build_note_does_not_claim_rescaling_when_weights_already_sum_to_one():
    """Honesty: the human-facing note must not contradict the
    weights_were_normalized flag. When the proposed weights already sum to 1
    (here a single weight of 1.0), the flag is False and the prose must not claim
    the weights were rescaled."""
    hypothesis = Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="band_gap",
                    direction="maximize",
                    weight=1.0,
                    lower=1.0,
                    target=3.0,
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
        ),
        mechanism="m",
    )
    provider = _StubProvider(hypothesis)
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "no-rescale"}}
    result = orchestrator.invoke({"goal": "wide-gap oxide"}, config)

    payload = result["__interrupt__"][0].value
    assert payload["weights_were_normalized"] is False
    assert "rescaled" not in payload["note"].lower()


def test_spec_build_rejects_a_resume_value_that_is_not_a_triagespec():
    """The resume contract is 'an approved TriageSpec'. A resume of the wrong
    type is caught here as an attributable SpecCompilationError, not left to
    surface as an opaque AttributeError in a downstream node."""
    provider = _StubProvider(_hypothesis_with_unnormalized_weights())
    orchestrator = build_orchestrator(provider=provider)
    config = {"configurable": {"thread_id": "bad-resume"}}
    orchestrator.invoke({"goal": "wide-gap oxide"}, config)  # pauses

    with pytest.raises(SpecCompilationError, match="must be a TriageSpec"):
        orchestrator.invoke(Command(resume="not a spec"), config)


class _CountingProvider:
    """A provider that returns a fixed valid Hypothesis and counts its calls, so
    a resume can prove the hypothesis step was NOT re-invoked."""

    def __init__(self):
        self.calls = 0

    def propose(self, prompt):
        self.calls += 1
        return _valid_hypothesis()


class _FlakyAdapter(SourceAdapter):
    """Raises an infra error on the first retrieve (transient outage), succeeds
    on the second — mimicking a recovered backend."""

    def __init__(self):
        self.calls = 0

    def retrieve(self, spec):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Materials Project transiently unavailable")
        return RetrievalResult(candidates=(_candidate("mp-1", 4.0),))


def test_resume_run_recovers_from_an_infra_failure_reusing_upstream_steps():
    """Slice 7 (crash recovery): an infra error (not a ValidationError, so not
    retried in-node) propagates and stops the run at the failing step. resume_run
    continues from that checkpoint — re-running the failed step (now that the
    backend recovered) while REUSING every upstream step's result, so the LLM
    hypothesis call is not re-paid for."""
    provider = _CountingProvider()
    adapter = _FlakyAdapter()
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=2.0),),
        ranking_targets=(
            RankingTarget(property_name="band_gap", direction="maximize", weight=1.0),
        ),
    )
    orchestrator = build_orchestrator(adapter=adapter, provider=provider)
    config = {"configurable": {"thread_id": "resume-infra"}}

    # First attempt: hypothesis runs, then retrieve hits the outage and the run
    # stops (infra errors are deliberately not retried in-node).
    with pytest.raises(RuntimeError, match="transiently unavailable"):
        orchestrator.invoke({"goal": "wide-gap oxide", "run_id": "r", "spec": spec}, config)
    assert provider.calls == 1
    assert adapter.calls == 1

    final = resume_run(orchestrator, config)

    # The hypothesis step was reused from the checkpoint (not re-invoked); only
    # the failed step onward re-ran, and the run completed.
    assert provider.calls == 1
    assert adapter.calls == 2
    assert [sc.candidate.identifier for sc in final["result"].ranked] == ["mp-1"]
