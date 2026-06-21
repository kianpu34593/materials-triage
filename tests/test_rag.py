"""Tests for the literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank."""

import pytest

from materials_triage.core.schema import Provenance
from materials_triage.retrieval.rag import (
    LiteraturePassage,
    LiteratureRAG,
    OpenAlexFetcher,
    _parse_work,
    _rank,
    _reconstruct_abstract,
    _tokenize,
)


class _FakeHttpGet:
    """Records (url, params, headers) and returns a canned OpenAlex envelope."""

    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        return self.envelope


def _inverted(text):
    """Build an OpenAlex-style abstract_inverted_index from plain text."""
    index = {}
    for pos, word in enumerate(text.split()):
        index.setdefault(word, []).append(pos)
    return index


class _FakeFetcher:
    """Records its calls and returns canned OpenAlex work dicts (offline seam)."""

    def __init__(self, works):
        self.works = works
        self.calls = []

    def fetch(self, query, pool_size):
        self.calls.append((query, pool_size))
        return self.works


def _oer_work():
    return _openalex_work(
        id="https://openalex.org/W-oer",
        title="Oxygen evolution catalysis",
        abstract_inverted_index=_inverted("oxygen evolution catalyst for water splitting"),
    )


def _battery_work():
    return _openalex_work(
        id="https://openalex.org/W-bat",
        title="Lithium battery anodes",
        abstract_inverted_index=_inverted("graphite anode capacity in lithium ion cells"),
    )


def _pv_work():
    return _openalex_work(
        id="https://openalex.org/W-pv",
        title="Silicon photovoltaics",
        abstract_inverted_index=_inverted("solar cell efficiency in crystalline silicon"),
    )


def _passage(**overrides):
    """Build a valid LiteraturePassage, overriding individual fields per test."""
    fields = dict(
        provenance=Provenance(source="openalex", record_id="W1"),
        title="A study of perovskite oxides",
        authors=["Doe, J."],
        year=2020,
        venue="Nature Materials",
        doi="10.1/abc",
        text="some abstract text",
        missing=False,
        score=0.0,
    )
    fields.update(overrides)
    return LiteraturePassage(**fields)


def test_reconstruct_abstract_single_word():
    """A one-word inverted index reconstructs to that word."""
    assert _reconstruct_abstract({"Hello": [0]}) == "Hello"


def test_reconstruct_abstract_orders_by_position():
    """Words are emitted in position order, not dict order, space-joined."""
    assert _reconstruct_abstract({"world": [1], "Hello": [0]}) == "Hello world"


def test_reconstruct_abstract_repeats_word_at_each_position():
    """A word with multiple positions appears at each (OpenAlex's inverted index)."""
    assert _reconstruct_abstract({"the": [0, 2], "cat": [1]}) == "the cat the"


def test_reconstruct_abstract_none_index_is_empty():
    """A null inverted index (no abstract on OpenAlex) reconstructs to ""."""
    assert _reconstruct_abstract(None) == ""


def test_reconstruct_abstract_empty_index_is_empty():
    """An empty inverted index reconstructs to ""."""
    assert _reconstruct_abstract({}) == ""


def test_passage_missing_requires_empty_text():
    """A passage flagged missing cannot carry abstract text (honesty invariant)."""
    with pytest.raises(ValueError):
        _passage(missing=True, text="an abstract is present")


def test_passage_present_requires_nonempty_text():
    """A passage not flagged missing must carry abstract text (honesty invariant)."""
    with pytest.raises(ValueError):
        _passage(missing=False, text="")


def _openalex_work(**overrides):
    """A representative OpenAlex work record, overridable per test."""
    work = {
        "id": "https://openalex.org/W123",
        "title": "Perovskite oxides for oxygen evolution",
        "publication_year": 2021,
        "doi": "https://doi.org/10.1/abc",
        "authorships": [
            {"author": {"display_name": "Jane Doe"}},
            {"author": {"display_name": "Sam Roe"}},
        ],
        "primary_location": {"source": {"display_name": "Nature Materials"}},
        "abstract_inverted_index": {"Perovskites": [0], "catalyze": [1], "OER.": [2]},
    }
    work.update(overrides)
    return work


def test_parse_work_happy_path():
    """A full OpenAlex work parses into a fully-populated passage."""
    passage = _parse_work(_openalex_work())

    assert passage.provenance == Provenance(source="openalex", record_id="W123")
    assert passage.title == "Perovskite oxides for oxygen evolution"
    assert passage.authors == ["Jane Doe", "Sam Roe"]
    assert passage.year == 2021
    assert passage.venue == "Nature Materials"
    assert passage.doi == "10.1/abc"
    assert passage.text == "Perovskites catalyze OER."
    assert passage.missing is False
    assert passage.score == 0.0


def test_parse_work_no_abstract_is_flagged_missing():
    """A work with a null abstract index parses to empty text, flagged missing."""
    passage = _parse_work(_openalex_work(abstract_inverted_index=None))

    assert passage.text == ""
    assert passage.missing is True
    assert passage.title == "Perovskite oxides for oxygen evolution"


def test_parse_work_tolerates_ragged_metadata():
    """Missing doi/venue/year and empty authorships degrade to None/[], not errors."""
    sparse = {
        "id": "https://openalex.org/W9",
        "title": "Minimal record",
        "abstract_inverted_index": {"Hello": [0]},
    }
    passage = _parse_work(sparse)

    assert passage.authors == []
    assert passage.year is None
    assert passage.venue is None
    assert passage.doi is None
    assert passage.text == "Hello"


def test_rank_orders_by_relevance():
    """A passage sharing the query terms ranks above ones that don't."""
    relevant = _passage(
        provenance=Provenance(source="openalex", record_id="W-rel"),
        title="Oxygen evolution reaction",
        text="oxygen evolution catalyst materials for water splitting",
    )
    battery = _passage(
        provenance=Provenance(source="openalex", record_id="W-bat"),
        title="Lithium battery anodes",
        text="graphite anode capacity in lithium ion cells",
    )
    photovoltaic = _passage(
        provenance=Provenance(source="openalex", record_id="W-pv"),
        title="Silicon photovoltaics",
        text="solar cell efficiency in crystalline silicon devices",
    )

    ranked = _rank("oxygen evolution catalyst", [battery, relevant, photovoltaic])

    assert ranked[0].provenance.record_id == "W-rel"


def test_rank_counts_title_terms():
    """A query term present only in the title still lifts that passage."""
    title_hit = _passage(
        provenance=Provenance(source="openalex", record_id="W-title"),
        title="Perovskite solar absorbers",
        text="this abstract discusses unrelated thin-film deposition methods",
    )
    other_a = _passage(
        provenance=Provenance(source="openalex", record_id="W-a"),
        title="Graphite anodes",
        text="capacity fade in lithium ion battery cells over cycling",
    )
    other_b = _passage(
        provenance=Provenance(source="openalex", record_id="W-b"),
        title="Zeolite catalysis",
        text="acid site density governs cracking selectivity in zeolites",
    )

    ranked = _rank("perovskite", [other_a, title_hit, other_b])

    assert ranked[0].provenance.record_id == "W-title"


def test_rank_populates_score():
    """Ranked passages carry their BM25 score (nonzero for a real match)."""
    a = _passage(
        provenance=Provenance(source="openalex", record_id="W-a"), text="oxygen evolution catalyst"
    )
    b = _passage(
        provenance=Provenance(source="openalex", record_id="W-b"), text="lithium battery anode"
    )
    c = _passage(
        provenance=Provenance(source="openalex", record_id="W-c"), text="silicon solar cell"
    )

    ranked = _rank("oxygen evolution", [a, b, c])

    assert ranked[0].score > 0.0


def test_rank_empty_pool_returns_empty():
    """Ranking an empty pool yields an empty list."""
    assert _rank("anything", []) == []


def test_rank_keeps_zero_relevance_in_stable_order():
    """A query matching nothing still returns all passages, score 0.0, input order."""
    first = _passage(
        provenance=Provenance(source="openalex", record_id="W-1"), text="alpha beta gamma"
    )
    second = _passage(
        provenance=Provenance(source="openalex", record_id="W-2"), text="delta epsilon zeta"
    )
    third = _passage(
        provenance=Provenance(source="openalex", record_id="W-3"), text="eta theta iota"
    )

    ranked = _rank("nonexistentterm", [first, second, third])

    assert [p.provenance.record_id for p in ranked] == ["W-1", "W-2", "W-3"]
    assert all(p.score == 0.0 for p in ranked)


def test_tokenize_keeps_integer_formulas_intact():
    """Integer-subscript formulas stay single tokens (lowercased), not split."""
    assert _tokenize("TiO2 LiFePO4 Li2CO3") == ["tio2", "lifepo4", "li2co3"]


def test_tokenize_keeps_decimal_stoichiometry_and_values_intact():
    """Decimal subscripts (doped perovskites) and decimal values are not split."""
    assert _tokenize("La0.6Sr0.4CoO3 has a 3.5 eV gap") == [
        "la0.6sr0.4coo3",
        "has",
        "a",
        "3.5",
        "ev",
        "gap",
    ]


def test_tokenize_strips_sentence_punctuation():
    """Trailing punctuation and hyphens are split off, not glued into tokens."""
    assert _tokenize("thin-film OER.") == ["thin", "film", "oer"]


def test_search_returns_ranked_passages():
    """search() fetches, parses, and BM25-ranks into attributed passages."""
    fetcher = _FakeFetcher([_battery_work(), _oer_work(), _pv_work()])
    rag = LiteratureRAG(fetcher)

    results = rag.search("oxygen evolution catalyst", k=3)

    assert len(results) == 3
    assert all(isinstance(r, LiteraturePassage) for r in results)
    assert results[0].provenance.record_id == "W-oer"


def test_search_respects_k():
    """search() returns at most k passages."""
    fetcher = _FakeFetcher([_battery_work(), _oer_work(), _pv_work()])
    results = LiteratureRAG(fetcher).search("oxygen evolution", k=2)
    assert len(results) == 2


def test_search_is_best_first():
    """Returned passages are ordered by non-increasing BM25 score."""
    fetcher = _FakeFetcher([_battery_work(), _oer_work(), _pv_work()])
    results = LiteratureRAG(fetcher).search("oxygen evolution catalyst", k=3)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_passes_default_pool_size_to_fetcher():
    """search() requests the coarse pool from the fetcher (default 200)."""
    fetcher = _FakeFetcher([_oer_work()])
    LiteratureRAG(fetcher).search("oxygen", k=1)
    assert fetcher.calls == [("oxygen", 200)]


def test_search_uses_configured_pool_size():
    """A custom pool_size is forwarded to the fetcher."""
    fetcher = _FakeFetcher([_oer_work()])
    LiteratureRAG(fetcher, pool_size=50).search("oxygen", k=1)
    assert fetcher.calls == [("oxygen", 50)]


def test_search_empty_fetch_returns_empty():
    """An empty pool yields no passages."""
    assert LiteratureRAG(_FakeFetcher([])).search("anything", k=10) == []


def test_search_includes_missing_abstract_works_ranked_on_title():
    """Works with no abstract are kept, flagged missing, and rank on their title."""
    no_abstract = _openalex_work(
        id="https://openalex.org/W-noabs",
        title="oxygen evolution catalyst on perovskite surfaces",
        abstract_inverted_index=None,
    )
    fetcher = _FakeFetcher([_battery_work(), no_abstract, _pv_work()])

    results = LiteratureRAG(fetcher).search("oxygen evolution catalyst", k=3)

    by_id = {r.provenance.record_id: r for r in results}
    assert "W-noabs" in by_id
    assert by_id["W-noabs"].missing is True
    assert by_id["W-noabs"].text == ""
    assert results[0].provenance.record_id == "W-noabs"


def test_openalex_fetcher_queries_works_endpoint_and_returns_results():
    """fetch() hits /works with search/per-page/select and unwraps the results list."""
    works = [_oer_work(), _battery_work()]
    http = _FakeHttpGet({"results": works, "meta": {"count": 2}})

    result = OpenAlexFetcher(http_get=http).fetch("oxygen evolution", pool_size=25)

    assert result == works
    url, params, _headers = http.calls[0]
    assert url == "/works"
    assert params["search"] == "oxygen evolution"
    assert params["per-page"] == "25"
    assert "abstract_inverted_index" in params["select"]


def test_openalex_fetcher_sends_polite_mailto_and_user_agent():
    """A configured mailto rides in both the User-Agent header and the query."""
    http = _FakeHttpGet({"results": []})

    OpenAlexFetcher(http_get=http, mailto="me@example.com").fetch("x", pool_size=1)

    _url, params, headers = http.calls[0]
    assert params["mailto"] == "me@example.com"
    assert "me@example.com" in headers["User-Agent"]


def test_openalex_fetcher_omits_mailto_when_unset():
    """With no mailto, no mailto param is sent and the User-Agent stays generic."""
    http = _FakeHttpGet({"results": []})

    OpenAlexFetcher(http_get=http, mailto="").fetch("x", pool_size=1)

    _url, params, headers = http.calls[0]
    assert "mailto" not in params
    assert "mailto" not in headers["User-Agent"]


@pytest.mark.live
def test_openalex_fetcher_live_returns_works():
    """Live OpenAlex call returns work records of the expected shape."""
    results = OpenAlexFetcher().fetch("perovskite oxygen evolution catalyst", pool_size=5)

    assert len(results) > 0
    assert "id" in results[0]
    assert "abstract_inverted_index" in results[0]
