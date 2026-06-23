"""Tests for the Materials Project adapter in materials_triage.sources.materials_project.

Parsing is exercised through the public ``retrieve`` method with a fake transport
that returns a fixture envelope, so every test is deterministic and offline.
"""

import os

import pytest

from materials_triage.core.ranking import rank
from materials_triage.core.schema import (
    BooleanConstraint,
    Constraint,
    CountConstraint,
    ElementPredicate,
    RankingTarget,
    TriageSpec,
)
from materials_triage.core.scoring import apply_hard_filters
from materials_triage.sources.materials_project import (
    MaterialsProjectAdapter,
    _fetch_run_types,
    _field_task_id,
    _origin_task_ids,
    _query_params,
)


def _spec() -> TriageSpec:
    return TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0),))


def _fixed(envelope: dict) -> MaterialsProjectAdapter:
    """An adapter whose transport always returns ``envelope`` (offline)."""
    return MaterialsProjectAdapter(http_get=lambda url, params, headers: envelope)


def test_vocabulary_names_only_properties_retrieve_can_populate():
    """The contract #39 protects: every name the adapter publishes is one retrieve
    actually fills -- so a hypothesis built from the vocabulary never asks for a
    property that silently comes back empty. And the vocabulary needs no network."""
    adapter = MaterialsProjectAdapter(
        http_get=lambda url, params, headers: pytest.fail("vocabulary must not hit the network")
    )

    vocab = adapter.property_vocabulary()

    # a SummaryDoc carrying a value for every published field
    doc = {"material_id": "mp-1", "formula_pretty": "Si", **{name: 1.0 for name in vocab}}
    candidate = _two_endpoint({"data": [doc]}, {"data": []}).retrieve(_spec())[0]

    assert vocab  # non-empty: the source declares a surface
    assert set(vocab) <= set(candidate.properties)  # every published name got populated


def test_property_vocabulary_exposes_the_full_generated_surface():
    """The adapter publishes the whole schema-derived surface (the generated table),
    not just the original hand-pinned six -- efermi, total_magnetization, the
    refractive index. A genuinely dimensionless field carries unit=None."""
    adapter = MaterialsProjectAdapter(
        http_get=lambda url, params, headers: pytest.fail("vocabulary must not hit the network")
    )

    vocab = adapter.property_vocabulary()

    assert {"efermi", "total_magnetization", "n"} <= set(vocab)  # fields the old 6 lacked
    assert vocab["band_gap"] == "eV"  # a pinned unit still resolves
    assert vocab["n"] is None  # refractive index: dimensionless


def test_origin_task_ids_indexes_task_ids_by_origin_name():
    """MP's origins list (one entry per computed property doc) collapses to a
    name -> task_id lookup, the first step in tracing each value to its run."""
    origins = [
        {"name": "energy", "task_id": "t-energy", "last_updated": "2026-05-30"},
        {"name": "electronic_structure", "task_id": "t-bands", "last_updated": "2022-06-22"},
    ]

    assert _origin_task_ids(origins) == {"energy": "t-energy", "electronic_structure": "t-bands"}


def test_origin_task_ids_of_absent_origins_is_empty():
    """A doc with no origins (the field unrequested or null) yields no lookup,
    so downstream functional resolution simply finds nothing."""
    assert _origin_task_ids(None) == {}


def test_field_task_id_resolves_a_field_through_its_origin():
    """A summary field maps (via _FIELD_ORIGIN) to an origin name, then to that
    origin's task_id — the task whose run we'll read the functional from."""
    index = {"electronic_structure": "t-bands", "energy": "t-energy"}

    assert _field_task_id("band_gap", index) == "t-bands"
    assert _field_task_id("formation_energy_per_atom", index) == "t-energy"


def test_field_task_id_is_none_when_the_origin_is_absent():
    """A field whose origin doc wasn't computed for this material (band_gap needs an
    electronic_structure run, absent here) has no traceable task — functional unknown."""
    assert _field_task_id("band_gap", {"energy": "t-energy"}) is None


def test_field_task_id_is_none_for_a_field_with_no_origin_mapping():
    """A field outside _FIELD_ORIGIN (functional-independent, e.g. the element count
    nelements) has no origin and so no functional to trace."""
    assert _field_task_id("nelements", {"structure": "t-struct"}) is None


def test_fetch_run_types_batches_task_ids_into_one_call():
    """The functional lives in the task doc, not the summary: one batched
    /materials/tasks/ call maps every task_id to its run_type."""
    captured: dict = {}

    def transport(url, params, headers):
        captured["url"] = url
        captured["params"] = params
        return {
            "data": [
                {"task_id": "t-bands", "run_type": "GGA"},
                {"task_id": "t-energy", "run_type": "r2SCAN"},
            ]
        }

    run_types = _fetch_run_types(transport, {"X-API-KEY": "k"}, ["t-bands", "t-energy"])

    assert run_types == {"t-bands": "GGA", "t-energy": "r2SCAN"}
    assert captured["url"] == "/materials/tasks/"
    assert set(captured["params"]["_fields"].split(",")) == {"task_id", "run_type"}
    assert set(captured["params"]["task_ids"].split(",")) == {"t-bands", "t-energy"}


def test_fetch_run_types_degrades_to_empty_when_the_tasks_call_fails():
    """The functional is best-effort enrichment on an already-complete summary
    result: a failed tasks call yields an empty map (all functionals unknown)
    rather than aborting the retrieval."""

    def transport(url, params, headers):
        raise RuntimeError("tasks endpoint unavailable")

    assert _fetch_run_types(transport, {"X-API-KEY": "k"}, ["t-bands"]) == {}


def test_fetch_run_types_skips_the_call_when_there_are_no_task_ids():
    """With nothing to trace (no task-derived fields / no origins) the adapter
    makes no second network call at all."""

    def transport(url, params, headers):
        raise AssertionError("transport must not be called when there are no task_ids")

    assert _fetch_run_types(transport, {}, []) == {}


def _two_endpoint(summary: dict, tasks: dict):
    """A transport that answers the summary and tasks endpoints by URL (offline)."""

    def transport(url, params, headers):
        return tasks if url == "/materials/tasks/" else summary

    return MaterialsProjectAdapter(http_get=transport)


def test_retrieve_stamps_each_property_with_its_own_xc_functional():
    """The payoff: a value's provenance carries the functional of the task that
    produced IT — band_gap (GGA bandstructure) and the energy (r2SCAN) differ
    within one material, traced via origins -> tasks run_type."""
    summary = {
        "data": [
            {
                "material_id": "mp-x",
                "formula_pretty": "Ac2O3",
                "band_gap": 3.0,
                "formation_energy_per_atom": -3.0,
                "origins": [
                    {"name": "electronic_structure", "task_id": "t-bands"},
                    {"name": "energy", "task_id": "t-energy"},
                ],
            }
        ],
        "meta": {},
    }
    tasks = {
        "data": [
            {"task_id": "t-bands", "run_type": "GGA"},
            {"task_id": "t-energy", "run_type": "r2SCAN"},
        ]
    }
    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        ranking_targets=(
            RankingTarget(
                property_name="formation_energy_per_atom", direction="minimize", weight=1.0
            ),
        ),
    )

    props = _two_endpoint(summary, tasks).retrieve(spec)[0].properties

    assert props["band_gap"].provenance.xc_functional == "GGA"
    assert props["formation_energy_per_atom"].provenance.xc_functional == "r2SCAN"


def test_retrieve_leaves_xc_functional_none_when_the_origin_is_absent():
    """A property with no origin doc (no elasticity run) gets no functional —
    honestly unknown, not fabricated."""
    summary = {
        "data": [
            {
                "material_id": "mp-x",
                "formula_pretty": "Ac2O3",
                "bulk_modulus": 200.0,
                "origins": [{"name": "energy", "task_id": "t-energy"}],
            }
        ],
        "meta": {},
    }
    tasks = {"data": [{"task_id": "t-energy", "run_type": "r2SCAN"}]}
    spec = TriageSpec(constraints=(Constraint(property_name="bulk_modulus", min=1.0),))

    props = _two_endpoint(summary, tasks).retrieve(spec)[0].properties

    assert props["bulk_modulus"].provenance.xc_functional is None


def test_retrieve_requests_origins_to_trace_the_functional():
    """The adapter must ask the summary endpoint for origins, the bridge from a
    value to the task carrying its functional."""
    captured: dict = {}

    def transport(url, params, headers):
        captured.setdefault("summary_params", params)
        return {"data": [], "meta": {}}

    MaterialsProjectAdapter(http_get=transport).retrieve(_spec())

    assert "origins" in captured["summary_params"]["_fields"].split(",")


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


def test_retrieve_collapses_the_vrh_modulus_dict_to_its_vrh_average():
    """MP serves the elastic moduli as a Voigt-Reuss-Hill dict, not a bare number.
    The adapter collapses ``{voigt, reuss, vrh}`` to the VRH average so the value the
    pipeline filters and ranks on is the scalar the vocabulary promises — not a dict
    that would fail PropertyValue validation."""
    doc = {
        "material_id": "mp-x",
        "formula_pretty": "TiO2",
        "bulk_modulus": {"voigt": 205.0, "reuss": 190.0, "vrh": 197.5},
        "shear_modulus": {"voigt": 120.0, "reuss": 104.0, "vrh": 112.0},
    }

    props = _fixed({"data": [doc], "meta": {}}).retrieve(_spec())[0].properties

    assert props["bulk_modulus"].value == 197.5
    assert props["bulk_modulus"].unit == "GPa"
    assert props["bulk_modulus"].missing is False
    assert props["shear_modulus"].value == 112.0


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


def test_retrieve_scopes_the_query_by_required_elements():
    """An "all"-quantifier ElementPredicate scopes the pool server-side via the
    sorted, comma-joined `elements` param; numeric bounds stay the deterministic
    core's job, so this is only a composition filter on what gets fetched."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        element_predicates=(ElementPredicate(quantifier="all", members=frozenset({"Ga", "N"})),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["elements"] == "Ga,N"


def test_retrieve_omits_elements_when_spec_has_no_required_elements():
    """With no composition requirement the adapter sends no `elements` filter, so
    the query is scoped only by the fields it asks for."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    MaterialsProjectAdapter(http_get=spy).retrieve(_spec())

    assert "elements" not in captured["params"]


def test_classify_routes_an_unqueryable_boolean_to_local():
    """The exclusive set: `is_magnetic` is retrievable (in FIELD_UNITS) but not
    queryable (not in PUSHABLE_PARAMS), so the adapter routes it to the local
    bucket — the deterministic filter must enforce it. A queryable boolean
    (`is_stable`) is the server's job and stays out of the local bucket."""
    spec = TriageSpec(
        boolean_constraints=(
            BooleanConstraint(property_name="is_stable", required=True),
            BooleanConstraint(property_name="is_magnetic", required=True),
        ),
    )

    routing = MaterialsProjectAdapter(http_get=lambda *a: {}).classify_predicates(spec)

    assert BooleanConstraint(property_name="is_magnetic", required=True) in routing.local_booleans
    assert BooleanConstraint(property_name="is_stable", required=True) not in routing.local_booleans


def test_retrieve_excludes_forbidden_elements_server_side():
    """A "none"-quantifier ElementPredicate scopes the pool server-side via MP's
    `exclude_elements` param (sorted, comma-joined) — the mirror of `elements`."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        element_predicates=(ElementPredicate(quantifier="none", members=frozenset({"Pb", "Cd"})),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["exclude_elements"] == "Cd,Pb"


def test_retrieve_does_not_push_an_any_element_predicate():
    """MP has no OR-membership query param, so an "any"-quantifier predicate cannot be
    pushed — the adapter must not leak its members into `elements`/`exclude_elements`,
    which would wrongly over-restrict (AND) or exclude the pool server-side. It is not
    enforced locally either — that refocus is task 2c in docs/handoff.md."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        constraints=(Constraint(property_name="band_gap", min=1.0),),
        element_predicates=(ElementPredicate(quantifier="any", members=frozenset({"Fe", "Co"})),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert "elements" not in captured["params"]
    assert "exclude_elements" not in captured["params"]


def test_retrieve_pushes_a_boolean_constraint_in_the_vocabulary():
    """A BooleanConstraint on a field the adapter publishes (`is_stable`) is pushed
    as MP's same-named exact-match query param, lowercase `true`/`false`."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_stable", required=True),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["is_stable"] == "true"


def test_retrieve_does_not_push_a_retrievable_but_unqueryable_boolean():
    """`is_magnetic` is a retrievable field but NOT a /summary query param — pushing
    it returns HTTP 400. Gating on the pushable-param surface (not the retrievable
    vocabulary) keeps it out of the query. Nothing enforces it locally either —
    refocusing the local filter to cover such predicates is task 2c in docs/handoff.md."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_magnetic", required=True),)
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert "is_magnetic" not in captured["params"]


def test_retrieve_does_not_push_a_boolean_constraint_outside_the_vocabulary():
    """A BooleanConstraint on a field the adapter does not publish must not be sent
    as a query param — MP would silently ignore an unknown name, so pushing it would
    falsely imply server-side scoping. It is not enforced locally either — refocusing
    the local filter to cover such predicates is task 2c in docs/handoff.md."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_superconductor", required=True),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert "is_superconductor" not in captured["params"]


def test_retrieve_does_not_push_a_boolean_named_for_a_control_param():
    """`deprecated` is a real /summary query param (in PUSHABLE_PARAMS) but NOT a
    retrievable boolean property (absent from FIELD_UNITS). A BooleanConstraint that
    names it — property names pass through from LLM proposals verbatim — must not
    collide with the control param; the double-gate on FIELD_UNITS keeps it off the
    wire so `deprecated=true` is never emitted."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="deprecated", required=True),),
    )

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert "deprecated" not in captured["params"]


def test_retrieve_pushes_a_count_constraint_as_nelements_bounds():
    """A CountConstraint on composition cardinality is pushed as MP's inclusive
    `nelements_min`/`nelements_max` range params, shrinking the pool server-side."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(count=CountConstraint(min=2, max=3))

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["nelements_min"] == "2"
    assert captured["params"]["nelements_max"] == "3"


def test_retrieve_pushes_a_numeric_constraint_as_field_bounds():
    """A numeric Constraint on a field the adapter publishes is pushed as MP's
    inclusive `<field>_min`/`<field>_max` range params, so the API trims the pool
    instead of the _limit budget being spent on rows the bound would drop."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0, max=3.0),))

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["band_gap_min"] == "1.0"
    assert captured["params"]["band_gap_max"] == "3.0"


def test_retrieve_pushes_bulk_modulus_via_the_k_vrh_param():
    """The elastic moduli filter server-side via the Voigt-Reuss-Hill params (`k_vrh`
    for bulk, `g_vrh` for shear), NOT `<field>_min` — `bulk_modulus_min` isn't a real
    query param. The adapter maps the field to its VRH filter param; the local
    `_scalar` already collapses the returned VRH dict, so both sides agree."""
    captured: dict = {}

    def spy(url, params, headers):
        captured["params"] = params
        return {"data": [], "meta": {}}

    spec = TriageSpec(constraints=(Constraint(property_name="bulk_modulus", min=50.0),))

    MaterialsProjectAdapter(http_get=spy).retrieve(spec)

    assert captured["params"]["k_vrh_min"] == "50.0"
    assert "bulk_modulus_min" not in captured["params"]


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


# --- Live contract suite -----------------------------------------------------
# These are the SAFETY GUARANTEE of the trusted-adapter model: with no local
# backstop re-checking server-side filters, a param MP silently ignores would
# return unfiltered rows undetected. Each test sources its params from the real
# _query_params(spec) — so it verifies the exact name the adapter ships — then
# asserts every returned row actually satisfies the constraint. Because the
# params combine as conjunction and _query_params builds each independently,
# verifying each in isolation suffices; no combinatorial coverage needed.


def _live_rows(spec: TriageSpec, fields_back: list[str]) -> list[dict]:
    """Issue the adapter's own query for ``spec`` against the live API, requesting
    ``fields_back`` so the pushed filter can be checked on the response."""
    adapter = MaterialsProjectAdapter()
    params = dict(_query_params(spec))
    params["_fields"] = ",".join(fields_back)
    rows = adapter._http_get("/materials/summary/", params, {"X-API-KEY": adapter._api_key})["data"]
    assert rows, "live query returned no rows; cannot verify the param is honored"
    return rows


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_a_numeric_range_param():
    """MP applies the `<field>_min`/`<field>_max` the adapter emits: every returned
    row's value lands inside the requested window (an ignored param would leak
    out-of-range rows)."""
    spec = TriageSpec(constraints=(Constraint(property_name="band_gap", min=1.0, max=3.0),))

    rows = _live_rows(spec, ["band_gap"])

    assert all(1.0 <= r["band_gap"] <= 3.0 for r in rows if r.get("band_gap") is not None)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_a_boolean_param():
    """MP applies the same-named boolean exact-match param: every returned row is
    actually stable."""
    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_stable", required=True),)
    )

    rows = _live_rows(spec, ["is_stable"])

    assert all(r["is_stable"] is True for r in rows)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_exclude_elements():
    """MP applies `exclude_elements`: no returned row contains a forbidden element."""
    spec = TriageSpec(
        element_predicates=(ElementPredicate(quantifier="none", members=frozenset({"Pb"})),)
    )

    rows = _live_rows(spec, ["elements"])

    assert all("Pb" not in r["elements"] for r in rows)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_required_elements():
    """MP applies `elements` with AND-membership: every returned row contains all
    required elements."""
    spec = TriageSpec(
        element_predicates=(ElementPredicate(quantifier="all", members=frozenset({"Ga", "N"})),)
    )

    rows = _live_rows(spec, ["elements"])

    assert all({"Ga", "N"} <= set(r["elements"]) for r in rows)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_nelements_range():
    """MP applies `nelements_min`/`nelements_max`: every returned row has exactly the
    requested number of distinct elements."""
    spec = TriageSpec(count=CountConstraint(min=2, max=2))

    rows = _live_rows(spec, ["nelements"])

    assert all(r["nelements"] == 2 for r in rows)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_mp_honors_the_vrh_modulus_alias():
    """A `bulk_modulus` constraint pushes via the VRH alias `k_vrh_min` (not the
    non-existent `bulk_modulus_min`): every returned row's VRH bulk modulus clears
    the bound. Guards the one non-1:1 field→param mapping against silent ignore."""
    spec = TriageSpec(constraints=(Constraint(property_name="bulk_modulus", min=100.0),))

    rows = _live_rows(spec, ["bulk_modulus"])

    vrhs = [r["bulk_modulus"]["vrh"] for r in rows if r.get("bulk_modulus")]
    assert vrhs
    assert all(v >= 100.0 for v in vrhs)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("X_API_KEY"), reason="needs X_API_KEY for the live API")
def test_live_retrieve_with_an_unqueryable_boolean_does_not_400():
    """Regression for the is_magnetic 400 crash: constraining a retrievable-but-not-
    queryable boolean must NOT reach the wire as a param. retrieve() succeeds because
    the gate keeps is_magnetic local rather than pushing it (which 400s)."""
    spec = TriageSpec(
        boolean_constraints=(BooleanConstraint(property_name="is_magnetic", required=True),)
    )

    candidates = MaterialsProjectAdapter().retrieve(spec)

    assert candidates  # no HTTP 400 — the param was never sent


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
