"""Core pydantic data models for the triage pipeline.

Every scientific value flowing through the pipeline carries a ``Provenance`` so
that the output validator can confirm nothing was invented by the LLM.
"""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from materials_triage.core.elements import ELEMENT_SYMBOLS


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


class Constraint(BaseModel):
    """A hard filter on one property: candidates outside the bound are dropped.

    A bound is expressed as an inclusive ``min`` and/or ``max``; the hard-filter
    stage reads these to gate candidates.
    """

    model_config = ConfigDict(frozen=True)

    property_name: str = Field(min_length=1)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds_some_property(self) -> Self:
        if self.min is None and self.max is None:
            raise ValueError("a constraint must set at least one of min or max")
        for name, bound in (("min", self.min), ("max", self.max)):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"a constraint's {name} must be a finite number")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("a constraint's min cannot exceed its max")
        return self


class RankingTarget(BaseModel):
    """A soft scoring preference on one property: the ranker normalises the
    property in the given direction and weights it in the weighted average.

    ``weight`` is a proportional share in ``(0, 1]``; across the targets of a
    single ``TriageSpec`` the weights must sum to 1.
    """

    model_config = ConfigDict(frozen=True)

    property_name: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]
    weight: float = Field(gt=0, le=1)
    on_missing: Literal["exclude", "penalize", "flag_only"] = "flag_only"


class TriageSpec(BaseModel):
    """The fully-resolved request the deterministic pipeline consumes:
    the hard filters it gates on, the soft targets it ranks by, and the
    composition rules that scope retrieval.
    """

    model_config = ConfigDict(frozen=True)

    constraints: tuple[Constraint, ...] = ()
    ranking_targets: tuple[RankingTarget, ...] = ()
    required_elements: frozenset[str] = frozenset()
    excluded_elements: frozenset[str] = frozenset()
    max_nelements: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _has_a_hard_filter(self) -> Self:
        if not self.constraints:
            raise ValueError("a spec must have at least one constraint")
        seen: set[str] = set()
        for c in self.constraints:
            if c.property_name in seen:
                raise ValueError(
                    f"property {c.property_name!r} is constrained more than once; "
                    "combine bounds into a single constraint"
                )
            seen.add(c.property_name)
        ranked: set[str] = set()
        for t in self.ranking_targets:
            if t.property_name in ranked:
                raise ValueError(
                    f"property {t.property_name!r} is ranked more than once; "
                    "a property should have a single ranking target"
                )
            ranked.add(t.property_name)
        if self.ranking_targets:
            total = math.fsum(t.weight for t in self.ranking_targets)
            if not math.isclose(total, 1.0, abs_tol=1e-9):
                raise ValueError(
                    f"ranking weights are proportional shares and must sum to 1, "
                    f"but they sum to {total}"
                )
        for name, symbols in (
            ("required_elements", self.required_elements),
            ("excluded_elements", self.excluded_elements),
        ):
            unknown = symbols - ELEMENT_SYMBOLS
            if unknown:
                raise ValueError(
                    f"{name} contains symbols that are not chemical elements: {sorted(unknown)}"
                )
        contradictory = self.required_elements & self.excluded_elements
        if contradictory:
            raise ValueError(
                f"elements cannot be both required and excluded: {sorted(contradictory)}"
            )
        if self.max_nelements is not None and len(self.required_elements) > self.max_nelements:
            raise ValueError(
                f"required_elements demands {len(self.required_elements)} distinct "
                f"elements but max_nelements caps it at {self.max_nelements}"
            )
        return self
