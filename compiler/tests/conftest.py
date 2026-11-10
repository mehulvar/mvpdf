import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAKE_DIM = 64


def _word_vector(word: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "little")
    return np.random.default_rng(seed).standard_normal(FAKE_DIM)


def fake_embed(texts):
    """Deterministic bag-of-words embedder: shared words -> similar vectors."""
    rows = []
    for text in texts:
        words = [w.lower() for w in text.split() if w.isalpha()] or ["empty"]
        vec = np.sum([_word_vector(w) for w in words], axis=0)
        rows.append(vec / np.linalg.norm(vec))
    return np.asarray(rows, dtype=np.float32)


@pytest.fixture
def embed():
    return fake_embed


@pytest.fixture
def sample_text():
    return (Path(__file__).resolve().parents[2] / "samples" / "solar_energy.txt").read_text()


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    """The generated multi-page sample PDF (headings, links, image, TOC, footers)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "samples"))
    from make_sample_pdf import build

    return build(tmp_path_factory.mktemp("pdf") / "sample.pdf")
