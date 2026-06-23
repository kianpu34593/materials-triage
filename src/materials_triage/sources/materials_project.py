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

from materials_triage.core.schema import Candidate, PropertyValue, Provenance, TriageSpec
from materials_triage.sources.base import SourceAdapter

#: A transport: ``(url, params, headers) -> parsed JSON envelope (dict)``.
HttpGet = Callable[[str, Mapping[str, str], Mapping[str, str]], dict]

SOURCE_NAME = "Materials Project"
DEFAULT_BASE_URL = "https://api.materialsproject.org"

#: SummaryDoc field → pinned unit. The payload carries no units, so the adapter
#: is the single place this tribal knowledge lives. Keys double as the canonical
#: property names used by the constraint/ranking stages — no rename layer.
FIELD_UNITS: Mapping[str, str] = {
    "band_gap": "eV",
    "energy_above_hull": "eV/atom",
    "formation_energy_per_atom": "eV/atom",
    "density": "g/cm³",
    "bulk_modulus": "GPa",
    "shear_modulus": "GPa",
}

#: SummaryDoc field → the name of the MP "origin" (computed property doc) whose
#: calculation produced it. ``origins`` is keyed by these internal doc names, not
#: by our field names, so this table is the bridge used to trace each value back
#: to its task (and thence its XC functional). Vendor knowledge, hence here in the
#: adapter and not the source-neutral core. A field with no matching origin in a
#: given doc simply has no traceable task → its functional stays unknown.
_FIELD_ORIGIN: Mapping[str, str] = {
    "band_gap": "electronic_structure",
    "formation_energy_per_atom": "energy",
    "energy_above_hull": "energy",
    "density": "structure",
    "bulk_modulus": "elasticity",
    "shear_modulus": "elasticity",
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


#: Identity fields always requested alongside the spec's properties.
_IDENTITY_FIELDS = ("material_id", "formula_pretty")
_DEFAULT_LIMIT = 100


def _query_params(spec: TriageSpec) -> dict[str, str]:
    """Derive the API query from the spec: request exactly the columns the filter
    and ranker will read (the union of constrained and ranked property names) plus
    the identity fields. Hard filtering itself stays the job of apply_hard_filters.
    """
    properties = {c.property_name for c in spec.constraints}
    properties |= {t.property_name for t in spec.ranking_targets}
    # origins is the per-property bridge to the task carrying each value's XC functional.
    fields = list(_IDENTITY_FIELDS) + ["origins"] + sorted(properties)
    params = {"_fields": ",".join(fields), "_limit": str(_DEFAULT_LIMIT)}
    # Composition scoping is pushed server-side; the numeric bounds stay with
    # apply_hard_filters, which remains the authority on what survives. Only the
    # "all" quantifier maps to MP's AND-semantics `elements` param — "any"/"none"
    # are honoured by the deterministic filter, not the query.
    must_have = sorted(
        e for p in spec.element_predicates if p.quantifier == "all" for e in p.members
    )
    if must_have:
        params["elements"] = ",".join(must_have)
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
    return Candidate(identifier=material_id, formula=doc["formula_pretty"], properties=properties)


def _property_value(raw: float | None, unit: str, provenance: Provenance) -> PropertyValue:
    """Wrap a raw payload number as a PropertyValue; a null becomes flagged-missing."""
    return PropertyValue(value=raw, unit=unit, missing=raw is None, provenance=provenance)
