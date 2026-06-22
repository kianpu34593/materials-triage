"""Tests for the CLI entry point (run_triage) — the whole pipeline, offline.

run_triage drives gate -> hypothesis -> spec (auto-accepted) -> retrieve ->
filter -> rank -> synthesis -> validate -> render with injected fakes, so this is
the end-to-end smoke test that the wired graph produces a rendered view and
refuses forbidden input — no LLM, no network.
"""

import pytest

from materials_triage.agent.orchestrator import InputRefused
from materials_triage.cli import run_triage
from materials_triage.core.hypothesis import ConstraintProposal, Hypothesis, RankingProposal
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PropertyValue,
    Provenance,
    RankingTarget,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.sources.base import SourceAdapter


class _Adapter(SourceAdapter):
    def __init__(self, candidates):
        self._candidates = candidates

    def retrieve(self, spec):
        return list(self._candidates)

    def property_vocabulary(self):
        return {"band_gap": "eV"}


class _Provider:
    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def propose(self, prompt):
        return self._hypothesis


class _SynthProvider:
    def __init__(self, synthesis):
        self._synthesis = synthesis

    def synthesize(self, prompt):
        return self._synthesis


def _candidate(identifier, band_gap):
    prov = Provenance(source="Materials Project", record_id=identifier)
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=prov)},
    )


def _hypothesis():
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="band_gap", direction="maximize", weight=1.0
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


def test_run_triage_end_to_end_renders_a_pi_view_and_writes_the_trace(tmp_path):
    """A full offline run: the LLM hypothesis compiles to a spec (auto-accepted),
    the fake source retrieves two candidates, one is filtered out, the survivor is
    ranked and narrated — and the PI view shows the narrative + the survivor, with
    the audit trace persisted to disk."""
    adapter = _Adapter([_candidate("mp-keep", 4.0), _candidate("mp-drop", 1.0)])
    provider = _Provider(_hypothesis())
    synth = _SynthProvider(
        Synthesis(
            summary="mp-keep is the standout.",
            claims=(GroundedClaim(text="widest gap", record_id="mp-keep"),),
        )
    )

    out = run_triage(
        "wide-gap oxide",
        adapter=adapter,
        provider=provider,
        synthesis_provider=synth,
        runs_dir=tmp_path / "runs",
        thread_id="run-1",
    )

    assert "mp-keep is the standout." in out
    assert "mp-keep" in out
    assert "mp-drop" not in out  # excluded; only summarized as a count in the PI view
    assert (tmp_path / "runs" / "run-1.json").exists()


def test_run_triage_audit_view_shows_the_excluded_candidate_and_reason(tmp_path):
    """The audit view exposes what the PI view summarizes away — the dropped
    candidate with its hard-filter reason."""
    adapter = _Adapter([_candidate("mp-keep", 4.0), _candidate("mp-drop", 1.0)])

    out = run_triage(
        "wide-gap oxide",
        adapter=adapter,
        provider=_Provider(_hypothesis()),
        synthesis_provider=_SynthProvider(Synthesis(summary="ok")),
        view="audit",
        thread_id="run-2",
    )

    assert "mp-drop" in out and "below_min" in out


def test_run_triage_raises_input_refused_for_a_forbidden_goal():
    """A forbidden request never reaches retrieval/LLM — run_triage surfaces the
    gate's InputRefused for the caller (main) to turn into a refusal message."""
    adapter = _Adapter([_candidate("mp-keep", 4.0)])

    with pytest.raises(InputRefused):
        run_triage(
            "scrape data from a paywalled journal",
            adapter=adapter,
            provider=_Provider(_hypothesis()),
            synthesis_provider=_SynthProvider(Synthesis(summary="ok")),
            thread_id="run-3",
        )
