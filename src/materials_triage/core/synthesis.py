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


class CandidateNote(BaseModel):
    """A per-candidate one-line verdict bound to the candidate it is about: a short
    ``summary`` and an optional suitability ``caveat`` the numeric layer cannot
    express (e.g. a molecular solid like H2O/CO2 that matches the oxide filter
    numerically but cannot be deposited as a thin film). Like a claim, its
    ``record_id`` must resolve to a retrieved candidate — the validator enforces it,
    which is what keeps the note non-fabricated.
    """

    model_config = ConfigDict(frozen=True)

    #: The provenance ``record_id`` of the retrieved candidate this note is about.
    record_id: str
    #: A one-line summary of the candidate's fit for the goal.
    summary: str
    #: An optional suitability caveat; empty when the candidate has no flagged concern.
    caveat: str = ""

    @field_validator("record_id", "summary")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a candidate note needs a record_id and a non-empty summary")
        return value


class Synthesis(BaseModel):
    """The LLM's whole narrative emission: a PI-facing summary, the cited mechanistic
    claims behind the shortlist, and an optional per-candidate note (one-line summary +
    suitability caveat). The summary is the at-a-glance prose the PI view leads with;
    the claims are the auditable, per-candidate "why"; the notes annotate each presented
    candidate with fit and caveats."""

    model_config = ConfigDict(frozen=True)

    summary: str
    claims: tuple[GroundedClaim, ...] = ()
    candidate_notes: tuple[CandidateNote, ...] = ()

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
    materials deterministic retrieval actually returned — across BOTH the mechanistic
    claims and the per-candidate notes. An empty result means the narrative is fully
    grounded. Order-preserving (claims before notes) and de-duplicated."""
    valid = set(valid_record_ids)
    seen: set[str] = set()
    bad: list[str] = []
    cited_ids = [claim.record_id for claim in synthesis.claims]
    cited_ids += [note.record_id for note in synthesis.candidate_notes]
    for record_id in cited_ids:
        if record_id not in valid and record_id not in seen:
            seen.add(record_id)
            bad.append(record_id)
    return tuple(bad)
