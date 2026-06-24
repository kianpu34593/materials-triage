"""The ``SourceAdapter`` interface every retrieval backend implements.

Retrieval is deterministic code — never the LLM — and is the pipeline's only
source of ground-truth numbers. Each adapter turns a resolved ``TriageSpec``
into provenance-tagged ``Candidate``s that flow straight into the hard-filter
and ranking stages.
"""

import abc
from collections.abc import Mapping

from materials_triage.core.schema import PredicateRouting, RetrievalResult, TriageSpec


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
    def retrieve(self, spec: TriageSpec) -> RetrievalResult:
        """Return a :class:`~materials_triage.core.schema.RetrievalResult`: the
        candidates this source offers for ``spec`` (each property carrying its
        :class:`~materials_triage.core.schema.Provenance`) plus any run-level
        ``caveats`` the I/O loop must surface — e.g. the set was capped at a page
        ceiling, so the returned candidates are an incomplete subset of the match."""

    def property_vocabulary(self) -> Mapping[str, str | None]:
        """The canonical retrievable property names this source exposes, mapped to
        their units (e.g. ``{"band_gap": "eV"}``; ``None`` = dimensionless). The
        spec-building prompt hands these to the LLM so a hypothesis names only
        properties ``retrieve`` will actually populate. The default is empty (a
        source that declares no vocabulary constrains nothing); a real source
        overrides it."""
        return {}

    def property_descriptions(self) -> Mapping[str, str]:
        """A one-line meaning for each retrievable property name (e.g.
        ``{"vbm": "Valence-band maximum … NOT a cell voltage"}``). Handed to the
        spec-building prompt alongside the units so the LLM picks proxies by *meaning*,
        not just by unit — preventing wrong-but-plausible picks (an ``eV`` field grabbed
        as "voltage"). The default is empty (a source may expose units without glosses);
        a real source overrides it."""
        return {}

    def unrankable_properties(self) -> frozenset[str]:
        """Property names that may be *filters* but never *ranking targets* — chiefly
        boolean flags (``is_stable``, ``is_magnetic``): every candidate surviving the
        filter holds the same value, so scoring it flattens the pool to one desirability.
        The hypothesis stage drops any ranking target naming one. The default is empty (a
        source that can't classify rankability constrains nothing); a real source
        overrides it."""
        return frozenset()

    def classify_predicates(self, spec: TriageSpec) -> PredicateRouting:
        """Route the spec's hard predicates between server-side push and local
        enforcement, so the deterministic filter knows which ones this source could
        neither push nor express (its *exclusive set*) and which it can't enforce at
        all (caveats). The default routes nothing local — a source with no declared
        capability is assumed to enforce its own pushes; a real source overrides this
        using its retrievable and queryable surfaces."""
        return PredicateRouting()
