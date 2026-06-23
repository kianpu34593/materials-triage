"""A throwaway local web GUI over the Materials-Triage pipeline.

A single-page FastAPI app over the live pipeline (Bedrock + Materials Project,
wired like ``cli.main()``). ``GET /`` serves the form; ``GET /triage/stream``
runs a request and streams live per-step progress as Server-Sent Events,
*pausing* at the spec gate so the human can approve / edit / regenerate the
compiled spec; ``GET /triage/resume`` continues the parked run with the approved
spec. ``POST /triage`` is a no-JS fallback that auto-accepts the spec gate and
renders in one shot. This is a v0 demo front door for the
``feat/fast-track-wire-guardrails`` branch — not production hosting. Launch with::

    uvicorn mt_server.app:app --reload --app-dir server
"""

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from langgraph.types import Command

from materials_triage.agent.orchestrator import (
    WORKFLOW_STEPS,
    InputRefused,
    build_orchestrator,
)
from materials_triage.cli import render_run
from materials_triage.core.run_trace import export_run
from materials_triage.core.schema import TriageSpec

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Human-readable label per workflow node, for the live progress checklist.
STEP_LABELS = {
    "gate": "Input policy gate",
    "hypothesis": "Hypothesis",
    "spec_build": "Spec building",
    "retrieve": "Retrieve",
    "filter": "Hard filters",
    "rank": "Ranking",
    "synthesis": "Synthesis",
    "output_validate": "Output validation",
    "render": "Render",
}
#: The checklist the page renders up front (name + label, in execution order).
STEP_VIEW = [{"name": s, "label": STEP_LABELS.get(s, s)} for s in WORKFLOW_STEPS]

#: Runs paused at the spec gate, keyed by thread_id, so a later /triage/resume
#: request can continue the same checkpointed run. In-process only — fine for a
#: single-process local demo; entries are dropped when the run finishes.
_RUNS: dict[str, object] = {}

app = FastAPI(title="Materials-Triage (local GUI)")


def _sse(payload: dict) -> str:
    """Encode one payload as a Server-Sent Events ``data:`` frame."""
    return f"data: {json.dumps(payload)}\n\n"


def build_seams():
    """Wire the live pipeline seams (Bedrock + Materials Project), exactly as
    ``cli.main()`` does. Imports are lazy so merely importing this module — or
    serving ``GET /`` — never needs credentials or the heavy ``llm`` extra."""
    from materials_triage.agent.llm import (
        HypothesisProvider,
        QueryProvider,
        RankingCriticProvider,
        SynthesisProvider,
    )
    from materials_triage.retrieval.rag import LiteratureRAG, OpenAlexFetcher
    from materials_triage.sources.materials_project import MaterialsProjectAdapter

    return {
        "adapter": MaterialsProjectAdapter(),
        "provider": HypothesisProvider(),
        "synthesis_provider": SynthesisProvider(),
        "rag": LiteratureRAG(OpenAlexFetcher()),
        "query_provider": QueryProvider(),
        "ranking_critic": RankingCriticProvider(),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Serve the empty request form."""
    return _TEMPLATES.TemplateResponse(request, "index.html", {"steps_json": json.dumps(STEP_VIEW)})


@app.post("/triage", response_class=HTMLResponse)
def post_triage(
    request: Request,
    goal: str = Form(...),
    view: str = Form("pi"),
) -> HTMLResponse:
    """Run one request end-to-end through the live pipeline and render the result.

    Re-renders the page with the goal/view preserved plus the rendered ``view``
    markdown. The spec human-in-the-loop gate is auto-accepted inside ``triage``,
    so this completes in one call. A refused goal shows a refusal banner; any
    other failure (missing credentials, transport error) shows an error banner —
    never a bare 500."""
    from materials_triage.agent.orchestrator import InputRefused
    from materials_triage.cli import render_run, triage

    context = {"goal": goal, "view": view}
    try:
        run = triage(goal, **build_seams())
        context["result_md"] = render_run(run, view=view)
    except InputRefused as refused:
        context["refusal"] = {
            "category": refused.decision.category,
            "reason": refused.decision.reason,
        }
    except Exception as exc:  # noqa: BLE001 — surface any failure as a banner, not a 500
        context["error"] = f"{type(exc).__name__}: {exc}"
    return _TEMPLATES.TemplateResponse(request, "index.html", context)


def _drive(orchestrator, config, next_input, view, thread_id):
    """Stream one pass of the graph, yielding a ``step`` frame per completed node.

    Pauses at the spec gate — parks the (checkpointed) orchestrator in ``_RUNS``
    and yields a ``spec_gate`` frame carrying the compiled spec — so the human can
    approve / edit / regenerate via ``/triage/resume``. With no interrupt it runs
    to completion and yields the terminal ``done`` frame. Shared by the initial
    run and every resume."""
    gate = None
    for chunk in orchestrator.stream(next_input, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            gate = chunk["__interrupt__"][0].value
            break
        for node in chunk:
            if node in WORKFLOW_STEPS:
                yield _sse({"type": "step", "name": node})
                # When the hypothesis node completes, surface its goal -> query ->
                # RAG -> passages -> prompt -> proposals interaction live.
                trace = chunk[node].get("rag_trace") if isinstance(chunk[node], dict) else None
                if trace:
                    yield _sse({"type": "rag_trace", "trace": trace})

    if gate is not None:
        _RUNS[thread_id] = orchestrator
        yield _sse(
            {
                "type": "spec_gate",
                "thread_id": thread_id,
                "note": gate.get("note", ""),
                "weights_were_normalized": gate.get("weights_were_normalized", False),
                "fidelity": gate.get("fidelity_findings", []),
                "spec": gate["recommended_spec"].model_dump(mode="json"),
            }
        )
        return  # pause: the resume request continues this run

    run = export_run(orchestrator, config)
    _RUNS.pop(thread_id, None)
    yield _sse({"type": "done", "view": view, "result": render_run(run, view=view)})


def _terminal_error(exc: Exception):
    """Map an exception raised mid-stream to its terminal SSE frame."""
    if isinstance(exc, InputRefused):
        return _sse(
            {"type": "refused", "category": exc.decision.category, "reason": exc.decision.reason}
        )
    return _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


def _triage_events(goal: str, view: str):
    """Start a fresh run and stream it until the spec gate (or completion)."""
    try:
        orchestrator = build_orchestrator(**build_seams())
        thread_id = uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id}}
        start = {"goal": goal, "run_id": thread_id}
        yield from _drive(orchestrator, config, start, view, thread_id)
    except Exception as exc:  # noqa: BLE001 — stream the failure, don't drop the connection
        yield _terminal_error(exc)


def _resume_events(thread_id: str, spec_json: str, view: str):
    """Resume a gated run with the human's approved (possibly edited) spec.

    Validates the edited JSON back into a ``TriageSpec`` and resumes the parked
    run from the spec gate. A missing run or invalid spec yields an ``error``
    frame; the parked run is left intact so the edit can be retried."""
    orchestrator = _RUNS.get(thread_id)
    if orchestrator is None:
        yield _sse({"type": "error", "message": "This run is no longer available; start again."})
        return
    try:
        spec = TriageSpec.model_validate_json(spec_json)
    except Exception as exc:  # noqa: BLE001 — a bad edit is the human's, surface it for retry
        yield _sse({"type": "error", "message": f"Edited spec is invalid: {exc}", "retry": True})
        return
    try:
        config = {"configurable": {"thread_id": thread_id}}
        yield from _drive(orchestrator, config, Command(resume=spec), view, thread_id)
    except Exception as exc:  # noqa: BLE001 — stream the failure, don't drop the connection
        yield _terminal_error(exc)


@app.get("/triage/stream")
def triage_stream(goal: str, view: str = "pi") -> StreamingResponse:
    """Stream a fresh run's live step progress as Server-Sent Events."""
    return StreamingResponse(_triage_events(goal, view), media_type="text/event-stream")


@app.get("/triage/resume")
def triage_resume(thread_id: str, spec: str, view: str = "pi") -> StreamingResponse:
    """Resume a gated run with the approved spec and stream the rest as SSE."""
    return StreamingResponse(_resume_events(thread_id, spec, view), media_type="text/event-stream")
