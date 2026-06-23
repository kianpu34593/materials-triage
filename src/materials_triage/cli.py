"""The command-line entry point — the single chat-style front door.

``run_triage`` drives one request through the whole orchestrator (gate ->
hypothesis -> spec -> retrieve -> filter -> rank -> synthesis -> validate) and
returns the rendered view; the spec human-in-the-loop gate is auto-accepted
(echo the recommended spec) so a one-shot CLI run completes unattended. ``main``
wires the real Bedrock + Materials Project seams, loads ``.env``, and prints the
PI or audit view. The seams are injected into ``run_triage`` so the whole
pipeline is exercisable offline with fakes.
"""

import argparse
import sys

from langgraph.types import Command

from materials_triage.agent.orchestrator import InputRefused, build_orchestrator
from materials_triage.core.run_trace import export_run, write_run
from materials_triage.core.schema import TriageResult
from materials_triage.render import render_audit, render_pi


def triage(
    goal,
    *,
    adapter,
    provider,
    synthesis_provider,
    rag=None,
    query_provider=None,
    ranking_critic=None,
    runs_dir=None,
    thread_id="cli",
):
    """Run one triage request end-to-end and return its :class:`TriageRun`.

    Drives the whole graph once: the spec gate's ``interrupt()`` is answered by
    echoing the recommended spec back (auto-accept), so the run completes in one
    call. When ``runs_dir`` is given, the derived audit trace is persisted to
    ``<runs_dir>/<run_id>.json``. Both views render from this single run, so they
    can never disagree. Raises :class:`InputRefused` if the goal is refused.
    """
    orchestrator = build_orchestrator(
        adapter=adapter,
        provider=provider,
        synthesis_provider=synthesis_provider,
        rag=rag,
        query_provider=query_provider,
        ranking_critic=ranking_critic,
    )
    config = {"configurable": {"thread_id": thread_id}}
    state = orchestrator.invoke({"goal": goal, "run_id": thread_id}, config)

    interrupts = state.get("__interrupt__")
    if interrupts:
        recommended = interrupts[0].value["recommended_spec"]
        orchestrator.invoke(Command(resume=recommended), config)

    run = export_run(orchestrator, config)
    if runs_dir is not None:
        write_run(run, runs_dir)
    return run


def render_run(run, view="pi") -> str:
    """Render a completed :class:`TriageRun` as the ``pi`` or ``audit`` view."""
    if view == "audit":
        return render_audit(run)
    return render_pi(run.result or TriageResult(), run.synthesis)


def run_triage(goal, *, view="pi", **kwargs) -> str:
    """Run one request and return the rendered ``view`` (convenience wrapper over
    :func:`triage` + :func:`render_run`)."""
    return render_run(triage(goal, **kwargs), view)


def main(argv=None) -> int:
    """Parse args, wire the real seams, run one request, and print the view."""
    parser = argparse.ArgumentParser(
        prog="materials-triage",
        description="Turn a materials request into a ranked, cited shortlist (public data only).",
    )
    parser.add_argument("goal", help="the materials-triage request, in natural language")
    parser.add_argument(
        "--view", choices=("pi", "audit"), default="pi", help="output view (default: pi)"
    )
    parser.add_argument(
        "--runs-dir", default=None, help="directory to persist the audit trace JSON"
    )
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # Lazy: importing these builds nothing that needs credentials until invoked.
    from materials_triage.agent.llm import (
        HypothesisProvider,
        QueryProvider,
        RankingCriticProvider,
        SynthesisProvider,
    )
    from materials_triage.retrieval.rag import LiteratureRAG, OpenAlexFetcher
    from materials_triage.sources.materials_project import MaterialsProjectAdapter

    try:
        output = run_triage(
            args.goal,
            adapter=MaterialsProjectAdapter(),
            provider=HypothesisProvider(),
            synthesis_provider=SynthesisProvider(),
            rag=LiteratureRAG(OpenAlexFetcher()),
            query_provider=QueryProvider(),
            ranking_critic=RankingCriticProvider(),
            view=args.view,
            runs_dir=args.runs_dir,
        )
    except InputRefused as refused:
        print(f"Refused [{refused.decision.category}]: {refused.decision.reason}", file=sys.stderr)
        return 2

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
