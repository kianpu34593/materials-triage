"""Plain-text renderers for a completed run (workflow step 9).

Two views over one :class:`~materials_triage.core.run_trace.TriageRun` (the audit
export): :func:`render_pi` is the concise PI-facing summary, :func:`render_audit`
is the full technical trace. Both are pure functions returning a string the CLI
prints — plain text in v1 (Rich is v2). Rendering is a presentation concern done
*after* the run, reading the exported artifacts; it is not a graph node.
"""

from typing import Literal

from materials_triage.core.run_trace import TriageRun


def render_run(run: TriageRun, *, view: Literal["pi", "audit"] = "pi") -> str:
    """Render a run in the requested view: ``pi`` (concise summary) or ``audit`` (full
    trace). Raises ``ValueError`` on an unknown view rather than silently defaulting."""
    if view == "pi":
        return render_pi(run)
    if view == "audit":
        return render_audit(run)
    raise ValueError(f"unknown view {view!r}; expected 'pi' or 'audit'")


def render_pi(run: TriageRun) -> str:
    """Render the concise PI view: the goal, the synthesis summary (lead), and the
    ranked shortlist best-first (formula · id · score)."""
    lines = [f"Goal: {run.goal}", ""]
    if run.synthesis is not None:
        lines += [run.synthesis.summary, ""]
    lines.append("Ranked shortlist:")
    ranked = run.result.ranked if run.result is not None else ()
    if not ranked:
        lines.append("  (no candidates matched the spec)")
    for position, scored in enumerate(ranked, start=1):
        cand = scored.candidate
        line = f"  {position}. {cand.formula} ({cand.identifier}) — score {scored.score:.2f}"
        if scored.flagged_missing:
            line += f" ⚠ missing data: {', '.join(sorted(scored.flagged_missing))}"
        lines.append(line)
    if run.caveats:
        lines += ["", "Caveats:"]
        lines += [f"  ⚠ {caveat}" for caveat in run.caveats]
    return "\n".join(lines)


def render_audit(run: TriageRun) -> str:
    """Render the full technical view: run id, spec, hypothesis, ranked and excluded
    candidates (each drop with its structured reason), the cited synthesis claims,
    caveats, and the per-step execution trace — everything the PI view summarizes away."""
    lines = [f"Run: {run.run_id}", f"Goal: {run.goal}", ""]

    if run.spec is not None:
        lines.append("Spec:")
        for constraint in run.spec.constraints:
            bounds = []
            if constraint.min is not None:
                bounds.append(f"min={constraint.min}")
            if constraint.max is not None:
                bounds.append(f"max={constraint.max}")
            lines.append(f"  - {constraint.property_name} ({', '.join(bounds)})")
        for boolean in run.spec.boolean_constraints:
            lines.append(f"  - {boolean.property_name} required={boolean.required}")
        for predicate in run.spec.element_predicates:
            members = ", ".join(sorted(predicate.members))
            lines.append(f"  - elements {predicate.quantifier} of: {members}")
        if run.spec.count is not None:
            bounds = []
            if run.spec.count.min is not None:
                bounds.append(f"min={run.spec.count.min}")
            if run.spec.count.max is not None:
                bounds.append(f"max={run.spec.count.max}")
            lines.append(f"  - distinct element count ({', '.join(bounds)})")
        for target in run.spec.ranking_targets:
            lines.append(
                f"  - rank {target.property_name} {target.direction} (weight {target.weight})"
            )
        lines.append(f"  ranking method: {run.spec.ranking_method}")
        lines.append("")

    if run.hypothesis is not None:
        lines += ["Hypothesis:", f"  mechanism: {run.hypothesis.mechanism}", ""]

    if run.literature:
        lines.append("Literature grounding:")
        lines += [f"  - {p.title} ({p.provenance.record_id})" for p in run.literature]
        lines.append("")

    ranked = run.result.ranked if run.result is not None else ()
    lines.append("Ranked candidates:")
    if not ranked:
        lines.append("  (no candidates matched the spec)")
    for position, scored in enumerate(ranked, start=1):
        cand = scored.candidate
        lines.append(f"  {position}. {cand.formula} ({cand.identifier}) — score {scored.score:.3f}")
        for name, prop in cand.properties.items():
            lines.append(f"       {name} = {prop.value} {prop.unit or ''}".rstrip())
        if scored.flagged_missing:
            missing = ", ".join(sorted(scored.flagged_missing))
            lines.append(f"       ⚠ missing/imputed: {missing}")

    excluded = run.result.excluded if run.result is not None else ()
    if excluded:
        lines += ["", "Excluded candidates:"]
        for ex in excluded:
            detail = f"{ex.property_name}: {ex.reason}"
            if ex.value is not None and ex.bound is not None:
                detail += f" (value {ex.value} vs bound {ex.bound})"
            lines.append(f"  - {ex.candidate.formula} ({ex.candidate.identifier}) — {detail}")

    if run.synthesis is not None:
        lines += ["", "Synthesis:", f"  {run.synthesis.summary}"]
        for claim in run.synthesis.claims:
            lines.append(f"  - {claim.text} [cite: {claim.record_id}]")

    if run.caveats:
        lines += ["", "Caveats:"]
        lines += [f"  ⚠ {caveat}" for caveat in run.caveats]

    if run.steps:
        lines += ["", "Trace:"]
        lines += [
            f"  - {step.name}: wrote {', '.join(step.writes) or '(nothing)'}" for step in run.steps
        ]

    return "\n".join(lines)
