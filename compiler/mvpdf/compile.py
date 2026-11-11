"""Orchestrates PDF/TXT -> .mvpdf conversion: read, embed, summarise, package."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .document import DocumentParts, SearchIndex, build_document, write_document
from .insights import Embedder, extract_tags, summarize
from .sources import ProgressFn, read_source

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Same weights, exported to ONNX for Transformers.js in the browser.
DEFAULT_BROWSER_MODEL = "Xenova/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class CompileResult:
    output: Path
    page_count: int
    chunk_count: int
    summary: str
    tags: tuple[str, ...]
    size_bytes: int


def load_embedder(model_name: str = DEFAULT_MODEL) -> Embedder:
    """Load a sentence-transformers model and return a normalized-embedding function."""
    from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

    model = SentenceTransformer(model_name)

    def embed(texts):
        return model.encode(
            list(texts),
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype(np.float32)

    return embed


def compile_parts(
    parts: DocumentParts,
    *,
    output: Path,
    embed: Embedder,
    model: str = DEFAULT_MODEL,
    browser_model: str = DEFAULT_BROWSER_MODEL,
) -> CompileResult:
    texts = [c["text"] for c in parts.chunks]
    if not texts and not parts.pages:
        raise ValueError("Input contains no text to compile")

    search, summary, tags = None, "", ()
    if texts:
        vectors = embed(texts)
        if vectors.shape[0] != len(texts):
            raise ValueError("Embedder returned the wrong number of vectors")
        search = SearchIndex(vectors, model, browser_model)
        summary = summarize(texts, vectors, embed)
        tags = extract_tags(texts, vectors, embed)

    # Chunk text is recoverable from its flow item, so it isn't stored twice.
    slim_chunks = [{"item": c["item"], "heading": c["heading"]} for c in parts.chunks]
    data = build_document(
        replace(parts, chunks=slim_chunks), summary=summary, tags=tags, search=search
    )
    write_document(output, data)
    return CompileResult(output, len(parts.pages), len(texts), summary, tuple(tags), len(data))


def compile_file(
    source: Path,
    *,
    output: Path,
    embed: Embedder,
    model: str = DEFAULT_MODEL,
    browser_model: str = DEFAULT_BROWSER_MODEL,
    progress: ProgressFn = lambda done, total: None,
    source_url: str | None = None,
) -> CompileResult:
    parts = read_source(source, progress)
    if source_url:
        parts = replace(parts, source=source_url)
    return compile_parts(parts, output=output, embed=embed, model=model, browser_model=browser_model)
