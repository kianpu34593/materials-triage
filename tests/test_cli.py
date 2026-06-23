"""Tests for the CLI driver in materials_triage.cli.

``triage`` runs the orchestrator end to end for one goal, auto-accepting the
spec-build human-in-the-loop interrupt (a non-interactive CLI run takes the
recommended spec), and returns the exported TriageRun. The seams (adapter, LLM
providers, RAG) are injected so the whole driver runs offline.
"""

from materials_triage.cli import main, triage
from materials_triage.core.hypothesis import (
    ConstraintProposal,
    Hypothesis,
    RankingProposal,
)
from materials_triage.core.run_trace import TriageRun
from materials_triage.core.schema import (
    Candidate,
    Constraint,
    PredicateRouting,
    PropertyValue,
    Provenance,
    RankingTarget,
    RetrievalResult,
    ScoredCandidate,
    TriageResult,
)
from materials_triage.core.synthesis import GroundedClaim, Synthesis
from materials_triage.sources.base import SourceAdapter


class _FakeAdapter(SourceAdapter):
    def __init__(self, candidates):
        self._candidates = candidates

    def retrieve(self, spec):
        return RetrievalResult(candidates=tuple(self._candidates))

    def classify_predicates(self, spec):
        return PredicateRouting()


def _candidate(identifier, band_gap):
    prov = Provenance(source="Materials Project", record_id=identifier, method="computational")
    return Candidate(
        identifier=identifier,
        formula="ZnO",
        properties={"band_gap": PropertyValue(value=band_gap, unit="eV", provenance=prov)},
    )


class _StubProvider:
    """A hypothesis provider returning a fixed, compilable hypothesis."""

    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def propose(self, prompt):
        return self._hypothesis


def _compilable_hypothesis():
    return Hypothesis(
        proposals=(
            ConstraintProposal(
                constraint=Constraint(property_name="band_gap", min=2.0),
                rationale="wide gap",
                confidence=0.8,
            ),
            RankingProposal(
                ranking_target=RankingTarget(
                    property_name="band_gap",
                    direction="maximize",
                    weight=1.0,
                    lower=1.0,
                    target=4.0,
                ),
                rationale="prefer wider",
                confidence=0.8,
            ),
        ),
        mechanism="wide gaps lower leakage",
    )


def test_triage_runs_end_to_end_and_auto_accepts_the_spec_gate():
    """A non-interactive triage run drives the whole graph for one goal: the LLM's
    hypothesis is compiled to a spec, the spec-build interrupt is auto-accepted (the
    recommended spec), and the result comes back as a TriageRun with the survivor
    ranked — no human input required."""
    adapter = _FakeAdapter([_candidate("mp-1", 3.0), _candidate("mp-low", 1.0)])
    provider = _StubProvider(_compilable_hypothesis())

    run = triage("wide-gap oxide", adapter=adapter, provider=provider, thread_id="t-end-to-end")

    assert isinstance(run, TriageRun)
    assert run.goal == "wide-gap oxide"
    # The spec gate was auto-accepted and the deterministic core ran: mp-1 survives
    # the band_gap >= 2.0 filter and is ranked; mp-low is dropped.
    assert [sc.candidate.identifier for sc in run.result.ranked] == ["mp-1"]
    assert {ex.candidate.identifier for ex in run.result.excluded} == {"mp-low"}


def test_triage_persists_the_run_when_a_runs_dir_is_given(tmp_path):
    """With a runs_dir, the completed run is written as <run_id>.json so it can be
    replayed / audited later; without one, triage just returns the in-memory run."""
    adapter = _FakeAdapter([_candidate("mp-1", 3.0)])
    provider = _StubProvider(_compilable_hypothesis())

    run = triage(
        "wide-gap oxide",
        adapter=adapter,
        provider=provider,
        runs_dir=str(tmp_path),
        thread_id="t-persist",
    )

    written = tmp_path / f"{run.run_id}.json"
    assert written.exists()


def test_main_parses_args_and_prints_the_requested_view(monkeypatch, capsys):
    """main parses the goal + --view, runs triage, and prints the chosen view. The
    triage call is stubbed so this stays offline; we assert the goal was forwarded and
    the audit view (which leads with 'Run:') reached stdout."""
    canned = TriageRun(run_id="r1", goal="wide-gap oxide", result=TriageResult())
    seen = {}

    def fake_triage(goal, **kwargs):
        seen["goal"] = goal
        return canned

    monkeypatch.setattr("materials_triage.cli.triage", fake_triage)

    main(["wide-gap oxide", "--view", "audit"])

    out = capsys.readouterr().out
    assert seen["goal"] == "wide-gap oxide"
    assert "Run: r1" in out  # the audit view was rendered to stdout


class _RecordingSynthesisProvider:
    def __init__(self, synthesis):
        self._synthesis = synthesis
        self.prompts = []

    def synthesize(self, prompt):
        self.prompts.append(prompt)
        return self._synthesis


def test_triage_caps_the_synthesis_citable_list_to_top_k():
    """triage forwards top_k to the synthesis step, so even when many candidates
    survive, the LLM is handed only the top_k as citable (the hallucination fix)."""
    rec = _RecordingSynthesisProvider(
        Synthesis(summary="s", claims=(GroundedClaim(text="t", record_id="mp-0"),))
    )
    adapter = _FakeAdapter([_candidate(f"mp-{i}", 3.0) for i in range(6)])
    provider = _StubProvider(_compilable_hypothesis())

    triage(
        "wide-gap oxide",
        adapter=adapter,
        provider=provider,
        synthesis_provider=rec,
        top_k=3,
        thread_id="t-topk",
    )

    assert rec.prompts[0].count("score=") == 3  # only top 3 offered as citable


def test_main_forwards_top_k_to_triage_and_render(monkeypatch, capsys):
    """--top-k controls BOTH the synthesis citable cap (via triage) and the displayed
    shortlist (via render): the flag value reaches triage and the view is capped."""
    big = TriageRun(
        run_id="r",
        goal="g",
        result=TriageResult(
            ranked=tuple(
                ScoredCandidate(candidate=_candidate(f"mp-{i:02d}", 3.0), score=1.0 - i / 100)
                for i in range(25)
            )
        ),
    )
    seen = {}

    def fake_triage(goal, **kwargs):
        seen["top_k"] = kwargs.get("top_k")
        return big

    monkeypatch.setattr("materials_triage.cli.triage", fake_triage)

    main(["g", "--top-k", "5"])

    out = capsys.readouterr().out
    assert seen["top_k"] == 5  # forwarded to triage (synthesis cap)
    assert "5 of 25" in out  # forwarded to render (display cap)


def test_main_defaults_to_the_pi_view(monkeypatch, capsys):
    """With no --view, main renders the concise PI view (which leads with 'Goal:' and
    a 'Ranked shortlist:' section, not the audit's 'Run:' header)."""
    canned = TriageRun(run_id="r2", goal="g", result=TriageResult())
    monkeypatch.setattr("materials_triage.cli.triage", lambda goal, **kw: canned)

    main(["g"])

    out = capsys.readouterr().out
    assert "Ranked shortlist:" in out
    assert "Run: r2" not in out  # not the audit view
