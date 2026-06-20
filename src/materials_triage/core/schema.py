"""Core pydantic data models for the triage pipeline.

Every scientific value flowing through the pipeline carries a ``Provenance`` so
that the output validator can confirm nothing was invented by the LLM.
"""

from pydantic import BaseModel, ConfigDict


class Provenance(BaseModel):
    """Records where a scientific value came from.

    Immutable: once a value is tagged with its origin, that tag cannot change
    as it travels downstream.
    """

    model_config = ConfigDict(frozen=True)

    source: str
