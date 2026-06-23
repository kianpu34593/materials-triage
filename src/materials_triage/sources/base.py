"""The ``SourceAdapter`` interface every retrieval backend implements.

Retrieval is deterministic code — never the LLM — and is the pipeline's only
source of ground-truth numbers. Each adapter turns a resolved ``TriageSpec``
into provenance-tagged ``Candidate``s that flow straight into the hard-filter
and ranking stages.
"""

import abc
from collections.abc import Mapping

from materials_triage.core.schema import Candidate, PredicateRouting, TriageSpec


class SourceAdapter(abc.ABC):
    """Abstract base for a public-database retrieval backend.

    A concrete adapter must implement :meth:`retrieve`; the spec carries every
    fact the adapter needs (which properties matter, which composition to scope
    to), so retrieval stays a single method. :meth:`property_vocabulary` lets the
    spec-building stages discover, from the very adapter that will retrieve, which
    property names are actually fetchable — so a hypothesis can only name
    properties this source returns (no silent missing-data wipeout downstream).
    """

    @abc.abstractmethod
    def retrieve(self, spec: TriageSpec) -> list[Candidate]:
        """Return the candidates this source offers for ``spec``, each property
        carrying its :class:`~materials_triage.core.schema.Provenance`."""

    def property_vocabulary(self) -> Mapping[str, str | None]:
        """The canonical retrievable property names this source exposes, mapped to
        their units (e.g. ``{"band_gap": "eV"}``; ``None`` = dimensionless). The
        spec-building prompt hands these to the LLM so a hypothesis names only
        properties ``retrieve`` will actually populate. The default is empty (a
        source that declares no vocabulary constrains nothing); a real source
        overrides it."""
        return {}

    def classify_predicates(self, spec: TriageSpec) -> PredicateRouting:
        """Route the spec's hard predicates between server-side push and local
        enforcement, so the deterministic filter knows which ones this source could
        neither push nor express (its *exclusive set*) and which it can't enforce at
        all (caveats). The default routes nothing local — a source with no declared
        capability is assumed to enforce its own pushes; a real source overrides this
        using its retrievable and queryable surfaces."""
        return PredicateRouting()
