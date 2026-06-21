"""Tests for the input policy gate in materials_triage.policy.guardrails.

The gate is workflow step 1: it classifies a request as in-scope materials-triage
vs forbidden/out-of-scope and returns a ``GateDecision``. It is deterministic in
v1 (no LLM) so it is injection-resistant by construction and exactly testable.
"""

from materials_triage.policy.guardrails import check_input


def test_in_scope_materials_query_is_allowed():
    decision = check_input("rank perovskite oxides for oxygen evolution catalysis")
    assert decision.allowed is True


def test_wet_lab_action_is_refused():
    decision = check_input("synthesize and run a CV scan on LaCoO3 in the lab")
    assert decision.allowed is False
    assert decision.category == "wet_lab"


def test_private_data_request_is_refused():
    decision = check_input(
        "rank these oxides using our internal lab database of unpublished cycle-life results"
    )
    assert decision.allowed is False
    assert decision.category == "private_data"


def test_scrape_paywalled_source_is_refused():
    decision = check_input("scrape the paywalled Elsevier review PDF and extract band gaps")
    assert decision.allowed is False
    assert decision.category == "paywalled"
