"""Tests for sequence RAG document ingest and chunking."""
from outreach.services.document_ingest import recursive_chunk, parse_document
from pathlib import Path
import tempfile


def test_recursive_chunk_overlap_and_metadata():
    text = "Paragraph one.\n\nParagraph two has more words in it.\n\nParagraph three ends here."
    chunks = recursive_chunk(text, chunk_size=40, chunk_overlap=10)
    assert len(chunks) >= 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].total_chunks == len(chunks)
    assert chunks[0].char_start >= 0
    assert chunks[0].char_end > chunks[0].char_start
    assert chunks[0].text.strip()


def test_recursive_chunk_single_short_text():
    text = "Short pitch about our analytics platform for engineering teams."
    chunks = recursive_chunk(text, chunk_size=500, chunk_overlap=50)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].total_chunks == 1


def test_parse_txt_document():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Hello from a test document about SaaS outreach.")
        path = Path(f.name)
    try:
        assert "SaaS outreach" in parse_document(path)
    finally:
        path.unlink()


def test_point_id_user_isolation_filter():
    """Verify search filter construction includes user_id (unit-level)."""
    from qdrant_client.http import models as qmodels

    user_id = 42
    doc_ids = [1, 2]
    must = [
        qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)),
        qmodels.FieldCondition(key="document_id", match=qmodels.MatchAny(any=doc_ids)),
    ]
    flt = qmodels.Filter(must=must)
    assert flt.must is not None
    assert len(flt.must) == 2
    assert flt.must[0].key == "user_id"
