"""Renderers — workflow step 9 (the two output views).

``render_pi`` is the concise PI summary: the grounded narrative plus the ranked
shortlist, what a scientist reads first. ``render_audit`` is the full technical
trace over a :class:`~materials_triage.core.run_trace.TriageRun`: the spec, the
per-step trace, the ranked shortlist with score contributions, every exclusion
with its reason, and the cited narrative — the replayable record. Both are pure
string builders over already-validated domain objects (no LLM, no I/O).
"""

from materials_triage.core.run_trace import TriageRun
from materials_triage.core.schema import ScoredCandidate, TriageResult
from materials_triage.core.synthesis import Synthesis


def _properties(scored: ScoredCandidate) -> str:
    """Render a candidate's retrieved property values (skipping missing ones)."""
    cand = scored.candidate
    return ", ".join(
        f"{name}={pv.value} {pv.unit}"
        for name, pv in cand.properties.items()
        if pv.value is not None
    )


def _shortlist_lines(result: TriageResult, *, limit: int | None = None) -> list[str]:
    """The ranked shortlist as numbered lines (best-first), optionally capped."""
    ranked = result.ranked if limit is None else result.ranked[:limit]
    lines = []
    for i, scored in enumerate(ranked, 1):
        cand = scored.candidate
        lines.append(
            f"  {i:>2}. {cand.identifier} ({cand.formula})  score={scored.score:.3f}"
            f"  [{_properties(scored)}]"
        )
    return lines


def render_pi(result: TriageResult, synthesis: Synthesis | None = None, *, top_k: int = 5) -> str:
    """The PI view: the grounded summary (if any) and the top-k ranked shortlist,
    with a one-line note on how many candidates were excluded."""
    out: list[str] = ["# Candidate shortlist (PI summary)", ""]
    if synthesis is not None:
        out += [synthesis.summary, ""]
    if result.ranked:
        out.append(f"Top {min(top_k, len(result.ranked))} of {len(result.ranked)} ranked:")
        out += _shortlist_lines(result, limit=top_k)
    else:
        out.append("No candidates survived the hard filters.")
    if result.excluded:
        out += ["", f"({len(result.excluded)} candidate(s) excluded — see the audit view.)"]
    return "\n".join(out)


def render_audit(run: TriageRun) -> str:
    """The audit view: the full replayable trace of one run — goal, spec, per-step
    writes, the complete ranked shortlist with score contributions, every
    exclusion with its reason, and the cited narrative."""
    out: list[str] = [f"# Triage audit — run {run.run_id}", "", f"Goal: {run.goal}", ""]

    if run.spec is not None:
        out.append("## Spec")
        for c in run.spec.constraints:
            bounds = ", ".join(
                f"{k}={v}" for k, v in (("min", c.min), ("max", c.max)) if v is not None
            )
            out.append(f"  constraint  {c.property_name}: {bounds}")
        for t in run.spec.ranking_targets:
            out.append(f"  rank        {t.property_name}: {t.direction} (weight={t.weight:.3f})")
        out.append("")

    out.append("## Step trace")
    for step in run.steps:
        written = ", ".join(sorted(step.writes)) if step.writes else "(pass-through)"
        out.append(f"  {step.name}: {written}")
    out.append("")

    if run.result is not None:
        out.append(f"## Ranked shortlist ({len(run.result.ranked)})")
        out += _shortlist_lines(run.result) or ["  (none)"]
        out.append("")
        out.append(f"## Excluded ({len(run.result.excluded)})")
        for ex in run.result.excluded:
            detail = f"{ex.property_name} {ex.reason}"
            if ex.bound is not None:
                detail += f" (value={ex.value}, bound={ex.bound})"
            out.append(f"  - {ex.candidate.identifier} ({ex.candidate.formula}): {detail}")
        out.append("")

    if run.synthesis is not None:
        out.append("## Narrative")
        out += [run.synthesis.summary, ""]
        for claim in run.synthesis.claims:
            out.append(f"  - [{claim.record_id}] {claim.text}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"
