"""The triage orchestrator: the nine-step workflow as a compiled LangGraph.

Per ADR 0003 the workflow is a deterministic, linear, *traced* state machine —
not an autonomous tool-calling loop. The steps are graph nodes wired in a fixed
linear edge order and compiled with a checkpointer (the substrate for the #9
trace export and `resume --from`). This module currently builds the skeleton:
the steps are pass-through nodes that later slices replace with the real spec,
hypothesis, retrieval, filter, rank, synthesis, validation, and render logic.
"""

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from materials_triage.core.hypothesis import Hypothesis
from materials_triage.core.ranking import rank
from materials_triage.core.schema import (
    Candidate,
    ExcludedCandidate,
    TriageResult,
    TriageSpec,
)
from materials_triage.core.scoring import apply_hard_filters
from materials_triage.sources.base import SourceAdapter

#: The nine workflow steps, in the fixed order they execute. The graph wires
#: exactly this linear chain; there are no branches or back-edges (it is a
#: pipeline, not an agentic loop).
WORKFLOW_STEPS: tuple[str, ...] = (
    "gate",
    "spec_build",
    "hypothesis",
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
    result: TriageResult | None


def _passthrough(state: OrchestratorState) -> dict:
    """A skeleton node that contributes no state update yet (gate, spec_build,
    hypothesis, synthesis, output_validate, render are filled in by later
    slices / tasks)."""
    return {}


def _make_retrieve_node(adapter: SourceAdapter | None):
    """The retrieve step: deterministic code, the pipeline's only source of
    ground-truth numbers. With no adapter injected (e.g. a resume seeded with
    pre-retrieved candidates) it leaves the ``candidates`` channel untouched."""

    def retrieve(state: OrchestratorState) -> dict:
        if adapter is None:
            return {}
        return {"candidates": tuple(adapter.retrieve(state["spec"]))}

    return retrieve


def _filter_node(state: OrchestratorState) -> dict:
    """The hard-filter step: partition retrieved candidates into survivors and
    the stage's own structured exclusions against the spec's constraints."""
    survivors, excluded = apply_hard_filters(
        list(state.get("candidates", ())), state["spec"].constraints
    )
    return {"survivors": tuple(survivors), "filter_excluded": tuple(excluded)}


def _rank_node(state: OrchestratorState) -> dict:
    """The ranking step: weighted-average rank the survivors. The ranker's own
    missing-policy drops are recorded in the `rank_excluded` channel (the ranking
    stage's authoritative exclusions), and `result.excluded` is the union of both
    stages — the complete presentation set the renderers read."""
    ranked = rank(list(state.get("survivors", ())), state["spec"].ranking_targets)
    union = tuple(state.get("filter_excluded", ())) + ranked.excluded
    return {
        "rank_excluded": ranked.excluded,
        "result": TriageResult(ranked=ranked.ranked, excluded=union),
    }


def build_orchestrator(
    adapter: SourceAdapter | None = None,
    checkpointer: MemorySaver | None = None,
) -> CompiledStateGraph:
    """Build and compile the triage orchestrator graph.

    The nine ``WORKFLOW_STEPS`` become nodes wired START -> gate -> ... ->
    render -> END, compiled with a checkpointer (v1 default: an in-process
    ``MemorySaver``) so execution state is captured for trace export and resume.
    The ``retrieve`` -> ``filter`` -> ``rank`` steps run the deterministic core;
    the rest are pass-throughs until their slices land. ``adapter`` is the
    injected retrieval seam (a fake makes the whole graph offline-testable).
    """
    nodes = {
        "retrieve": _make_retrieve_node(adapter),
        "filter": _filter_node,
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
