"""A throwaway local web GUI over the Materials-Triage pipeline.

A single-page FastAPI app: ``GET /`` serves the request form, ``POST /triage``
runs one request end-to-end through the live pipeline (Bedrock + Materials
Project, wired exactly like ``cli.main()``) and renders the result back into the
page. This is a v0 demo front door for the ``feat/fast-track-wire-guardrails``
branch — not production hosting. Launch with::

    uvicorn mt_server.app:app --reload --app-dir server
"""

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from materials_triage.agent.orchestrator import WORKFLOW_STEPS

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

app = FastAPI(title="Materials-Triage (local GUI)")


def _sse(payload: dict) -> str:
    """Encode one payload as a Server-Sent Events ``data:`` frame."""
    return f"data: {json.dumps(payload)}\n\n"


def build_seams():
    """Wire the live pipeline seams (Bedrock + Materials Project), exactly as
    ``cli.main()`` does. Imports are lazy so merely importing this module — or
    serving ``GET /`` — never needs credentials or the heavy ``llm`` extra."""
    from materials_triage.agent.llm import HypothesisProvider, SynthesisProvider
    from materials_triage.sources.materials_project import MaterialsProjectAdapter

    return {
        "adapter": MaterialsProjectAdapter(),
        "provider": HypothesisProvider(),
        "synthesis_provider": SynthesisProvider(),
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


def _triage_events(goal: str, view: str):
    """Drive one run via ``orchestrator.stream`` and yield SSE frames: one
    ``step`` per completed node, the auto-accepted spec interrupt resumed inline,
    then a terminal ``done`` (rendered result), ``refused``, or ``error`` frame."""
    from langgraph.types import Command

    from materials_triage.agent.orchestrator import InputRefused, build_orchestrator
    from materials_triage.cli import render_run
    from materials_triage.core.run_trace import export_run

    try:
        orchestrator = build_orchestrator(**build_seams())
        thread_id = uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id}}

        next_input = {"goal": goal, "run_id": thread_id}
        while next_input is not None:
            resumed = None
            for chunk in orchestrator.stream(next_input, config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    recommended = chunk["__interrupt__"][0].value["recommended_spec"]
                    resumed = Command(resume=recommended)  # auto-accept the spec gate
                    break
                for node in chunk:
                    if node in WORKFLOW_STEPS:
                        yield _sse({"type": "step", "name": node})
            next_input = resumed

        run = export_run(orchestrator, config)
        yield _sse({"type": "done", "view": view, "result": render_run(run, view=view)})
    except InputRefused as refused:
        yield _sse(
            {
                "type": "refused",
                "category": refused.decision.category,
                "reason": refused.decision.reason,
            }
        )
    except Exception as exc:  # noqa: BLE001 — stream the failure, don't drop the connection
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


@app.get("/triage/stream")
def triage_stream(goal: str, view: str = "pi") -> StreamingResponse:
    """Stream one run's live step progress + final result as Server-Sent Events."""
    return StreamingResponse(_triage_events(goal, view), media_type="text/event-stream")
