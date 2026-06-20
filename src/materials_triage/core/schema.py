"""Core pydantic data models for the triage pipeline.

Every scientific value flowing through the pipeline carries a ``Provenance`` so
that the output validator can confirm nothing was invented by the LLM.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Provenance(BaseModel):
    """Records where a scientific value came from.

    Immutable: once a value is tagged with its origin, that tag cannot change
    as it travels downstream.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    record_id: str = Field(min_length=1)


class PropertyValue(BaseModel):
    """A scientific value bound to the receipt proving where it came from.

    The number and its unit are one inseparable fact; every value carries a
    ``Provenance`` so the output validator can confirm nothing was invented.
    """

    model_config = ConfigDict(frozen=True)

    value: float | None = None
    unit: str
    missing: bool = False
    provenance: Provenance

    @model_validator(mode="after")
    def _presence_matches_value(self) -> Self:
        if self.missing and self.value is not None:
            raise ValueError("a missing value cannot carry a number")
        if not self.missing and self.value is None:
            raise ValueError("a present value must carry a number")
        return self
