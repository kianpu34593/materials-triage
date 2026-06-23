"""The command-line entry point: run one triage goal end to end.

``triage`` wires the orchestrator for a single goal, auto-accepts the spec-build
human-in-the-loop interrupt (a non-interactive run takes the recommended spec),
and returns the exported :class:`~materials_triage.core.run_trace.TriageRun`. The
retrieval, LLM, and RAG seams are injected so the driver is fully offline-testable;
``main`` wires the real Bedrock / Materials Project / OpenAlex seams.
"""

import argparse
import os
import sys

from langgraph.types import Command

from materials_triage.agent.orchestrator import (
    HypothesisProvider,
    LiteratureRetriever,
    SynthesisProvider,
    build_orchestrator,
)
from materials_triage.agent.prompts import DEFAULT_TOP_K
from materials_triage.core.run_trace import TriageRun, export_run, write_run
from materials_triage.render import render_run
from materials_triage.sources.base import SourceAdapter


def triage(
    goal: str,
    *,
    adapter: SourceAdapter | None = None,
    provider: HypothesisProvider | None = None,
    synthesis_provider: SynthesisProvider | None = None,
    rag: LiteratureRetriever | None = None,
    top_k: int = DEFAULT_TOP_K,
    runs_dir: str | None = None,
    thread_id: str = "cli",
) -> TriageRun:
    """Run the triage workflow for ``goal`` and return the exported TriageRun.

    Drives the orchestrator on a single thread; if the run pauses at the spec-build
    interrupt (no spec supplied), it auto-accepts the recommended spec — a
    non-interactive CLI run has no human to confirm, so the agent's recommendation
    stands. ``top_k`` caps the citable shortlist the synthesis step sees. The completed
    run is exported to a TriageRun (which keeps the FULL ranking) and, when ``runs_dir``
    is given, persisted as ``runs_dir/<run_id>.json``.
    """
    orchestrator = build_orchestrator(
        adapter=adapter,
        provider=provider,
        rag=rag,
        synthesis_provider=synthesis_provider,
        top_k=top_k,
    )
    config = {"configurable": {"thread_id": thread_id}}
    result = orchestrator.invoke({"goal": goal, "run_id": thread_id}, config)
    if "__interrupt__" in result:
        recommended = result["__interrupt__"][0].value["recommended_spec"]
        orchestrator.invoke(Command(resume=recommended), config)

    run = export_run(orchestrator, config)
    if runs_dir is not None:
        write_run(run, runs_dir)
    return run


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="materials-triage",
        description="Turn a research goal into a ranked, fully-cited shortlist of "
        "candidate materials drawn only from public databases.",
    )
    parser.add_argument("goal", help="the natural-language materials-triage goal")
    parser.add_argument(
        "--view",
        choices=["pi", "audit"],
        default="pi",
        help="pi = concise summary (default); audit = full technical trace",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="if set, persist the run as <runs-dir>/<run_id>.json for later replay",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"size of the presented/citable shortlist (default {DEFAULT_TOP_K}); "
        "the full ranking is still persisted",
    )
    parser.add_argument("--thread-id", default="cli", help="run/thread identifier")
    return parser.parse_args(argv)


def _load_env() -> None:
    """Load a local ``.env`` (X_API_KEY, OPENALEX_MAILTO, AWS_*) if python-dotenv is
    installed; a no-op otherwise, so the package runs without the optional dep."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``materials-triage doctor`` runs the environment self-check
    (and returns its exit code); otherwise the argument is a triage goal — wire the
    real Bedrock / Materials Project / OpenAlex seams, run the triage, and print the
    requested view to stdout. Returns a process exit code."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "doctor":
        from materials_triage.doctor import run_doctor

        _load_env()
        return run_doctor(os.environ)

    args = _parse_args(argv)
    _load_env()
    # Concrete seams imported lazily so importing this module (and the offline
    # ``triage`` tests) never pulls in langchain/requests.
    from materials_triage.agent.llm import HypothesisProvider as BedrockHypothesisProvider
    from materials_triage.agent.llm import SynthesisProvider as BedrockSynthesisProvider
    from materials_triage.retrieval.rag import LiteratureRAG, OpenAlexFetcher
    from materials_triage.sources.materials_project import MaterialsProjectAdapter

    run = triage(
        args.goal,
        adapter=MaterialsProjectAdapter(),
        provider=BedrockHypothesisProvider(),
        synthesis_provider=BedrockSynthesisProvider(),
        rag=LiteratureRAG(OpenAlexFetcher()),
        top_k=args.top_k,
        runs_dir=args.runs_dir,
        thread_id=args.thread_id,
    )
    print(render_run(run, view=args.view, top_k=args.top_k))
    return 0
