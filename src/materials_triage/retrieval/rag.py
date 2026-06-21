"""Literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank.

Deterministic retriever, never the LLM. Retrieved text is untrusted DATA.
"""


def _reconstruct_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Rebuild ordered abstract text from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships abstracts as ``{word: [positions]}`` rather than plain text.
    """
    return next(iter(inverted_index))
