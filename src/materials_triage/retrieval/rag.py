"""Literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank.

Deterministic retriever, never the LLM. Retrieved text is untrusted DATA.
"""

import os
import re
from collections.abc import Callable, Mapping
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rank_bm25 import BM25Okapi

from materials_triage.core.schema import Provenance

#: A transport: ``(url, params, headers) -> parsed JSON envelope (dict)``.
HttpGet = Callable[[str, Mapping[str, str], Mapping[str, str]], dict]


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
        title=work.get("title") or "",
        authors=[
            name
            for a in work.get("authorships", [])
            if (name := (a.get("author") or {}).get("display_name"))
        ],
        year=work.get("publication_year"),
        venue=venue.get("display_name"),
        doi=_strip_prefix(work.get("doi"), "https://doi.org/"),
        text=text,
        missing=text == "",
    )


# Alphanumeric run, optionally joined across internal dots so decimal
# stoichiometry (La0.6Sr0.4CoO3) and decimal values (3.5) survive as one token,
# while trailing sentence punctuation (OER.) is left out.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    """Lowercase and split into tokens, keeping chemical formulas intact.

    Integer- and decimal-subscript formulas (TiO2, La0.6Sr0.4CoO3) and decimal
    numbers (3.5) stay single tokens; hyphens and trailing punctuation split.
    """
    return _TOKEN_RE.findall(text.lower())


def _rank(query: str, passages: list[LiteraturePassage]) -> list[LiteraturePassage]:
    """Re-rank passages by BM25 relevance to ``query`` over title + abstract.

    Returns best-first frozen copies carrying their BM25 ``score``; ties keep
    input order (deterministic).
    """
    if not passages:
        return []
    corpus = [_tokenize(f"{p.title} {p.text}") for p in passages]
    scores = BM25Okapi(corpus).get_scores(_tokenize(query))
    scored = [
        p.model_copy(update={"score": float(s)}) for p, s in zip(passages, scores, strict=True)
    ]
    return sorted(scored, key=lambda p: p.score, reverse=True)


class AbstractFetcher(Protocol):
    """Seam over the literature source: returns raw OpenAlex work dicts.

    Real implementations hit OpenAlex; tests/eval inject a fake returning cached
    JSON. Keeps the live network out of the deterministic parse + rank core.
    """

    def fetch(self, query: str, pool_size: int) -> list[dict]: ...


class LiteratureRAG:
    """Public abstracts retriever: fetch a coarse pool, then BM25 re-rank locally.

    Deterministic and LLM-free; query construction lives in the orchestrator, not
    here. Retrieved abstract text is untrusted DATA.
    """

    def __init__(self, fetcher: AbstractFetcher, pool_size: int = 200):
        self._fetcher = fetcher
        self._pool_size = pool_size

    def search(self, query: str, k: int = 10) -> list[LiteraturePassage]:
        """Return the top-``k`` passages most relevant to ``query``, best-first."""
        works = self._fetcher.fetch(query, self._pool_size)
        passages = [p for p in (_parse_work(w) for w in works) if p.title != "" or p.text != ""]
        return _rank(query, passages)[:k]


OPENALEX_BASE_URL = "https://api.openalex.org"

#: Work fields requested from OpenAlex — exactly what _parse_work reads, trimming
#: the otherwise large payload.
_SELECT_FIELDS = (
    "id",
    "title",
    "publication_year",
    "doi",
    "authorships",
    "primary_location",
    "abstract_inverted_index",
)


class OpenAlexFetcher:
    """The real :class:`AbstractFetcher`: a coarse keyword search over OpenAlex.

    The HTTP call is injected (``http_get``) so URL building is exercised offline;
    the real transport is built lazily so importing this module never needs
    ``requests``. A ``mailto`` joins OpenAlex's faster "polite pool".
    """

    def __init__(
        self,
        http_get: HttpGet | None = None,
        mailto: str | None = None,
        base_url: str = OPENALEX_BASE_URL,
    ) -> None:
        self._http_get = http_get or _requests_transport(base_url)
        self._mailto = mailto or os.environ.get("OPENALEX_MAILTO", "")

    def fetch(self, query: str, pool_size: int) -> list[dict]:
        params = {
            "search": query,
            "per-page": str(pool_size),
            "select": ",".join(_SELECT_FIELDS),
        }
        if self._mailto:
            params["mailto"] = self._mailto
        headers = {"User-Agent": _user_agent(self._mailto)}
        envelope = self._http_get("/works", params, headers)
        return envelope["results"]


def _user_agent(mailto: str) -> str:
    """A descriptive User-Agent; embedding a contact email is OpenAlex etiquette
    and also dodges the bare-urllib User-Agent bans some CDNs apply.
    """
    contact = f" (mailto:{mailto})" if mailto else ""
    return f"materials-triage/0.0.1{contact}"


def _requests_transport(base_url: str) -> HttpGet:
    """Build the real HTTP transport. ``requests`` is imported only when the
    transport is actually called, so offline use never requires the dependency.
    """

    def transport(url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> dict:
        import requests

        response = requests.get(base_url + url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    return transport
