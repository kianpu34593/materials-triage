"""The interactive REPL chat session: drive the triage workflow conversationally.

``run_chat`` is a read-eval loop — banner, prompt for a goal, stream the workflow
steps live, pause at the spec gate for the human to approve/edit/regenerate, then
print the rendered result and loop for the next goal. Every side channel (stdin,
stdout, the ``$EDITOR`` opener, the orchestrator seams) is injected, so the whole
session runs offline under test with fakes — no real terminal or editor.
"""

import os
import subprocess
import tempfile
from collections.abc import Callable
from typing import Literal, NamedTuple

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import ValidationError

from materials_triage.agent.orchestrator import InputRefused
from materials_triage.agent.prompts import DEFAULT_TOP_K
from materials_triage.core.run_trace import TriageRun, export_run
from materials_triage.core.schema import TriageSpec
from materials_triage.policy.guardrails import CAPABILITIES
from materials_triage.render import render_run

#: Signature of the editor seam: given the spec's current JSON text, return the
#: edited text. The default opens ``$EDITOR``; tests inject a pure function.
EditFn = Callable[[str], str]

#: Signature of the input seam: given a prompt, return the user's line. Mirrors the
#: builtin ``input``; tests inject a scripted function.
InputFn = Callable[[str], str]


class GateDecision(NamedTuple):
    """The outcome of one spec-gate interaction. ``action`` is one of ``approve``
    (run with ``spec``), ``regenerate`` (re-run hypothesis for a fresh proposal),
    or ``quit`` (abandon this query). ``spec`` carries the approved TriageSpec only
    when ``action == "approve"``; it is ``None`` otherwise."""

    action: str
    spec: TriageSpec | None


def _edit_spec(spec: TriageSpec, *, edit_fn: EditFn, out: Callable[[str], None]) -> TriageSpec:
    """Open the recommended spec as JSON in the editor seam and parse the result.

    The spec is serialized to indented JSON, handed to ``edit_fn``, and the edited
    text is re-validated into a TriageSpec. A malformed edit is non-fatal: the
    error is reported via ``out`` and the original spec is returned unchanged, so
    the gate re-displays it and the human can try again (no inner retry loop)."""
    initial = spec.model_dump_json(indent=2)
    edited_text = edit_fn(initial)
    try:
        return TriageSpec.model_validate_json(edited_text)
    except ValidationError as exc:
        out(f"Invalid spec, keeping the previous one:\n{exc}")
        return spec


_GATE_MENU = "[a]pprove / [e]dit / [r]egenerate / [q]uit"


def _spec_gate(
    payload: dict,
    *,
    input_fn: InputFn,
    out: Callable[[str], None],
    edit_fn: EditFn,
) -> GateDecision:
    """Run the human-in-the-loop spec gate over one interrupt ``payload``.

    Displays the recommended spec (as JSON) and the orchestrator's note, then loops
    on the ``[a]pprove / [e]dit / [r]egenerate / [q]uit`` menu: ``edit`` opens the
    spec in the editor seam and re-displays the (possibly changed) spec without
    leaving the gate; ``approve`` returns the current working spec; ``regenerate``
    and ``quit`` return immediately. Unrecognized input re-prompts."""
    spec: TriageSpec = payload["recommended_spec"]
    out(payload.get("note", ""))
    out(spec.model_dump_json(indent=2))
    while True:
        choice = input_fn(f"{_GATE_MENU}: ").strip().lower()
        if choice in ("a", "approve"):
            return GateDecision("approve", spec)
        if choice in ("r", "regenerate"):
            return GateDecision("regenerate", None)
        if choice in ("q", "quit"):
            return GateDecision("quit", None)
        if choice in ("e", "edit"):
            spec = _edit_spec(spec, edit_fn=edit_fn, out=out)
            out(spec.model_dump_json(indent=2))
            continue
        out(f"Unrecognized choice {choice!r}. Choose {_GATE_MENU}.")


def _run_query(
    orchestrator: CompiledStateGraph,
    goal: str,
    *,
    thread_id: str,
    input_fn: InputFn,
    out: Callable[[str], None],
    edit_fn: EditFn,
) -> TriageRun | None:
    """Run one goal through the workflow with live step streaming and the HITL gate.

    Streams the graph in ``updates`` mode, echoing each completed step to ``out``,
    until it pauses at the spec-build ``interrupt``; the human then drives
    ``_spec_gate``. ``approve`` resumes the same run and streams it to completion;
    ``regenerate`` starts a *fresh* run on a new thread-id (re-running the gate and
    hypothesis for a new LLM proposal) and returns to the gate; ``quit`` abandons
    the query (returns ``None``). A run that needs no human input (a pre-resolved
    spec, so no interrupt) streams straight through. Returns the exported TriageRun
    (or ``None`` on quit). Step exceptions (e.g. an input-gate refusal) propagate to
    the REPL, which keeps the session alive."""
    attempt = 0
    while True:
        tid = thread_id if attempt == 0 else f"{thread_id}r{attempt}"
        config = {"configurable": {"thread_id": tid}}
        payload = _stream(orchestrator, {"goal": goal, "run_id": tid}, config, out)
        if payload is None:  # finished without pausing — nothing to confirm
            return export_run(orchestrator, config)

        decision = _spec_gate(payload, input_fn=input_fn, out=out, edit_fn=edit_fn)
        if decision.action == "quit":
            return None
        if decision.action == "regenerate":
            attempt += 1
            out("Regenerating the spec…")
            continue
        _stream(orchestrator, Command(resume=decision.spec), config, out)
        return export_run(orchestrator, config)


def _summarize_step(node: str, delta: dict) -> str:
    """One-line, human-facing summary of a completed workflow step, drawn from the
    counts in its state ``delta`` — so the streamed trace shows what each step did,
    not just that it ran. Steps with no informative output (gate, output_validate,
    render) fall back to their bare name."""
    if node == "hypothesis" and delta.get("hypothesis") is not None:
        return f"hypothesis → {len(delta['hypothesis'].proposals)} proposals"
    if node == "spec_build" and delta.get("spec") is not None:
        return "spec_build → spec confirmed"
    if node == "retrieve":
        return f"retrieve → {len(delta.get('candidates', ()))} candidates retrieved"
    if node == "filter":
        survivors = len(delta.get("survivors", ()))
        excluded = len(delta.get("filter_excluded", ()))
        return f"filter → {survivors} survivors, {excluded} excluded"
    if node == "rank" and delta.get("result") is not None:
        result = delta["result"]
        return f"rank → {len(result.ranked)} ranked, {len(result.excluded)} excluded"
    if node == "synthesis":
        grounded = delta.get("synthesis") is not None
        return "synthesis → narrative grounded" if grounded else "synthesis → narrative omitted"
    return node


def _stream(
    orchestrator: CompiledStateGraph,
    graph_input,
    config: dict,
    out: Callable[[str], None],
) -> dict | None:
    """Stream one graph invocation in ``updates`` mode, echoing a per-step summary
    to ``out``. Returns the interrupt payload if the run paused at the spec gate, or
    ``None`` if it ran to the end. ``graph_input`` is the initial state for a fresh
    run or a ``Command(resume=...)`` to continue a paused one."""
    for chunk in orchestrator.stream(graph_input, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            return chunk["__interrupt__"][0].value
        for node, delta in chunk.items():
            # A node that contributes no state update streams as a None delta.
            out(f"  ✓ {_summarize_step(node, delta or {})}")
    return None


def _open_editor(initial_text: str) -> str:
    """The default editor seam: write ``initial_text`` to a temp file, open it in
    ``$EDITOR`` (falling back to ``vi``), and return the saved contents. Live only —
    tests inject a pure ``edit_fn`` instead of spawning an editor."""
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        handle.write(initial_text)
        path = handle.name
    try:
        subprocess.run([editor, path], check=True)  # noqa: S603 — $EDITOR is the user's own
        with open(path) as handle:
            return handle.read()
    finally:
        os.unlink(path)


_BANNER = (
    "materials-triage — interactive session\n"
    "Type a research goal to triage; 'exit' or Ctrl-D to quit.\n"
)


def run_chat(
    orchestrator: CompiledStateGraph,
    *,
    input_fn: InputFn = input,
    out: Callable[[str], None] = print,
    edit_fn: EditFn | None = None,
    view: Literal["pi", "audit"] = "pi",
    top_k: int = DEFAULT_TOP_K,
) -> None:
    """The interactive REPL: banner, then loop reading a goal, streaming its workflow
    steps, pausing at the spec gate, and rendering the result.

    Each goal runs on its own thread-id (``chat-1``, ``chat-2``, …). After a run, the
    result is rendered in ``view`` (concise ``pi`` by default) and — unless already in
    ``audit`` — the user is offered the full audit trace. ``exit``/``quit``/EOF ends
    the session; a blank line re-prompts. An input-gate refusal prints the capabilities
    redirect and any other per-query error is reported, both keeping the session alive
    (one bad goal never kills the REPL)."""
    edit_fn = edit_fn or _open_editor
    out(_BANNER)
    counter = 0
    while True:
        try:
            goal = input_fn("triage> ").strip()
        except EOFError:
            break
        if goal.lower() in ("exit", "quit"):
            break
        if not goal:
            continue
        counter += 1
        try:
            run = _run_query(
                orchestrator,
                goal,
                thread_id=f"chat-{counter}",
                input_fn=input_fn,
                out=out,
                edit_fn=edit_fn,
            )
        except InputRefused as exc:
            out(exc.reason if CAPABILITIES in exc.reason else f"{exc.reason} {CAPABILITIES}")
            continue
        except Exception as exc:  # noqa: BLE001 — one query's failure must not kill the REPL
            out(f"error: {exc}")
            continue
        if run is None:
            out("Query cancelled.")
            continue
        out(render_run(run, view=view, top_k=top_k))
        if view != "audit":
            choice = input_fn("Show full audit trace? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                out(render_run(run, view="audit", top_k=top_k))
