"""Hypothesis-layer data models for the triage pipeline.

The LLM proposes bridges from a scientist's fuzzy goal to a queryable
``TriageSpec``; these models are the contract that proposal must conform to.
Literature grounding rides along as ``Citation`` (the untrusted-DATA analog of
``Provenance``) so synthesis can cite it and the output validator can confirm
every reference resolves. Validation here is the gate: malformed LLM output is
rejected before it can reach the deterministic core.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from materials_triage.core.elements import ELEMENT_SYMBOLS
from materials_triage.core.schema import Constraint, RankingTarget


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


class ElementRule(BaseModel):
    """The element_rule payload: one composition-scoping decision.

    ``mode`` is whether to require or exclude the named elements; the rule
    compiles to a ``TriageSpec`` ``required_elements`` / ``excluded_elements``
    set. Symbols are validated against the canonical element set so a hallucinated
    symbol is rejected before it can reach the spec.
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["require", "exclude"]
    elements: frozenset[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _elements_are_real(self) -> Self:
        unknown = self.elements - ELEMENT_SYMBOLS
        if unknown:
            raise ValueError(f"not chemical elements: {sorted(unknown)}")
        return self


class Proposal(BaseModel):
    """One cited bridge from a fuzzy goal to a queryable spec field.

    The LLM emits these; a deterministic compile step assembles the accepted ones
    into a ``TriageSpec``. ``kind`` discriminates which spec field the proposal
    compiles to and which payload it carries; ``rationale`` and ``citations`` are
    the grounding the human gate and output validator judge it by.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["constraint", "ranking_target", "element_rule"]
    constraint: Constraint | None = None
    ranking_target: RankingTarget | None = None
    element_rule: ElementRule | None = None
    rationale: str = Field(min_length=1)
    citations: tuple[Citation, ...] = ()
    confidence: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Self:
        # kind is the single source of truth for the payload: exactly the field it
        # names must be present, and no foreign payload may ride along — so the
        # compiler can dispatch on kind alone.
        payloads = {
            "constraint": self.constraint,
            "ranking_target": self.ranking_target,
            "element_rule": self.element_rule,
        }
        for name, value in payloads.items():
            if name == self.kind and value is None:
                raise ValueError(f"a {self.kind}-kind proposal must carry a {name}")
            if name != self.kind and value is not None:
                raise ValueError(f"a {self.kind}-kind proposal must not carry a {name}")
        return self
