"""Tests for RAG chunk selection and snippet formatting."""
from outreach.services.qdrant_store import RetrievedChunk
from outreach.services.rag_retriever import select_top_chunks
from outreach.services.sequence_generator import _format_rag_snippets, _rag_grounding_warnings


def _chunk(text: str, score: float, doc_id: int = 1, filename: str = "pitch.pdf", idx: int = 0):
    return RetrievedChunk(
        text=text, score=score, document_id=doc_id, filename=filename, chunk_index=idx,
    )


def test_select_top_chunks_filters_by_score():
    candidates = [
        _chunk("high relevance product info", 0.9, idx=0),
        _chunk("low relevance noise", 0.2, idx=1),
        _chunk("medium relevance pricing", 0.6, idx=2),
    ]
    selected = select_top_chunks(candidates, min_score=0.35, top_k=5)
    assert len(selected) == 2
    assert all(c.score >= 0.35 for c in selected)


def test_select_top_chunks_respects_top_k():
    candidates = [_chunk(f"chunk {i} content here", 0.9 - i * 0.05, idx=i) for i in range(8)]
    selected = select_top_chunks(candidates, min_score=0.35, top_k=5)
    assert len(selected) == 5


def test_select_top_chunks_dedupes_prefix():
    candidates = [
        _chunk("Coherent Analytics helps teams ship faster with release visibility.", 0.85, idx=0),
        _chunk("Coherent Analytics helps teams ship faster", 0.80, idx=1),
        _chunk("Pricing starts at $299 per month for starter plan.", 0.75, idx=2),
    ]
    selected = select_top_chunks(candidates, min_score=0.35, top_k=5)
    assert len(selected) == 2
    assert "Pricing" in selected[1].text


def test_format_rag_snippets_includes_filename_and_score():
    chunks = [_chunk("Release visibility for engineering teams.", 0.82, filename="pitch.pdf")]
    block = _format_rag_snippets(chunks)
    assert "pitch.pdf" in block
    assert "0.82" in block
    assert "Release visibility" in block


def test_format_rag_snippets_empty():
    assert "no uploaded documents" in _format_rag_snippets([])


def test_rag_grounding_warnings_with_chunks():
    chunks = [
        _chunk("a", 0.9, filename="a.pdf"),
        _chunk("b", 0.8, filename="b.md"),
    ]
    warnings = _rag_grounding_warnings(chunks, had_doc_ids=True)
    assert len(warnings) == 1
    assert "2 document chunks" in warnings[0]
    assert "a.pdf" in warnings[0]


def test_rag_grounding_warnings_no_chunks_but_docs():
    warnings = _rag_grounding_warnings([], had_doc_ids=True)
    assert "No matching document context" in warnings[0]
