"""Hypothesis-layer data models for the triage pipeline.

The LLM proposes bridges from a scientist's fuzzy goal to a queryable
``TriageSpec``; these models are the contract that proposal must conform to.
Literature grounding rides along as ``Citation`` (the untrusted-DATA analog of
``Provenance``) so synthesis can cite it and the output validator can confirm
every reference resolves. Validation here is the gate: malformed LLM output is
rejected before it can reach the deterministic core.
"""

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    """A literature reference a hypothesis was grounded in.

    The untrusted-DATA counterpart of :class:`~materials_triage.core.schema.Provenance`:
    it names the specific public record (e.g. an OpenAlex work or a DOI) so a
    claim can cite it and the output validator can confirm it resolves.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
