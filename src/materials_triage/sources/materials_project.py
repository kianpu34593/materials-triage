"""The Materials Project adapter — the one real retrieval source for v1.

It queries the (sandboxed) Materials Project summary API, unwraps the
``{"data": [...], "meta": {...}}`` envelope, and turns each SummaryDoc into a
provenance-tagged :class:`~materials_triage.core.schema.Candidate`. The summary
payload never carries the XC functional, so a second batched call to the
``/materials/tasks/`` endpoint resolves each value's producing task (via the
doc's ``origins``) to its ``run_type`` and stamps it onto the value's
provenance as ``xc_functional``. This enrichment is best-effort: if the tasks
call fails the functional simply stays unknown rather than aborting retrieval.
The HTTP call is injected (``http_get``) so parsing is exercised fully offline;
the real transport is built lazily only when the adapter actually goes to the
network.
"""

import os
from collections.abc import Callable, Mapping

from materials_triage.core.schema import (
    Candidate,
    PredicateRouting,
    PropertyValue,
    Provenance,
    TriageSpec,
)
from materials_triage.sources._mp_fields import MP_FIELDS, PUSHABLE_PARAMS
from materials_triage.sources.base import SourceAdapter

#: A transport: ``(url, params, headers) -> parsed JSON envelope (dict)``.
HttpGet = Callable[[str, Mapping[str, str], Mapping[str, str]], dict]

SOURCE_NAME = "Materials Project"
DEFAULT_BASE_URL = "https://api.materialsproject.org"

#: SummaryDoc field → pinned unit (``None`` = dimensionless/count). The payload
#: carries no units, so this tribal knowledge is pinned out-of-band. Derived from the
#: generated MP_FIELDS table (built from the vendored schema by tools/gen_mp_vocab.py),
#: so the surface stays in lockstep with what the schema exposes — no hand drift. Keys
#: double as the canonical property names used by the constraint/ranking stages.
FIELD_UNITS: Mapping[str, str | None] = {name: meta["unit"] for name, meta in MP_FIELDS.items()}

#: SummaryDoc field → the name of the MP "origin" (computed property doc) whose
#: calculation produced it. ``origins`` is keyed by these internal doc names, not by
#: our field names, so this is the bridge used to trace each value back to its task
#: (and thence its XC functional). Only fields with a *traceable* origin appear —
#: derived from MP_FIELDS, dropping the ``origin: None`` fields (elasticity has no
#: origins[] entry, surface energies only method-named ones), so a value with no
#: traceable task simply has no entry and its functional stays unknown.
_FIELD_ORIGIN: Mapping[str, str] = {
    name: meta["origin"] for name, meta in MP_FIELDS.items() if meta["origin"] is not None
}


class MaterialsProjectAdapter(SourceAdapter):
    """Retrieve candidates from the Materials Project summary API."""

    def __init__(
        self,
        http_get: HttpGet | None = None,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        # Injecting http_get is the offline mode (tests pass a fake); the default
        # is the real network transport, built lazily so importing this module
        # (and every offline test) never needs ``requests`` installed.
        self._http_get = http_get or _requests_transport(base_url)
        self._api_key = api_key or os.environ.get("X_API_KEY", "")

    def retrieve(self, spec: TriageSpec) -> list[Candidate]:
        headers = {"X-API-KEY": self._api_key}
        docs = self._http_get("/materials/summary/", _query_params(spec), headers)["data"]
        # The functional isn't in the summary; resolve every retrieved value's task
        # and read its run_type in one batched call, then stamp each provenance.
        run_types = _fetch_run_types(self._http_get, headers, _page_task_ids(docs))
        return [_doc_to_candidate(doc, run_types) for doc in docs]

    def property_vocabulary(self) -> Mapping[str, str | None]:
        """The summary-API properties this adapter can populate, mapped to their
        units (``None`` = dimensionless) — exactly the keys ``_doc_to_candidate``
        parses, so a hypothesis built from this vocabulary names only properties
        ``retrieve`` returns. The table is committed static data (generated from the
        MP schema), never a live fetch, keeping every run replayable."""
        return FIELD_UNITS

    def classify_predicates(self, spec: TriageSpec) -> PredicateRouting:
        """Route the spec's hard predicates against this source's two committed
        surfaces — retrievable (``FIELD_UNITS``) and queryable (``PUSHABLE_PARAMS``).

        A predicate the source can push (retrievable ∩ queryable) is the server's
        job and appears in no bucket. A predicate that is retrievable but *not*
        queryable — the exclusive set — goes to a ``local`` bucket for the
        deterministic filter to enforce (e.g. ``is_magnetic``: returnable, but not a
        query param, so it can't be pushed yet its value comes back to check)."""
        local_booleans = tuple(
            b
            for b in spec.boolean_constraints
            if b.property_name in FIELD_UNITS and b.property_name not in PUSHABLE_PARAMS
        )
        # Composition is retrievable (`elements` comes back), but only `all`/`none` map
        # to query params (`elements`/`exclude_elements`); `any` has no MP OR-param, so
        # it's the exclusive set — enforced locally.
        local_element_predicates = tuple(
            p for p in spec.element_predicates if p.quantifier == "any"
        )
        # A constraint on a field MP can't return (not retrievable) can be neither
        # pushed nor enforced locally — record a loud caveat so the run doesn't silently
        # drop every candidate as missing-data (numeric) or quietly ignore it (boolean).
        unsupported = [
            c.property_name for c in spec.constraints if c.property_name not in FIELD_UNITS
        ] + [
            b.property_name for b in spec.boolean_constraints if b.property_name not in FIELD_UNITS
        ]
        caveats = tuple(
            f"constraint on '{name}' was not applied: "
            f"{SOURCE_NAME} provides no data or filter for it"
            for name in unsupported
        )
        return PredicateRouting(
            local_booleans=local_booleans,
            local_element_predicates=local_element_predicates,
            caveats=caveats,
        )


#: Identity fields always requested alongside the spec's properties.
_IDENTITY_FIELDS = ("material_id", "formula_pretty")
_DEFAULT_LIMIT = 100

#: Property name → the base of its MP range filter param, when it differs from the
#: field name. The elastic moduli are *returned* as ``bulk_modulus``/``shear_modulus``
#: (a Voigt-Reuss-Hill dict) but *filtered* via the VRH averages ``k_vrh``/``g_vrh`` —
#: ``bulk_modulus_min`` is not a real query param. The only non-1:1 field→param cases;
#: every other numeric field's range param is just ``<field>_min``/``<field>_max``.
_FILTER_PARAM_BASE = {"bulk_modulus": "k_vrh", "shear_modulus": "g_vrh"}


def _query_params(spec: TriageSpec) -> dict[str, str]:
    """Derive the API query from the spec: request exactly the columns the filter
    and ranker will read (the union of constrained and ranked property names) plus
    the identity fields, and push every hard filter MP can express server-side
    (numeric bounds, booleans, exclude_elements/elements, nelements range), each
    gated on PUSHABLE_PARAMS — the schema-derived set of real query-param names.
    """
    properties = {c.property_name for c in spec.constraints}
    properties |= {t.property_name for t in spec.ranking_targets}
    # origins is the per-property bridge to the task carrying each value's XC functional.
    fields = list(_IDENTITY_FIELDS) + ["origins"] + sorted(properties)
    params = {"_fields": ",".join(fields), "_limit": str(_DEFAULT_LIMIT)}
    # Push every hard filter MP can express, each gated on PUSHABLE_PARAMS — the
    # schema-derived set of real /summary query-param names. Server-side is the single
    # authority for what it pushes (the live contract suite verifies MP honours each
    # name); a predicate whose param isn't in the set is never sent, so it never 400s
    # the query. Such a predicate (element "any" has no MP OR-param; is_magnetic and
    # the like aren't query params at all) is currently enforced NOWHERE —
    # apply_hard_filters only handles numeric spec.constraints, and these are read only
    # here. Refocusing the local filter to enforce the DB-inexpressible predicates is
    # task 2c in docs/handoff.md; before this work "any" was already enforced nowhere.
    #
    # Composition: "all" → AND-membership `elements`, "none" → `exclude_elements`.
    must_have = sorted(
        e for p in spec.element_predicates if p.quantifier == "all" for e in p.members
    )
    if must_have and "elements" in PUSHABLE_PARAMS:
        params["elements"] = ",".join(must_have)
    must_lack = sorted(
        e for p in spec.element_predicates if p.quantifier == "none" for e in p.members
    )
    if must_lack and "exclude_elements" in PUSHABLE_PARAMS:
        params["exclude_elements"] = ",".join(must_lack)
    # Numeric bounds → inclusive <field>_min/<field>_max (or the VRH alias for the moduli).
    for c in spec.constraints:
        base = _FILTER_PARAM_BASE.get(c.property_name, c.property_name)
        if c.min is not None and f"{base}_min" in PUSHABLE_PARAMS:
            params[f"{base}_min"] = str(c.min)
        if c.max is not None and f"{base}_max" in PUSHABLE_PARAMS:
            params[f"{base}_max"] = str(c.max)
    # Booleans → same-named exact-match param. Double-gate on PUSHABLE_PARAMS *and*
    # FIELD_UNITS: BooleanConstraint.property_name is unrestricted (LLM proposals pass
    # through verbatim), so requiring it also be a real retrievable property field stops
    # a constraint named e.g. "deprecated"/"formula"/"_all_fields" — query params that
    # aren't boolean properties — from colliding with a non-boolean param.
    for b in spec.boolean_constraints:
        if b.property_name in PUSHABLE_PARAMS and b.property_name in FIELD_UNITS:
            params[b.property_name] = "true" if b.required else "false"
    # Element-count cap → inclusive nelements range.
    if spec.count is not None:
        if spec.count.min is not None and "nelements_min" in PUSHABLE_PARAMS:
            params["nelements_min"] = str(spec.count.min)
        if spec.count.max is not None and "nelements_max" in PUSHABLE_PARAMS:
            params["nelements_max"] = str(spec.count.max)
    return params


def _requests_transport(base_url: str) -> HttpGet:
    """Build the real HTTP transport. ``requests`` is imported only when the
    transport is actually called, so offline use never requires the dependency.
    """

    def transport(url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> dict:
        import requests

        response = requests.get(base_url + url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    return transport


def _origin_task_ids(origins: list[dict] | None) -> dict[str, str]:
    """Index a SummaryDoc's ``origins`` list by origin name → task_id.

    Each entry records which calculation produced one computed property doc; this
    lookup is the first step in tracing a value to its run. A doc with no origins
    (field unrequested or null) yields an empty lookup.
    """
    return {o["name"]: o["task_id"] for o in (origins or [])}


def _fetch_run_types(
    http_get: HttpGet, headers: Mapping[str, str], task_ids: list[str]
) -> dict[str, str]:
    """Map each task_id to its XC functional via one batched tasks call.

    The summary endpoint never carries the functional; it lives in the task doc's
    ``run_type``. All task_ids for a page are fetched in a single request. With no
    task_ids to trace, no call is made. A task that returns no run_type is omitted,
    so the caller treats its functional as unknown.

    This is best-effort enrichment layered on an already-complete summary result:
    if the tasks call fails (network error, non-200, malformed envelope) the map
    degrades to empty — every functional becomes unknown — rather than aborting an
    otherwise-valid retrieval.
    """
    unique = sorted(set(task_ids))
    if not unique:
        return {}
    params = {
        "task_ids": ",".join(unique),
        "_fields": "task_id,run_type",
        "_limit": str(len(unique)),
    }
    try:
        envelope = http_get("/materials/tasks/", params, headers)
        return {d["task_id"]: d["run_type"] for d in envelope["data"] if d.get("run_type")}
    except Exception:
        return {}


def _field_task_id(field: str, origin_index: Mapping[str, str]) -> str | None:
    """Resolve a summary field to the task_id that produced it, or ``None``.

    Bridges field → origin name (via ``_FIELD_ORIGIN``) → task_id (via the doc's
    origin index). A field that isn't task-derived, or whose origin doc wasn't
    computed for this material, has no task — so its functional stays unknown.
    """
    origin_name = _FIELD_ORIGIN.get(field)
    if origin_name is None:
        return None
    return origin_index.get(origin_name)


def _page_task_ids(docs: list[dict]) -> list[str]:
    """Every retrieved value's task_id across a page of docs — the batch whose
    run_types we fetch once. Fields with no traceable task contribute nothing.
    """
    ids: list[str] = []
    for doc in docs:
        origin_index = _origin_task_ids(doc.get("origins"))
        for name in FIELD_UNITS:
            if name in doc and (task_id := _field_task_id(name, origin_index)):
                ids.append(task_id)
    return ids


def _doc_to_candidate(doc: dict, run_types: Mapping[str, str]) -> Candidate:
    """Turn one SummaryDoc into a Candidate whose every value carries its own
    provenance — including the XC functional of the task that produced it.
    """
    material_id = doc["material_id"]
    origin_index = _origin_task_ids(doc.get("origins"))
    properties = {}
    for name, unit in FIELD_UNITS.items():
        if name not in doc:  # a field never returned stays absent; a returned null is "missing"
            continue
        task_id = _field_task_id(name, origin_index)
        # Every summary value MP serves is DFT-computed (the endpoint contract);
        # the functional is the producing task's run_type, or unknown if untraceable.
        provenance = Provenance(
            source=SOURCE_NAME,
            record_id=material_id,
            method="computational",
            xc_functional=run_types.get(task_id),
        )
        properties[name] = _property_value(doc[name], unit, provenance)
    return Candidate(
        identifier=material_id,
        formula=doc["formula_pretty"],
        properties=properties,
        elements=frozenset(doc.get("elements") or ()),
    )


def _property_value(
    raw: float | Mapping[str, float] | None, unit: str | None, provenance: Provenance
) -> PropertyValue:
    """Wrap a raw payload number as a PropertyValue; a null becomes flagged-missing."""
    value = _scalar(raw)
    return PropertyValue(value=value, unit=unit, missing=value is None, provenance=provenance)


def _scalar(raw: float | Mapping[str, float] | None) -> float | None:
    """Collapse a raw summary value to the single scalar the pipeline filters and
    ranks on. MP serves the elastic moduli (``bulk_modulus`` / ``shear_modulus``)
    as a Voigt-Reuss-Hill dict ``{"voigt", "reuss", "vrh"}`` — the only object-typed
    fields in the vocabulary — so take the VRH average. A plain number passes
    through; a null (or a dict missing ``vrh``) stays ``None`` and is flagged missing.
    """
    if isinstance(raw, Mapping):
        return raw.get("vrh")
    return raw
