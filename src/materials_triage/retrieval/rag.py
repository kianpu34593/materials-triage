"""Literature RAG (#17): OpenAlex abstract retrieval + BM25 re-rank.

Deterministic retriever, never the LLM. Retrieved text is untrusted DATA.
"""


def _reconstruct_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Rebuild ordered abstract text from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships abstracts as ``{word: [positions]}`` rather than plain text.
    A null index (no abstract on OpenAlex) reconstructs to an empty string.
    """
    if not inverted_index:
        return ""
    by_position = {pos: word for word, positions in inverted_index.items() for pos in positions}
    return " ".join(by_position[pos] for pos in sorted(by_position))
