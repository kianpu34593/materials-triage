"""Literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank.

Deterministic retriever, never the LLM. Retrieved text is untrusted DATA.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from materials_triage.core.schema import Provenance


class LiteraturePassage(BaseModel):
    """A public abstract retrieved for grounding, bound to its provenance.

    ``text`` is the whole abstract, kept isolated so the trust boundary can fence
    it as untrusted DATA. A passage with no abstract is flagged ``missing`` rather
    than dropped or fabricated; it stays rankable on its ``title``.
    """

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    text: str = ""
    missing: bool = False
    score: float = 0.0

    @model_validator(mode="after")
    def _missing_matches_text(self) -> Self:
        if self.missing and self.text != "":
            raise ValueError("a missing passage cannot carry abstract text")
        if not self.missing and self.text == "":
            raise ValueError("a present passage must carry abstract text")
        return self


def _reconstruct_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Rebuild ordered abstract text from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships abstracts as ``{word: [positions]}`` rather than plain text.
    A null index (no abstract on OpenAlex) reconstructs to an empty string.
    """
    if not inverted_index:
        return ""
    by_position = {pos: word for word, positions in inverted_index.items() for pos in positions}
    return " ".join(by_position[pos] for pos in sorted(by_position))


def _strip_prefix(value: str | None, prefix: str) -> str | None:
    """Drop a leading URL prefix OpenAlex wraps ids/DOIs in, if present."""
    if value is None:
        return None
    return value.removeprefix(prefix)


def _parse_work(work: dict) -> LiteraturePassage:
    """Convert one OpenAlex work record into a scoreless ``LiteraturePassage``."""
    text = _reconstruct_abstract(work.get("abstract_inverted_index"))
    venue = (work.get("primary_location") or {}).get("source") or {}
    return LiteraturePassage(
        provenance=Provenance(
            source="openalex",
            record_id=_strip_prefix(work["id"], "https://openalex.org/"),
        ),
        title=work["title"],
        authors=[a["author"]["display_name"] for a in work.get("authorships", [])],
        year=work.get("publication_year"),
        venue=venue.get("display_name"),
        doi=_strip_prefix(work.get("doi"), "https://doi.org/"),
        text=text,
        missing=text == "",
    )
