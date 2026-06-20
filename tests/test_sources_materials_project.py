"""Tests for the Materials Project adapter in materials_triage.sources.materials_project.

Parsing is exercised through the public ``retrieve`` method with a fake transport
that returns a fixture envelope, so every test is deterministic and offline.
"""

import os

import pytest

from materials_triage.core.ranking import rank
from materials_triage.core.schema import Constraint, RankingTarget, TriageSpec
from materials_triage.core.scoring import apply_hard_filters
from materials_triage.sources.materials_project import MaterialsProjectAdapter


def _spec() -> TriageSpec:
    return TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))


def _fixed(envelope: dict) -> MaterialsProjectAdapter:
    """An adapter whose transport always returns ``envelope`` (offline)."""
    return MaterialsProjectAdapter(http_get=lambda url, params, headers: envelope)


def test_retrieve_maps_a_one_doc_payload_to_a_candidate():
    """A single SummaryDoc becomes one Candidate carrying its returned id, pretty
    formula, and band_gap pinned to eV (the payload never states units)."""
    envelope = {
        "data": [{"material_id": "mp-aaaaadyf", "formula_pretty": "TiO2", "band_gap": 1.7719}],
        "meta": {},
    }

    candidates = _fixed(envelope).retrieve(_spec())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.identifier == "mp-aaaaadyf"
    assert candidate.formula == "TiO2"
    assert candidate.properties["band_gap"].value == 1.7719
    assert candidate.properties["band_gap"].unit == "eV"


def test_retrieve_stores_the_returned_id_not_the_query_id():
    """The sandbox mirror anonymizes ids: a query for 'mp-2657' comes back as
    'mp-aaaaadyf'. The adapter must store the RETURNED id in both identifier and
    every property's provenance, so citations resolve to what the source issued."""
    envelope = {
        "data": [{"material_id": "mp-aaaaadyf", "formula_pretty": "TiO2", "band_gap": 1.7719}],
        "meta": {},
    }

    candidate = _fixed(envelope).retrieve(_spec())[0]

    assert candidate.identifier == "mp-aaaaadyf"
    assert candidate.properties["band_gap"].provenance.record_id == "mp-aaaaadyf"
    assert candidate.properties["band_gap"].provenance.source == "Materials Project"


def test_retrieve_marks_a_null_field_as_missing():
    """A field present in the payload but null carries no number, so it becomes a
    flagged-missing PropertyValue (value=None) rather than a fabricated zero."""
    envelope = {
        "data": [{"material_id": "mp-x", "formula_pretty": "TiO2", "band_gap": None}],
        "meta": {},
    }

    candidate = _fixed(envelope).retrieve(_spec())[0]

    band_gap = candidate.properties["band_gap"]
    assert band_gap.missing is True
    assert band_gap.value is None
    assert band_gap.unit == "eV"


def test_retrieve_maps_all_pinned_fields_with_their_units():
    """Every property the adapter knows is mapped with the unit it pins, since the
    payload itself never carries units (band_gap eV, energies eV/atom, density
    g/cm³, moduli GPa)."""
    doc = {
        "material_id": "mp-aaaaadyf",
        "formula_pretty": "TiO2",
        "band_gap": 1.7719,
        "energy_above_hull": 0.0436,
        "formation_energy_per_atom": -3.4644,
        "density": 4.25,
        "bulk_modulus": 210.0,
        "shear_modulus": 112.0,
    }

    props = _fixed({"data": [doc], "meta": {}}).retrieve(_spec())[0].properties

    assert props["band_gap"].unit == "eV"
    assert props["energy_above_hull"].unit == "eV/atom"
    assert props["formation_energy_per_atom"].unit == "eV/atom"
    assert props["density"].unit == "g/cm³"
    assert props["bulk_modulus"].unit == "GPa"
    assert props["shear_modulus"].unit == "GPa"
    assert props["formation_energy_per_atom"].value == -3.4644


def test_retrieve_unwraps_multiple_docs_and_ignores_meta():
    """The transport envelope wraps a data list and a meta block; retrieve maps
    every doc in order and discards meta — the core never sees raw transport."""
    envelope = {
        "data": [
            {"material_id": "mp-a", "formula_pretty": "TiO2", "band_gap": 2.0},
            {"material_id": "mp-b", "formula_pretty": "SiO2", "band_gap": 8.9},
        ],
        "meta": {"total_doc": 2},
    }

    candidates = _fixed(envelope).retrieve(_spec())

    assert [c.identifier for c in candidates] == ["mp-a", "mp-b"]


def test_retrieve_returns_empty_list_when_data_is_empty():
    """A query that matches nothing yields an empty data list, so retrieve returns
    no candidates rather than erroring."""
    assert _fixed({"data": [], "meta": {}}).retrieve(_spec()) == []


def test_retrieve_requests_the_fields_the_pipeline_will_read():
    """The adapter asks the API only for the columns the run needs — the union of
    every constrained and ranked property plus the identity fields — so the parsed
    docs carry exactly what the filter and ranker read."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        ranking_targets=(RankingTarget(property_name="density", direction="minimize", weight=1.0),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    requested = set(captured["params"]["_fields"].split(","))
    assert {"band_gap", "density", "material_id", "formula_pretty"} <= requested


def test_retrieve_sends_the_api_key_header():
    """The summary API authenticates by an X-API-KEY header; retrieve sends the
    configured key so the live request is authorized."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["headers"] = headers
        return {"data": [], "meta": {}}

    MaterialsProjectAdapter(http_get=spy, api_key="secret-key").retrieve(_spec())

    assert captured["headers"]["X-API-KEY"] == "secret-key"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_retrieve_returns_real_candidates():
    """Smoke test against the real (sandboxed) API — deselected by default. With a
    key set it should return candidates whose ids are the anonymized mp-… tokens."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=0.0),))

    candidates = MaterialsProjectAdapter().retrieve(spec)

    assert candidates
    assert candidates[0].identifier.startswith("mp-")


def test_retrieved_candidates_flow_through_filter_and_rank():
    """The payoff: candidates the adapter parses drop straight into the existing
    hard-filter and ranking stages — a sub-min material is excluded, the survivor
    is ranked — proving retrieve → apply_hard_filters → rank composes end to end."""
    envelope = {
        "data": [
            {"material_id": "mp-good", "formula_pretty": "GaN", "band_gap": 3.4, "density": 6.1},
            {"material_id": "mp-low", "formula_pretty": "PbO", "band_gap": 0.4, "density": 9.5},
        ],
        "meta": {},
    }
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        ranking_targets=(RankingTarget(property_name="density", direction="minimize", weight=1.0),),
    )

    candidates = _fixed(envelope).retrieve(spec)
    survivors, excluded = apply_hard_filters(candidates, spec.constraints)
    result = rank(survivors, spec.ranking_targets)

    assert [sc.candidate.identifier for sc in result.ranked] == ["mp-good"]
    assert excluded[0].candidate.identifier == "mp-low"
    assert excluded[0].reason == "below_min"
