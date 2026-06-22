"""The Materials Project adapter — the one real retrieval source for v1.

It queries the (sandboxed) Materials Project summary API, unwraps the
``{"data": [...], "meta": {...}}`` envelope, and turns each SummaryDoc into a
provenance-tagged :class:`~materials_triage.core.schema.Candidate`. The HTTP
call is injected (``http_get``) so parsing is exercised fully offline; the real
transport is built lazily only when the adapter actually goes to the network.
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
        envelope = self._http_get("/materials/summary/", _query_params(spec), headers)
        return [_doc_to_candidate(doc) for doc in envelope["data"]]


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
    fields = list(_IDENTITY_FIELDS) + sorted(properties)
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


def _doc_to_candidate(doc: dict) -> Candidate:
    """Turn one SummaryDoc into a provenance-tagged Candidate."""
    material_id = doc["material_id"]
    # Every summary-endpoint property MP serves is DFT-computed (the endpoint
    # contract; corroborated per value by origins[].task_id), so the whole doc's
    # provenance is computational.
    provenance = Provenance(source=SOURCE_NAME, record_id=material_id, method="computational")
    properties = {
        name: _property_value(doc[name], unit, provenance)
        for name, unit in FIELD_UNITS.items()
        if name in doc  # a field never returned stays absent; a returned null is "missing"
    }
    return Candidate(
        identifier=material_id,
        formula=doc["formula_pretty"],
        properties=properties,
    )


def _property_value(raw: float | None, unit: str, provenance: Provenance) -> PropertyValue:
    """Wrap a raw payload number as a PropertyValue; a null becomes flagged-missing."""
    return PropertyValue(value=raw, unit=unit, missing=raw is None, provenance=provenance)
