"""The synthesis artifact: the LLM's grounded, cited narrative (workflow step 7).

The load-bearing rule holds here too: the LLM writes the *prose* (a PI-facing
summary and the mechanistic "why"), but every claim must cite a ``record_id`` of
a candidate that deterministic retrieval actually returned — it may not invent
materials or numbers. The output validator (step 8) enforces that each cited
``record_id`` resolves to retrieved provenance; this module is just the shape the
LLM fills and the validator checks.
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, field_validator


class GroundedClaim(BaseModel):
    """One sentence of narrative bound to the candidate it is grounded in.

    Immutable: a claim, once written and validated, travels unchanged into the
    rendered output and the audit trace.
    """

    model_config = ConfigDict(frozen=True)

    #: The claim text — a mechanistic or comparative statement about the candidate.
    text: str
    #: The provenance ``record_id`` of the retrieved candidate this claim is about.
    #: The output validator rejects any value that does not resolve to a retrieved
    #: candidate, which is what makes the narrative non-fabricated.
    record_id: str

    @field_validator("text", "record_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a grounded claim needs non-empty text and a record_id")
        return value


class Synthesis(BaseModel):
    """The LLM's whole narrative emission: a PI-facing summary plus the cited
    mechanistic claims behind the shortlist. The summary is the at-a-glance prose
    the PI view leads with; the claims are the auditable, per-candidate "why"."""

    model_config = ConfigDict(frozen=True)

    summary: str
    claims: tuple[GroundedClaim, ...] = ()

    @field_validator("summary")
    @classmethod
    def _summary_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("synthesis summary must be non-empty")
        return value


def ungrounded_record_ids(synthesis: Synthesis, valid_record_ids: Iterable[str]) -> tuple[str, ...]:
    """Return the claim ``record_id``s that do NOT resolve to a retrieved candidate.

    This is the grounding check the output validator (step 8) enforces and the
    synthesis retry loop uses to feed a correction back: the LLM may only cite
    materials deterministic retrieval actually returned. An empty result means the
    narrative is fully grounded. Order-preserving and de-duplicated."""
    valid = set(valid_record_ids)
    seen: set[str] = set()
    bad: list[str] = []
    for claim in synthesis.claims:
        if claim.record_id not in valid and claim.record_id not in seen:
            seen.add(claim.record_id)
            bad.append(claim.record_id)
    return tuple(bad)
