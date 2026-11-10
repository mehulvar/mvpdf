"""Local, model-light summary and tag generation.

Both use the same embedding model as search, so the compiler needs no second
(large) generative model:

* Summary: extractive. Picks the sentences closest to the document centroid,
  with Maximal Marginal Relevance (MMR) so they don't repeat each other, and
  returns them in their original reading order.
* Tags: KeyBERT-style. Scores candidate 1-2 word phrases against the document
  centroid, again diversified with MMR.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Sequence

import numpy as np

from .chunking import split_sentences

Embedder = Callable[[Sequence[str]], np.ndarray]

SUMMARY_SENTENCES = 3
TAG_COUNT = 3
MMR_DIVERSITY = 0.5
MAX_TAG_CANDIDATES = 200
MIN_SENTENCE_CHARS = 25
MIN_WORD_CHARS = 3

_WORD = re.compile(r"[A-Za-z][A-Za-z\-']*")

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    even every few for from further had has have having he her here hers him his how i if
    in into is it its itself just like may me might more most much must my no nor not now
    of off on once one only or other our ours out over own per same she should so some
    such than that the their theirs them then there these they this those through thus to
    too under until up upon us use used using very via was we were what when where which
    while who whom why will with within without would yet you your yours many make makes
    made new well way ways get gets got however therefore often within across among
    """.split()
)


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def _centroid(vectors: np.ndarray) -> np.ndarray:
    mean = vectors.mean(axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm else mean


def mmr_select(
    candidates: np.ndarray,
    target: np.ndarray,
    k: int,
    diversity: float = MMR_DIVERSITY,
) -> tuple[int, ...]:
    """Pick k indices relevant to target but dissimilar to each other."""
    if len(candidates) == 0 or k <= 0:
        return ()
    cand = _normalize_rows(np.asarray(candidates, dtype=np.float32))
    relevance = cand @ target
    selected: list[int] = [int(np.argmax(relevance))]
    while len(selected) < min(k, len(cand)):
        redundancy = (cand @ cand[selected].T).max(axis=1)
        scores = (1 - diversity) * relevance - diversity * redundancy
        scores[selected] = -np.inf
        selected.append(int(np.argmax(scores)))
    return tuple(selected)


def summarize(
    chunks: Sequence[str],
    chunk_vectors: np.ndarray,
    embed: Embedder,
    n_sentences: int = SUMMARY_SENTENCES,
) -> str:
    """Extractive summary: the most central, non-redundant sentences."""
    sentences = tuple(
        s for chunk in chunks for s in split_sentences(chunk) if len(s) >= MIN_SENTENCE_CHARS
    )
    if not sentences:
        return " ".join(chunks)[:500]
    if len(sentences) <= n_sentences:
        return " ".join(sentences)
    target = _centroid(np.asarray(chunk_vectors, dtype=np.float32))
    picked = mmr_select(embed(list(sentences)), target, n_sentences)
    return " ".join(sentences[i] for i in sorted(picked))


def candidate_phrases(text: str, limit: int = MAX_TAG_CANDIDATES) -> tuple[str, ...]:
    """Frequent 1-2 word phrases that contain no stopwords."""
    words = [w.lower().strip("-'") for w in _WORD.findall(text)]
    counts: Counter[str] = Counter()
    for i, word in enumerate(words):
        if len(word) < MIN_WORD_CHARS or word in STOPWORDS:
            continue
        counts[word] += 1
        if i + 1 < len(words):
            nxt = words[i + 1]
            if len(nxt) >= MIN_WORD_CHARS and nxt not in STOPWORDS:
                counts[f"{word} {nxt}"] += 1
    return tuple(phrase for phrase, _ in counts.most_common(limit))


def _dedupe_overlapping(phrases: Sequence[str]) -> tuple[str, ...]:
    """Drop a tag whose words are already covered by an earlier tag."""
    kept: list[str] = []
    for phrase in phrases:
        words = set(phrase.split())
        if any(words <= set(k.split()) or set(k.split()) <= words for k in kept):
            continue
        kept.append(phrase)
    return tuple(kept)


def extract_tags(
    chunks: Sequence[str],
    chunk_vectors: np.ndarray,
    embed: Embedder,
    n_tags: int = TAG_COUNT,
) -> tuple[str, ...]:
    """KeyBERT-style keyphrases: phrases semantically closest to the whole document."""
    candidates = candidate_phrases(" ".join(chunks))
    if not candidates:
        return ()
    target = _centroid(np.asarray(chunk_vectors, dtype=np.float32))
    ranked = mmr_select(embed(list(candidates)), target, n_tags * 3)
    return _dedupe_overlapping([candidates[i] for i in ranked])[:n_tags]
