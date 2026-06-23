"""A throwaway local web GUI over the Materials-Triage pipeline.

A single-page FastAPI app: ``GET /`` serves the request form, ``POST /triage``
runs one request end-to-end through the live pipeline (Bedrock + Materials
Project, wired exactly like ``cli.main()``) and renders the result back into the
page. This is a v0 demo front door for the ``feat/fast-track-wire-guardrails``
branch — not production hosting. Launch with::

    uvicorn mt_server.app:app --reload --app-dir server
"""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Materials-Triage (local GUI)")


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
    return _TEMPLATES.TemplateResponse(request, "index.html", {})


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
