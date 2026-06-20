"""Interface stubs for sources deferred past the v1 vertical slice.

Each is a real :class:`~materials_triage.sources.base.SourceAdapter` so the
pluggable, multi-source design is demonstrable today, but none retrieves: the
v1 slice sources data from Materials Project only, and a stub refuses rather
than fabricate. Cross-source merge is deferred with them.
"""

from materials_triage.core.schema import Candidate, TriageSpec
from materials_triage.sources.base import SourceAdapter


class _DeferredAdapter(SourceAdapter):
    """A named-but-unimplemented source; ``retrieve`` always refuses."""

    source_name: str = "deferred source"

    def retrieve(self, spec: TriageSpec) -> list[Candidate]:
        raise NotImplementedError(
            f"the {self.source_name} adapter is a v1 stub and retrieves nothing yet"
        )


class OqmdAdapter(_DeferredAdapter):
    source_name = "OQMD"


class AflowAdapter(_DeferredAdapter):
    source_name = "AFLOW"


class PubChemAdapter(_DeferredAdapter):
    source_name = "PubChem"


class IcsdAdapter(_DeferredAdapter):
    source_name = "ICSD"
