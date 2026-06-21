"""Tests for the literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank."""

from materials_triage.retrieval.rag import _reconstruct_abstract


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
