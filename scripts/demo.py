"""A quick end-to-end demo of the triage orchestrator (gate -> hypothesis ->
spec_build -> retrieve -> filter -> rank), backed by the REAL seams: Claude on
AWS Bedrock for the hypothesis and the (sandboxed) Materials Project mirror for
retrieval.

This is a "try it out" runner, not part of the package. It shows three things:

  1. the input policy gate refusing a forbidden request before any LLM call,
  2. a real in-scope run flowing through to a ranked, provenance-tagged result,
  3. the human-in-the-loop spec gate — here auto-accepted (the demo echoes the
     recommended spec back) so the run completes unattended.

Prerequisites (the live path needs both):
  - AWS credentials resolvable by botocore (e.g. `~/.aws/credentials` / SSO) for
    Bedrock. Do NOT print or read these.
  - `X_API_KEY` in the environment for the Materials Project mirror.

Run from the worktree root:

    PYTHONPATH="$PWD/src" python scripts/demo.py
    PYTHONPATH="$PWD/src" python scripts/demo.py "wide-gap oxide for a UV photodetector"
"""

import os
import sys

from langgraph.types import Command

# Load a local `.env` (if present) so the live seams can read their credentials —
# X_API_KEY (Materials Project) and the AWS_* vars (Bedrock) — before the real
# adapter/provider are constructed. Mirrors tests/conftest.py: real shell exports
# win over `.env`, it is a no-op without a `.env`, and the import is optional so
# the offline gate-refusal leg runs without python-dotenv installed.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()

from materials_triage.agent.llm import HypothesisProvider
from materials_triage.agent.orchestrator import InputRefused, build_orchestrator
from materials_triage.sources.materials_project import MaterialsProjectAdapter

FORBIDDEN_QUERY = "scrape unpublished band gaps from a paywalled journal"
DEFAULT_GOAL = "wide-gap oxide semiconductor with low formation energy"


def _print_result(result) -> None:
    """Pretty-print a TriageResult: the ranked shortlist with scores and the
    dropped candidates with their machine-readable reasons."""
    print(f"\n  Ranked shortlist ({len(result.ranked)}):")
    for i, scored in enumerate(result.ranked, 1):
        cand = scored.candidate
        props = ", ".join(
            f"{name}={pv.value}{'' if pv.value is None else ' ' + pv.unit}"
            for name, pv in cand.properties.items()
        )
        head = f"    {i:>2}. {cand.identifier:<14} {cand.formula:<10} score={scored.score:.3f}"
        print(f"{head}  [{props}]")
    if result.excluded:
        print(f"\n  Excluded ({len(result.excluded)}):")
        for ex in result.excluded:
            print(f"    - {ex.candidate.identifier:<14} {ex.reason}")


def demo_gate_refusal(orchestrator) -> None:
    print("=" * 78)
    print("1) Gate refusal — a forbidden request is stopped before any LLM call")
    print("=" * 78)
    print(f"  query: {FORBIDDEN_QUERY!r}")
    config = {"configurable": {"thread_id": "demo-refusal"}}
    try:
        orchestrator.invoke({"goal": FORBIDDEN_QUERY, "run_id": "demo-refusal"}, config)
    except InputRefused as refused:
        print(f"  REFUSED [{refused.decision.category}]: {refused.decision.reason}")
    else:
        print("  (unexpected) the gate allowed a forbidden request")


def demo_full_run(orchestrator, goal: str) -> None:
    print("\n" + "=" * 78)
    print("2) Full run — gate -> hypothesis (Bedrock) -> spec -> retrieve (MP) -> filter -> rank")
    print("=" * 78)
    print(f"  goal: {goal!r}")
    config = {"configurable": {"thread_id": "demo-full"}}

    # First leg: runs through the gate and the LLM hypothesis, then PAUSES at the
    # spec_build human-in-the-loop gate (interrupt) with a recommended spec.
    paused = orchestrator.invoke({"goal": goal, "run_id": "demo-full"}, config)
    interrupts = paused.get("__interrupt__")
    if not interrupts:
        print("  (unexpected) the run did not pause for spec confirmation")
        return

    payload = interrupts[0].value
    recommended = payload["recommended_spec"]
    print("\n  Recommended spec (from the LLM hypothesis):")
    for c in recommended.constraints:
        bounds = ", ".join(f"{k}={v}" for k, v in (("min", c.min), ("max", c.max)) if v is not None)
        print(f"    constraint  {c.property_name}: {bounds}")
    for t in recommended.ranking_targets:
        print(f"    rank        {t.property_name}: {t.direction} (weight={t.weight:.3f})")
    if payload["weights_were_normalized"]:
        print("    note: ranking weights were rescaled to sum to 1.")

    # Auto-accept: echo the recommended spec back as the human's approval, so the
    # demo completes unattended. A real UI would let the scientist edit it here.
    print("\n  Auto-accepting the recommended spec and resuming...")
    final = orchestrator.invoke(Command(resume=recommended), config)
    _print_result(final["result"])


def main() -> int:
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL

    if not os.environ.get("X_API_KEY"):
        print("warning: X_API_KEY is not set — the Materials Project retrieval will be empty.")

    # Real seams: default HypothesisProvider builds the Bedrock transport lazily;
    # default MaterialsProjectAdapter reads X_API_KEY and uses requests.
    orchestrator = build_orchestrator(
        adapter=MaterialsProjectAdapter(),
        provider=HypothesisProvider(),
    )

    demo_gate_refusal(orchestrator)
    demo_full_run(orchestrator, goal)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
