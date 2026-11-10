"""Assemble a complete .mvpdf document (META + pages + flow + vectors) into container bytes.

Block kinds:
    META  JSON  document metadata and the ids of every other top-level block (exactly one)
    PAGE  JSON  one fixed-layout page: size, graphics id, text layer, links
    SVGG  SVG   vector graphics for a page (glyphs as paths, images as mvpdf:<id> refs)
    IMAG  bytes a PNG/JPEG/GIF/WebP image, shared by every page and flow item that uses it
    FLOW  JSON  reading-order items (headings, paragraphs with link runs, images) + search chunks
    VECT  bytes little-endian float32 embeddings, one row per search chunk
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from .container import Block, pack

FORMAT_NAME = "mvpdf"
FORMAT_VERSION = 2
INCOMPRESSIBLE = frozenset({"IMAG"})  # already PNG/JPEG-compressed


@dataclass(frozen=True)
class SearchIndex:
    vectors: np.ndarray
    model: str
    browser_model: str


@dataclass(frozen=True)
class DocumentParts:
    title: str
    source: str
    flow: list[dict]
    chunks: list[dict]
    author: str = ""
    pages: tuple[Block, ...] = ()
    resources: tuple[Block, ...] = ()
    images: dict[str, str] = field(default_factory=dict)
    toc: list[dict] = field(default_factory=list)


def _json_block(kind: str, payload: dict) -> Block:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Block(kind, text.encode("utf-8"))


def vectors_to_bytes(vectors: np.ndarray) -> bytes:
    """Serialize a (count, dim) matrix as raw little-endian float32, row-major."""
    matrix = np.asarray(vectors)
    if matrix.ndim != 2:
        raise ValueError(f"Expected a 2-D matrix, got shape {matrix.shape}")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("Embeddings contain NaN or infinite values")
    return np.ascontiguousarray(matrix, dtype="<f4").tobytes()


def _search_meta(index: SearchIndex | None, chunk_count: int) -> tuple[dict | None, list[Block]]:
    if index is None or chunk_count == 0:
        return None, []
    if index.vectors.shape[0] != chunk_count:
        raise ValueError("Embedder returned the wrong number of vectors")
    vect = Block("VECT", vectors_to_bytes(index.vectors))
    meta = {
        "vectors": vect.id,
        "model": index.model,
        "browserModel": index.browser_model,
        "dim": int(index.vectors.shape[1]),
        "count": chunk_count,
        "dtype": "float32",
        "normalized": True,
        "pooling": "mean",
    }
    return meta, [vect]


def build_document(
    parts: DocumentParts,
    *,
    summary: str,
    tags: Sequence[str],
    search: SearchIndex | None,
    created: datetime | None = None,
) -> bytes:
    flow = _json_block("FLOW", {"items": parts.flow, "chunks": parts.chunks})
    search_meta, search_blocks = _search_meta(search, len(parts.chunks))
    meta = _json_block("META", {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "title": parts.title,
        "author": parts.author,
        "source": parts.source,
        "created": (created or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "summary": summary,
        "tags": list(tags),
        "pages": [p.id for p in parts.pages],
        "toc": parts.toc,
        "flow": flow.id,
        "images": parts.images,
        "search": search_meta,
    })
    blocks = [meta, flow, *parts.pages, *parts.resources, *search_blocks]
    return pack(blocks, incompressible=INCOMPRESSIBLE)


def write_document(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)  # atomic: a crash never leaves a half-written .mvpdf
    return path
