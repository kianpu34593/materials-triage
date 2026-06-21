"""Tests for the literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank."""

from materials_triage.retrieval.rag import _reconstruct_abstract


def test_reconstruct_abstract_single_word():
    """A one-word inverted index reconstructs to that word."""
    assert _reconstruct_abstract({"Hello": [0]}) == "Hello"
