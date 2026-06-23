"""The triage orchestrator: the nine-step workflow as a compiled LangGraph.

Per ADR 0003 the workflow is a deterministic, linear, *traced* state machine —
not an autonomous tool-calling loop. The steps are graph nodes wired in a fixed
linear edge order and compiled with a checkpointer (the substrate for the #9
trace export and `resume --from`). Wired so far: the ``hypothesis`` step (LLM,
with retry-on-malformed-output) and the ``retrieve`` -> ``filter`` -> ``rank``
deterministic core. The remaining steps (gate, spec_build, synthesis,
output_validate, render) are pass-throughs their own slices/tasks replace.
"""

import math
from typing import Protocol, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from materials_triage.agent.prompts import RANKING_TARGET_GUIDANCE
from materials_triage.core.hypothesis import Hypothesis, compile_spec
from materials_triage.core.ranking import rank_arithmetic_mean, rank_geometric_mean
from materials_triage.core.schema import (
    Candidate,
    ExcludedCandidate,
    PredicateRouting,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.scoring import apply_hard_filters, apply_local_filters
from materials_triage.sources.base import SourceAdapter

#: Default cap on how many times the hypothesis node re-invokes the LLM provider
#: when its structured output fails the Hypothesis schema (~15% measured rate).
DEFAULT_MAX_HYPOTHESIS_ATTEMPTS = 3


class HypothesisProvider(Protocol):
    """The LLM seam the hypothesis node calls: a rendered prompt in, a validated
    Hypothesis out (or a pydantic ValidationError if the output is malformed)."""

    def propose(self, prompt: str) -> Hypothesis: ...


class HypothesisConformanceError(RuntimeError):
    """The LLM could not produce a schema-valid Hypothesis within the retry cap.

    Carries the last pydantic ValidationError as its cause so the orchestrator
    (or a human) sees why every attempt was rejected, rather than a raw
    ValidationError leaking out of the node."""


class SpecCompilationError(RuntimeError):
    """The hypothesis's proposals were individually valid but did not compile to
    a coherent TriageSpec (e.g. duplicate constraint on one property, no
    constraint, or a require/exclude contradiction). Wraps compile_spec's raw
    pydantic ValidationError so the orchestrator / human gets an attributable
    error rather than an opaque validation dump."""


#: The nine workflow steps, in the fixed order they execute. The graph wires
#: exactly this linear chain; there are no branches or back-edges (it is a
#: pipeline, not an agentic loop).
WORKFLOW_STEPS: tuple[str, ...] = (
    "gate",
    # The LLM hypothesizes materials/properties of interest first; spec_build
    # then compiles those proposals into the TriageSpec (compile_spec consumes
    # the hypothesis, so hypothesis MUST precede spec_build).
    "hypothesis",
    "spec_build",
    "retrieve",
    "filter",
    "rank",
    "synthesis",
    "output_validate",
    "render",
)


class OrchestratorState(TypedDict, total=False):
    """The graph's shared state: one typed channel per step output.

    Per ADR 0003 the checkpointer persists exactly what flows through these
    channels, and the audit export is derived from that — so each channel holds
    the *rich* domain object (carrying provenance, missing-data flags, exclusion
    reasons, and citations), never a flattened summary. The container is a
    ``TypedDict`` (validation lives in the domain models themselves and at the
    one deliberate LLM-output retry seam, not implicitly on every channel write).

    ``total=False`` so a run can start from just ``goal``; downstream channels
    fill in as their steps execute.
    """

    goal: str
    run_id: str
    spec: TriageSpec | None
    hypothesis: Hypothesis | None
    candidates: tuple[Candidate, ...]
    survivors: tuple[Candidate, ...]
    # Exclusions are split by stage so each channel has a single writer (no
    # undercount, no read-then-write-the-same-channel resume hazard): the hard
    # filter writes `filter_excluded`, the ranker writes `rank_excluded`.
    filter_excluded: tuple[ExcludedCandidate, ...]
    rank_excluded: tuple[ExcludedCandidate, ...]
    # Loud, run-level notices, split by stage so each channel has a single writer
    # (the exporter unions them for presentation, like the two exclusion channels):
    # `retrieval_caveats` from the retrieve node (e.g. a page-capped, incomplete set);
    # `caveats` from the filter node (a hard predicate the source could neither push
    # nor return data for, ¬R∩¬Q).
    retrieval_caveats: tuple[str, ...]
    caveats: tuple[str, ...]
    result: TriageResult | None


def _passthrough(state: OrchestratorState) -> dict:
    """A skeleton node that contributes no state update yet. Backs the steps not
    wired so far — gate, synthesis, output_validate, render — which their own
    slices / tasks fill in (hypothesis and spec_build are now real nodes)."""
    return {}


def _hypothesis_prompt(goal: str, prior_error: str | None) -> str:
    """Render the prompt for the hypothesis step. Appends RANKING_TARGET_GUIDANCE so
    the LLM proposes ranking targets the agent's default geometric-mean ranker can
    score (each with explicit desirability ramp bounds) — the prose half of surfacing
    the schema, paired with the RankingTarget field descriptions. On a retry, the prior
    schema rejection is fed back so the model can correct the specific malformation."""
    prompt = (
        f"Propose a materials triage hypothesis for this goal: {goal}\n\n{RANKING_TARGET_GUIDANCE}"
    )
    if prior_error is not None:
        prompt += (
            "\n\nYour previous response was rejected because it did not conform "
            f"to the required schema:\n{prior_error}\nReturn a corrected response."
        )
    return prompt


def _make_hypothesis_node(
    provider: HypothesisProvider | None,
    max_attempts: int = DEFAULT_MAX_HYPOTHESIS_ATTEMPTS,
):
    """The hypothesis step: the LLM proposes the cited spec-bridges. The output is
    conformed in two ways, both retry-on-failure: structured output is only *shape*-
    checked by the schema (~15% of calls emit output it rejects), and a shape-valid
    hypothesis may still not *compile* into a coherent spec — e.g. a ranking target
    missing the ramp bounds the default geometric ranker requires, a duplicate
    constraint, or a contradiction. Both surface as a pydantic ValidationError, so
    this trial-compiles each candidate and retries either failure (feeding the reason
    back into the prompt) up to ``max_attempts``, then raises a wrapped
    HypothesisConformanceError. Catching the compile failure HERE — where the LLM can
    be re-prompted — is what stops it becoming a terminal, feedback-less
    SpecCompilationError later in spec_build. Non-validation failures (transport,
    throttling) are not retried here."""

    def hypothesis(state: OrchestratorState) -> dict:
        if provider is None:
            return {}
        last_exc: ValidationError | None = None
        for _ in range(max_attempts):
            prompt = _hypothesis_prompt(state["goal"], None if last_exc is None else str(last_exc))
            try:
                proposed = provider.propose(prompt)
                # Trial-compile: a shape-valid hypothesis whose proposals don't form a
                # coherent spec is retry-worthy too, so the LLM gets the reason back.
                compile_spec(proposed.proposals)
            except ValidationError as exc:
                last_exc = exc
                continue
            return {"hypothesis": proposed}
        raise HypothesisConformanceError(
            f"LLM did not produce a schema-valid, compilable Hypothesis in {max_attempts} attempts"
        ) from last_exc

    return hypothesis


def _spec_build_node(state: OrchestratorState) -> dict:
    """The spec-build step (human-in-the-loop): compile the hypothesis's proposals
    into the recommended TriageSpec, then PAUSE via interrupt() to let the human
    confirm or edit it — surfacing that the ranking weights were rescaled to sum
    to 1 (the weight-normalization confirmation). The human resumes with the
    approved TriageSpec (the recommendation echoed back to accept, or an edited
    one), which becomes the final spec.

    A run that already carries a resolved ``spec`` (provided directly, or a resume
    seeded with one) skips rebuilding; a run with no hypothesis has nothing to
    compile and is left alone.
    """
    if state.get("spec") is not None:
        return {}
    hypothesis = state.get("hypothesis")
    if hypothesis is None:
        return {}

    try:
        recommended = compile_spec(hypothesis.proposals)
    except ValidationError as exc:
        raise SpecCompilationError(
            "the hypothesis proposals did not compile to a coherent TriageSpec"
        ) from exc

    proposed_weights = [
        p.ranking_target.weight for p in hypothesis.proposals if p.kind == "ranking_target"
    ]
    weights_were_normalized = bool(proposed_weights) and not math.isclose(
        math.fsum(proposed_weights), 1.0, abs_tol=1e-9
    )

    note = (
        "Confirm the recommended spec; resume with the approved TriageSpec "
        "(echo to accept, or send an edited one)."
    )
    if weights_were_normalized:
        note = (
            "Ranking weights were rescaled to sum to 1. "
            "Confirm the recommended spec; resume with the approved TriageSpec "
            "(echo to accept, or send an edited one)."
        )
    approved_spec = interrupt(
        {
            "recommended_spec": recommended,
            "weights_were_normalized": weights_were_normalized,
            "note": note,
        }
    )
    # The resume value is the human's approved spec; guard the documented
    # "always a TriageSpec" contract so a bad resume surfaces as an attributable
    # error here, not an opaque AttributeError downstream in the filter node.
    if not isinstance(approved_spec, TriageSpec):
        raise SpecCompilationError(
            "the resumed spec-build decision must be a TriageSpec, "
            f"got {type(approved_spec).__name__}"
        )
    return {"spec": approved_spec}


def _make_retrieve_node(adapter: SourceAdapter | None):
    """The retrieve step: deterministic code, the pipeline's only source of
    ground-truth numbers. The adapter returns a ``RetrievalResult`` — candidates
    plus any I/O-level caveats (e.g. a page-capped, incomplete set) — which this
    node splits into the ``candidates`` channel and the single-writer
    ``retrieval_caveats`` channel (unioned with the filter stage's routing caveats
    at the trace boundary). With no adapter injected (e.g. a resume seeded with
    pre-retrieved candidates) it leaves both channels untouched."""

    def retrieve(state: OrchestratorState) -> dict:
        if adapter is None:
            return {}
        result = adapter.retrieve(state["spec"])
        return {
            "candidates": result.candidates,
            "retrieval_caveats": result.caveats,
        }

    return retrieve


def _make_filter_node(adapter: SourceAdapter | None):
    """The hard-filter step: partition retrieved candidates into survivors and the
    stage's structured exclusions. Numeric ``Constraint``s are checked by
    ``apply_hard_filters``; the source's *exclusive set* — predicates it could neither
    push nor express, routed via ``classify_predicates`` — is enforced by
    ``apply_local_filters``. Both drops land in the single ``filter_excluded`` channel.
    With no adapter (a resume seeded with candidates) only the numeric filter runs."""

    def filter_node(state: OrchestratorState) -> dict:
        spec = state["spec"]
        survivors, excluded = apply_hard_filters(
            list(state.get("candidates", ())), spec.constraints
        )
        routing = adapter.classify_predicates(spec) if adapter is not None else PredicateRouting()
        survivors, local_excluded = apply_local_filters(survivors, routing)
        return {
            "survivors": tuple(survivors),
            "filter_excluded": tuple(excluded) + tuple(local_excluded),
            "caveats": routing.caveats,
        }

    return filter_node


# The ranker chosen per run by `spec.ranking_method`: the compensatory weighted
# average, or the non-compensatory geometric mean of desirability curves.
_RANKERS = {
    "arithmetic_mean": rank_arithmetic_mean,
    "geometric_mean": rank_geometric_mean,
}


def _rank_node(state: OrchestratorState) -> dict:
    """The ranking step: rank the survivors by the method the spec records. The
    ranker's own missing-policy drops are recorded in the `rank_excluded` channel
    (the ranking stage's authoritative exclusions), and `result.excluded` is the
    union of both stages — the complete presentation set the renderers read."""
    spec = state["spec"]
    ranker = _RANKERS[spec.ranking_method]
    ranked = ranker(list(state.get("survivors", ())), spec.ranking_targets)
    union = tuple(state.get("filter_excluded", ())) + ranked.excluded
    return {
        "rank_excluded": ranked.excluded,
        "result": TriageResult(ranked=ranked.ranked, excluded=union),
    }


def build_orchestrator(
    adapter: SourceAdapter | None = None,
    provider: HypothesisProvider | None = None,
    checkpointer: MemorySaver | None = None,
) -> CompiledStateGraph:
    """Build and compile the triage orchestrator graph.

    The nine ``WORKFLOW_STEPS`` become nodes wired START -> gate -> ... ->
    render -> END, compiled with a checkpointer (v1 default: an in-process
    ``MemorySaver``) so execution state is captured for trace export and resume.
    The ``hypothesis`` step (LLM, retry-on-malformed) and the ``retrieve`` ->
    ``filter`` -> ``rank`` deterministic core are wired; the rest are
    pass-throughs until their slices land. ``adapter`` and ``provider`` are the
    injected retrieval and LLM seams (fakes make the whole graph offline-testable).
    """
    nodes = {
        "hypothesis": _make_hypothesis_node(provider),
        "spec_build": _spec_build_node,
        "retrieve": _make_retrieve_node(adapter),
        "filter": _make_filter_node(adapter),
        "rank": _rank_node,
    }
    builder = StateGraph(OrchestratorState)
    for step in WORKFLOW_STEPS:
        builder.add_node(step, nodes.get(step, _passthrough))
    builder.add_edge(START, WORKFLOW_STEPS[0])
    for earlier, later in zip(WORKFLOW_STEPS, WORKFLOW_STEPS[1:], strict=False):
        builder.add_edge(earlier, later)
    builder.add_edge(WORKFLOW_STEPS[-1], END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


def resume_run(orchestrator: CompiledStateGraph, config: dict) -> dict:
    """Resume a run that stopped on an infra error, reusing upstream work.

    Infra failures (anything that is not a pydantic ``ValidationError`` — a
    transport outage, throttling, a crash) are deliberately not retried in-node;
    they propagate and halt the run. The checkpointer leaves the run pending at
    the failed step (its ``.next``), with every upstream step's result already
    persisted. Resuming is simply continuing the same thread with ``None`` input:
    LangGraph re-runs the failed step onward and reads the upstream results from
    the checkpoint, so a recovered backend completes the run without re-paying
    for the LLM hypothesis call or re-querying already-retrieved data.

    (This is crash recovery, distinct from the HITL spec-gate resume, which
    answers an ``interrupt()`` with ``Command(resume=...)``.)
    """
    return orchestrator.invoke(None, config)
