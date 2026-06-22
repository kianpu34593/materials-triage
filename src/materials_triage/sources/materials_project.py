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

    def property_vocabulary(self) -> Mapping[str, str]:
        """The summary-API properties this adapter can populate, mapped to their
        units — exactly the keys ``_doc_to_candidate`` parses, so a hypothesis
        built from this vocabulary names only properties ``retrieve`` returns."""
        return FIELD_UNITS


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
    if spec.required_elements:
        # Composition scoping is pushed server-side; the numeric bounds stay with
        # apply_hard_filters, which remains the authority on what survives.
        params["elements"] = ",".join(sorted(spec.required_elements))
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


def _doc_to_candidate(doc: dict) -> Candidate:
    """Turn one SummaryDoc into a provenance-tagged Candidate."""
    material_id = doc["material_id"]
    provenance = Provenance(source=SOURCE_NAME, record_id=material_id)
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


def _scalar(raw: float | dict | None) -> float | None:
    """Collapse a raw payload value to a scalar. The MP summary API returns the
    elastic moduli (bulk_modulus, shear_modulus) not as a number but as a
    Voigt-Reuss-Hill dict ``{"voigt": .., "reuss": .., "vrh": ..}``; the VRH
    average is the standard single value to rank/filter on, so a dict collapses to
    its ``vrh`` entry. Plain numbers pass through; a null stays null (missing)."""
    if isinstance(raw, dict):
        return raw.get("vrh")
    return raw


def _property_value(raw: float | dict | None, unit: str, provenance: Provenance) -> PropertyValue:
    """Wrap a raw payload value as a PropertyValue; a null (or a modulus dict with
    no VRH average) becomes flagged-missing."""
    value = _scalar(raw)
    return PropertyValue(value=value, unit=unit, missing=value is None, provenance=provenance)
