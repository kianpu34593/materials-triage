"""The ``SourceAdapter`` interface every retrieval backend implements.

Retrieval is deterministic code — never the LLM — and is the pipeline's only
source of ground-truth numbers. Each adapter turns a resolved ``TriageSpec``
into provenance-tagged ``Candidate``s that flow straight into the hard-filter
and ranking stages.
"""

import abc

from materials_triage.core.schema import Candidate, TriageSpec


class SourceAdapter(abc.ABC):
    """Abstract base for a public-database retrieval backend.

    A concrete adapter must implement :meth:`retrieve`; the spec carries every
    fact the adapter needs (which properties matter, which composition to scope
    to), so the interface stays a single method.
    """

    @abc.abstractmethod
    def retrieve(self, spec: TriageSpec) -> list[Candidate]:
        """Return the candidates this source offers for ``spec``, each property
        carrying its :class:`~materials_triage.core.schema.Provenance`."""
