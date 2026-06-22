"""A quick end-to-end demo of the full triage pipeline, backed by the REAL seams:
Claude on AWS Bedrock (hypothesis + synthesis) and the (sandboxed) Materials
Project mirror (retrieval).

It shows the whole workflow: gate -> hypothesis -> spec (auto-accepted at the
human-in-the-loop gate) -> retrieve -> filter -> rank -> synthesis -> output
validation -> render, in both the PI and audit views, plus the gate refusing a
forbidden request before any LLM call. This is a "try it out" runner that just
calls the package's CLI core (`run_triage`); the CLI itself is
`python -m materials_triage.cli "<goal>" --view pi|audit`.

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

# Load a local `.env` (if present) so the live seams can read their credentials —
# X_API_KEY (Materials Project) and the AWS_* vars (Bedrock) — before the real
# seams are constructed. Mirrors tests/conftest.py: real shell exports win over
# `.env`, it is a no-op without a `.env`, and the import is optional.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()

from materials_triage.agent.llm import HypothesisProvider, SynthesisProvider
from materials_triage.agent.orchestrator import InputRefused
from materials_triage.cli import run_triage
from materials_triage.sources.materials_project import MaterialsProjectAdapter

FORBIDDEN_QUERY = "scrape unpublished band gaps from a paywalled journal"
DEFAULT_GOAL = "wide-gap oxide semiconductor with low formation energy"
RUNS_DIR = "runs"


def _seams():
    """The real Bedrock + Materials Project seams (built lazily; no creds needed
    until invoked)."""
    return {
        "adapter": MaterialsProjectAdapter(),
        "provider": HypothesisProvider(),
        "synthesis_provider": SynthesisProvider(),
    }


def demo_gate_refusal() -> None:
    print("=" * 78)
    print("1) Gate refusal — a forbidden request is stopped before any LLM call")
    print("=" * 78)
    print(f"  query: {FORBIDDEN_QUERY!r}")
    try:
        run_triage(FORBIDDEN_QUERY, **_seams(), thread_id="demo-refusal")
    except InputRefused as refused:
        print(f"  REFUSED [{refused.decision.category}]: {refused.decision.reason}")
    else:
        print("  (unexpected) the gate allowed a forbidden request")


def demo_full_run(goal: str) -> None:
    print("\n" + "=" * 78)
    print("2) Full run — gate -> hypothesis -> spec -> retrieve -> filter -> rank")
    print("   -> synthesis -> validate -> render (real Bedrock + Materials Project)")
    print("=" * 78)
    print(f"  goal: {goal!r}\n")

    # One run, both views: the spec gate is auto-accepted inside run_triage, and
    # the audit trace is persisted under runs/<thread_id>.json.
    pi = run_triage(goal, **_seams(), view="pi", runs_dir=RUNS_DIR, thread_id="demo-full")
    print(pi)
    audit = run_triage(goal, **_seams(), view="audit", thread_id="demo-full-audit")
    print("\n" + "-" * 78)
    print(audit)
    print(f"(audit trace also written to {RUNS_DIR}/demo-full.json)")


def main() -> int:
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    if not os.environ.get("X_API_KEY"):
        print("warning: X_API_KEY is not set — the Materials Project retrieval will be empty.")

    demo_gate_refusal()
    demo_full_run(goal)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
