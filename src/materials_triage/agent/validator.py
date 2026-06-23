"""The output validator — workflow step 8.

The final grounding gate before render: every material the output presents (each
ranked or excluded candidate) and every citation the narrative makes must resolve
to a candidate deterministic retrieval actually returned. This is the structural
guarantee that nothing fabricated reaches the scientist — the synthesis step
already retries on a grounding miss, so a violation here means a real contract
breach, and the validator refuses rather than render it.
"""

from collections.abc import Iterable

from materials_triage.core.schema import TriageResult
from materials_triage.core.synthesis import Synthesis, ungrounded_record_ids


class UngroundedOutputError(RuntimeError):
    """The output referenced a material or citation that does not resolve to
    retrieved provenance — an ungrounded artifact the validator refuses to render."""


def validate_output(
    result: TriageResult,
    synthesis: Synthesis | None,
    retrieved_ids: Iterable[str],
) -> None:
    """Raise :class:`UngroundedOutputError` unless every presented candidate and
    every narrative citation resolves to a retrieved record id; return ``None`` on
    a clean output. ``retrieved_ids`` is the set of identifiers deterministic
    retrieval returned (the only legitimate provenance)."""
    valid = set(retrieved_ids)

    presented = [sc.candidate.identifier for sc in result.ranked]
    presented += [ex.candidate.identifier for ex in result.excluded]
    ungrounded_candidates = sorted({i for i in presented if i not in valid})
    if ungrounded_candidates:
        raise UngroundedOutputError(
            f"presented candidates not in retrieved provenance: {', '.join(ungrounded_candidates)}"
        )

    if synthesis is not None:
        bad_citations = ungrounded_record_ids(synthesis, valid)
        if bad_citations:
            raise UngroundedOutputError(
                f"narrative cites unretrieved materials: {', '.join(bad_citations)}"
            )

    return None
