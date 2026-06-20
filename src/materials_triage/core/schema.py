"""Core pydantic data models for the triage pipeline.

Every scientific value flowing through the pipeline carries a ``Provenance`` so
that the output validator can confirm nothing was invented by the LLM.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


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


class Candidate(BaseModel):
    """A material returned by retrieval: its source-issued identity plus the
    canonical, provenance-tagged properties the filter and ranker read by name.
    """

    model_config = ConfigDict(frozen=True)

    identifier: str = Field(min_length=1)
    formula: str = Field(min_length=1)
    properties: Mapping[str, PropertyValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _freeze_properties(self) -> Self:
        if not isinstance(self.properties, MappingProxyType):
            frozen = MappingProxyType(dict(self.properties))
            object.__setattr__(self, "properties", frozen)
        return self

    @field_serializer("properties")
    def _serialize_properties(self, value: Mapping[str, PropertyValue]) -> dict[str, PropertyValue]:
        return dict(value)
