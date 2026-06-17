"""Unit tests for the pure scoring helpers in the RAG similarity evaluator.

These avoid any DB/Qdrant/model dependency — they exercise the math and text
handling that the grounding verdicts are built on.
"""
import numpy as np

from outreach.eval.rag_similarity import (
    GROUNDED_THRESHOLD,
    WEAK_THRESHOLD,
    cosine_sims,
    score_text,
    split_sentences,
    strip_tokens,
    verdict_for,
)


def test_strip_tokens_removes_personalization():
    # Tokens become a space (so neighbouring words aren't glued together).
    out = strip_tokens("Hi {{first_name}}, from {{company}}!")
    assert "{{" not in out and "first_name" not in out
    assert out.startswith("Hi") and out.endswith("!")
    assert "{{" not in strip_tokens("{{a}}{{b}} text")


def test_split_sentences_drops_short_fragments():
    body = "Our analytics platform cuts cycle time. Yes. It improves release visibility for teams."
    sents = split_sentences(body)
    assert "Yes." not in sents  # too short, dropped
    assert any("cycle time" in s for s in sents)
    assert any("release visibility" in s for s in sents)


def test_split_sentences_handles_newlines_and_tokens():
    body = "Hi {{first_name}},\n\nWe help engineering teams ship faster.\nLearn more today."
    sents = split_sentences(body)
    assert all("{{" not in s for s in sents)
    assert len(sents) == 2


def test_cosine_sims_identical_vector_is_one():
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    sims = cosine_sims([1.0, 0.0, 0.0], matrix)
    assert sims[0] == 1.0
    assert sims[1] == 0.0


def test_cosine_sims_normalizes_unnormalized_input():
    # Un-normalised vectors should still yield a valid cosine of 1.0 when parallel.
    matrix = np.array([[2.0, 0.0]])
    sims = cosine_sims([5.0, 0.0], matrix)
    assert np.isclose(sims[0], 1.0)


def test_cosine_sims_empty_matrix():
    assert cosine_sims([1.0, 0.0], np.empty((0, 2))).size == 0


def test_cosine_sims_zero_vector_is_safe():
    sims = cosine_sims([0.0, 0.0], np.array([[1.0, 0.0]]))
    assert sims.tolist() == [0.0]


def test_score_text_picks_best_chunk():
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]])
    score = score_text([1.0, 0.0, 0.0], matrix, top_k=2)
    assert score.best_index == 0
    assert np.isclose(score.max_sim, 1.0)
    assert score.mean_top_k <= score.max_sim


def test_score_text_no_chunks():
    score = score_text([1.0, 0.0], np.empty((0, 2)))
    assert score.best_index == -1
    assert score.max_sim == 0.0


def test_verdict_thresholds():
    assert verdict_for(GROUNDED_THRESHOLD + 0.1) == "grounded"
    assert verdict_for((GROUNDED_THRESHOLD + WEAK_THRESHOLD) / 2) == "weak"
    assert verdict_for(WEAK_THRESHOLD - 0.1) == "possible_hallucination"


def test_verdict_custom_thresholds():
    assert verdict_for(0.5, grounded=0.6, weak=0.4) == "weak"
    assert verdict_for(0.7, grounded=0.6, weak=0.4) == "grounded"
