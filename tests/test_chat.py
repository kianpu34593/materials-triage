"""Tests for the interactive REPL chat session in materials_triage.chat.

The chat layer is a thin, fully-injected presentation/REPL surface over the
orchestrator: the spec gate, the per-step streaming, and the read-eval loop all
take their I/O (input, output, the $EDITOR opener) as seams, so the whole
session runs offline with fakes — no real terminal or editor.
"""

import json

from materials_triage.agent.orchestrator import build_orchestrator
from materials_triage.chat import _edit_spec, _run_query, _spec_gate
from materials_triage.core.hypothesis import (
    ConstraintProposal,
    Hypothesis,
    RankingProposal,
)
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RankingTarget,
    RetrievalResult,
    TriageSpec,
)
from materials_triage.policy.guardrails import CAPABILITIES as CAPABILITIES_TEXT
from materials_triage.sources.base import SourceAdapter


def _gate_payload(spec: TriageSpec, *, normalized: bool = False) -> dict:
    return {
        "recommended_spec": spec,
        "weights_were_normalized": normalized,
        "note": "Confirm the recommended spec.",
    }


def _scripted(responses: list[str]):
    """An input seam that returns each scripted response in turn."""
    it = iter(responses)
    return lambda _prompt: next(it)


def _valid_hypothesis() -> Hypothesis:
    """A shape-valid, compilable hypothesis: one band-gap floor + a bounded
    maximize ranking target (so it compiles under the geometric default)."""
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
                    weight=1.0,
                    lower=1.0,
                    target=3.0,
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
        ),
        mechanism="stub",
    )


class _StubProvider:
    """An offline hypothesis seam: returns the same compilable hypothesis every
    call, counting calls so a test can confirm regeneration re-ran it."""

    def __init__(self):
        self.calls = 0

    def propose(self, prompt):
        self.calls += 1
        return _valid_hypothesis()

    def extract_keywords(self, goal):
        return goal


def _candidate(identifier: str, band_gap: float) -> Candidate:
    provenance = Provenance(
        source="Materials Project", record_id=identifier, method="computational"
    )
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=provenance)},
    )


class _FakeAdapter(SourceAdapter):
    """An offline retrieval seam: returns a fixed candidate list, ignoring the spec."""

    def __init__(self, candidates):
        self._candidates = tuple(candidates)

    def retrieve(self, spec):
        return RetrievalResult(candidates=self._candidates, caveats=())

    def classify_predicates(self, spec):
        from materials_triage.core.schema import PredicateRouting

        return PredicateRouting()

    def property_vocabulary(self):
        return {}


def test_edit_spec_returns_edited_spec():
    """The edit seam receives the recommended spec as JSON and its edited text is
    parsed back into a TriageSpec — the user's change is reflected in the result."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    def edit_fn(initial_text: str) -> str:
        data = json.loads(initial_text)
        data["constraints"][0]["min"] = 5.0
        return json.dumps(data)

    edited = _edit_spec(spec, edit_fn=edit_fn, out=lambda _msg: None)

    assert isinstance(edited, TriageSpec)
    assert edited.constraints[0].min == 5.0


def test_edit_spec_keeps_original_on_invalid_edit():
    """A malformed edit is non-fatal: the original spec is returned unchanged and
    the error is surfaced via the output seam (the gate re-prompts the human)."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    messages: list[str] = []

    def edit_fn(_initial_text: str) -> str:
        return "{not valid json"

    result = _edit_spec(spec, edit_fn=edit_fn, out=messages.append)

    assert result is spec
    assert messages and "Invalid spec" in messages[0]


def test_spec_gate_approve_returns_recommended_spec():
    """Choosing approve returns an 'approve' decision carrying the recommended spec."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    decision = _spec_gate(
        _gate_payload(spec),
        input_fn=_scripted(["a"]),
        out=lambda _m: None,
        edit_fn=lambda t: t,
    )
    assert decision.action == "approve"
    assert decision.spec is spec


def test_spec_gate_edit_then_approve():
    """Edit opens the editor seam, stays in the gate, and a subsequent approve
    returns the edited spec."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))

    def edit_fn(initial_text: str) -> str:
        data = json.loads(initial_text)
        data["constraints"][0]["min"] = 4.0
        return json.dumps(data)

    decision = _spec_gate(
        _gate_payload(spec),
        input_fn=_scripted(["e", "a"]),
        out=lambda _m: None,
        edit_fn=edit_fn,
    )
    assert decision.action == "approve"
    assert decision.spec.constraints[0].min == 4.0


def test_spec_gate_regenerate():
    """Choosing regenerate returns a 'regenerate' decision with no spec."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    decision = _spec_gate(
        _gate_payload(spec),
        input_fn=_scripted(["r"]),
        out=lambda _m: None,
        edit_fn=lambda t: t,
    )
    assert decision.action == "regenerate"
    assert decision.spec is None


def test_spec_gate_quit():
    """Choosing quit returns a 'quit' decision with no spec."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    decision = _spec_gate(
        _gate_payload(spec),
        input_fn=_scripted(["q"]),
        out=lambda _m: None,
        edit_fn=lambda t: t,
    )
    assert decision.action == "quit"
    assert decision.spec is None


def test_spec_gate_reprompts_on_unrecognized_choice():
    """An unrecognized choice re-prompts rather than terminating the gate."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=2.0),))
    messages: list[str] = []
    decision = _spec_gate(
        _gate_payload(spec),
        input_fn=_scripted(["huh?", "a"]),
        out=messages.append,
        edit_fn=lambda t: t,
    )
    assert decision.action == "approve"
    assert any("Unrecognized" in m for m in messages)


def test_run_query_approve_streams_steps_and_returns_run():
    """Approving at the gate runs the workflow end to end and returns the exported
    TriageRun; the step updates are surfaced to the output seam as they stream."""
    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0), _candidate("mp-low", 3.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)
    lines: list[str] = []

    run = _run_query(
        orchestrator,
        "wide-gap oxide dielectric",
        thread_id="q1",
        input_fn=_scripted(["a"]),
        out=lines.append,
        edit_fn=lambda t: t,
    )

    assert run is not None
    assert run.spec.constraints[0].property_name == "band_gap"
    assert [sc.candidate.identifier for sc in run.result.ranked] == ["mp-high", "mp-low"]
    # The streamed step names reached the output seam.
    joined = "\n".join(lines)
    assert "hypothesis" in joined and "rank" in joined


def test_run_query_quit_returns_none():
    """Quitting at the gate abandons the query and returns None (no run)."""
    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)

    run = _run_query(
        orchestrator,
        "wide-gap oxide",
        thread_id="q2",
        input_fn=_scripted(["q"]),
        out=lambda _m: None,
        edit_fn=lambda t: t,
    )
    assert run is None


def test_run_query_regenerate_reruns_hypothesis_then_approves():
    """Regenerate starts a fresh run (re-invoking the hypothesis seam) and lands
    back at the gate; a subsequent approve completes the (regenerated) run."""
    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)

    run = _run_query(
        orchestrator,
        "wide-gap oxide",
        thread_id="q3",
        input_fn=_scripted(["r", "a"]),
        out=lambda _m: None,
        edit_fn=lambda t: t,
    )
    assert run is not None
    assert provider.calls == 2  # initial proposal + one regeneration


def test_summarize_step_reports_counts_per_node():
    """Each streamed step is summarized with a count drawn from its state delta, so
    the user sees what happened (not just the bare step name)."""
    from materials_triage.chat import _summarize_step

    hyp = {"hypothesis": _valid_hypothesis()}
    assert "2 proposals" in _summarize_step("hypothesis", hyp)

    retr = {"candidates": (_candidate("a", 1.0), _candidate("b", 2.0), _candidate("c", 3.0))}
    retrieve_summary = _summarize_step("retrieve", retr)
    assert "3" in retrieve_summary and "candidate" in retrieve_summary

    filt = {"survivors": (_candidate("a", 1.0),), "filter_excluded": (object(), object())}
    summary = _summarize_step("filter", filt)
    assert "1" in summary and "2" in summary

    # An unrecognized / output-free step falls back to its name.
    assert "gate" in _summarize_step("gate", {})


def test_run_chat_runs_a_query_then_exits():
    """A full REPL turn: prompt for a goal, approve at the gate, render the concise
    result, decline the full trace, then exit. The PI result reaches output; the
    audit-only trace does not (it was declined)."""
    from materials_triage.chat import run_chat

    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0), _candidate("mp-low", 3.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)
    lines: list[str] = []

    run_chat(
        orchestrator,
        input_fn=_scripted(["wide-gap oxide dielectric", "a", "n", "exit"]),
        out=lines.append,
        edit_fn=lambda t: t,
    )

    joined = "\n".join(lines)
    assert "mp-high" in joined  # the ranked shortlist was rendered
    assert "Run:" not in joined  # the full audit trace was declined


def test_run_chat_shows_full_audit_on_request():
    """After the concise result, choosing 'y' renders the full audit trace too."""
    from materials_triage.chat import run_chat

    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)
    lines: list[str] = []

    run_chat(
        orchestrator,
        input_fn=_scripted(["wide-gap oxide", "a", "y", "exit"]),
        out=lines.append,
        edit_fn=lambda t: t,
    )

    joined = "\n".join(lines)
    assert "Run:" in joined and "Trace:" in joined  # audit-only sections rendered


def test_run_chat_keeps_session_alive_after_refused_goal():
    """An out-of-scope goal is refused with the capabilities redirect, and the REPL
    keeps running so the next goal still works."""
    from materials_triage.chat import run_chat

    provider = _StubProvider()
    adapter = _FakeAdapter([_candidate("mp-high", 4.0)])
    orchestrator = build_orchestrator(provider=provider, adapter=adapter)
    lines: list[str] = []

    run_chat(
        orchestrator,
        input_fn=_scripted(["hello there friend", "wide-gap oxide", "a", "n", "exit"]),
        out=lines.append,
        edit_fn=lambda t: t,
    )

    joined = "\n".join(lines)
    assert CAPABILITIES_TEXT in joined  # the refusal redirect was shown
    assert "mp-high" in joined  # the session survived and ran the next goal


def test_run_chat_exits_on_eof():
    """Ctrl-D (EOFError from the input seam) ends the session cleanly."""
    from materials_triage.chat import run_chat

    def eof_input(_prompt):
        raise EOFError

    orchestrator = build_orchestrator(provider=_StubProvider(), adapter=_FakeAdapter([]))
    lines: list[str] = []
    run_chat(orchestrator, input_fn=eof_input, out=lines.append, edit_fn=lambda t: t)
    # Returns without error; the banner was printed.
    assert any("interactive session" in m for m in lines)
