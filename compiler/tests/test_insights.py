import numpy as np

from mvpdf.chunking import split_paragraphs
from mvpdf.insights import candidate_phrases, extract_tags, mmr_select, summarize


def test_mmr_prefers_relevant_then_diverse():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    candidates = np.array([[1, 0, 0], [0.99, 0.01, 0], [0.7, 0.7, 0]], dtype=np.float32)
    picked = mmr_select(candidates, target, k=2, diversity=0.7)
    assert picked[0] == 0
    assert picked[1] == 2  # near-duplicate (index 1) is skipped


def test_mmr_empty_and_k_larger_than_pool():
    target = np.ones(2, dtype=np.float32)
    assert mmr_select(np.empty((0, 2)), target, 3) == ()
    assert sorted(mmr_select(np.eye(2), target, 5)) == [0, 1]


def test_candidate_phrases_skip_stopwords_and_short_words():
    phrases = candidate_phrases("The solar panel and the solar panel of an ox.")
    assert phrases[0] in {"solar", "panel", "solar panel"}
    assert "the" not in phrases and "ox" not in phrases
    assert "panel of" not in phrases


def test_summary_returns_three_sentences_in_reading_order(sample_text, embed):
    chunks = split_paragraphs(sample_text)
    summary = summarize(chunks, embed(chunks), embed)
    sentences = [s for c in chunks for s in c.split(". ")]
    assert summary.count(".") >= 3
    first_sentence = summary.split(". ")[0]
    assert any(first_sentence in s for s in sentences)


def test_summary_short_document_returns_all_sentences(embed):
    chunks = ("Only one sentence lives in this tiny document.",)
    assert summarize(chunks, embed(chunks), embed) == chunks[0]


def test_summary_without_full_sentences_falls_back_to_text(embed):
    chunks = ("tiny",)
    assert summarize(chunks, embed(chunks), embed) == "tiny"


def test_extract_tags_returns_three_distinct_phrases(sample_text, embed):
    chunks = split_paragraphs(sample_text)
    tags = extract_tags(chunks, embed(chunks), embed)
    assert len(tags) == 3
    assert len(set(tags)) == 3
    for a in tags:
        for b in tags:
            if a != b:
                assert not set(a.split()) <= set(b.split())


def test_extract_tags_empty_when_no_candidates(embed):
    chunks = ("a an the of",)
    assert extract_tags(chunks, embed(chunks), embed) == ()
