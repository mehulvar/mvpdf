"""Split raw text into paragraph and sentence chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_CHUNK_CHARS = 40
MAX_CHUNK_CHARS = 1200
MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 10

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def split_sentences(text: str) -> tuple[str, ...]:
    """Split a block of text into sentences using punctuation boundaries."""
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return ()
    return tuple(s.strip() for s in _SENTENCE_END.split(cleaned) if s.strip())


def _split_long(paragraph: str, max_chars: int) -> tuple[str, ...]:
    """Break an oversized paragraph into pieces on sentence boundaries."""
    if len(paragraph) <= max_chars:
        return (paragraph,)
    pieces: list[str] = []
    current = ""
    for sentence in split_sentences(paragraph):
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return tuple(pieces)


def _merge_short(paragraphs: tuple[str, ...], min_chars: int) -> tuple[str, ...]:
    """Fold fragments shorter than min_chars (e.g. headings) into the next paragraph."""
    merged: list[str] = []
    carry = ""
    for paragraph in paragraphs:
        combined = f"{carry} {paragraph}".strip() if carry else paragraph
        if len(combined) < min_chars:
            carry = combined
            continue
        merged.append(combined)
        carry = ""
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return tuple(merged)


def _validate_bounds(min_chars: int, max_chars: int) -> None:
    if min_chars < 0 or max_chars <= 0 or min_chars > max_chars:
        raise ValueError("Require 0 <= min_chars <= max_chars and max_chars > 0")


def _blocks(text: str) -> tuple[str, ...]:
    raw = (normalize_whitespace(b) for b in _PARAGRAPH_BREAK.split(text.replace("\r\n", "\n")))
    return tuple(b for b in raw if b)


def _chunk_blocks(blocks: tuple[str, ...], min_chars: int, max_chars: int) -> tuple[str, ...]:
    merged = _merge_short(blocks, min_chars)
    return tuple(piece for p in merged for piece in _split_long(p, max_chars))


def split_paragraphs(
    text: str,
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> tuple[str, ...]:
    """Split text into clean paragraph chunks suitable for embedding."""
    _validate_bounds(min_chars, max_chars)
    return _chunk_blocks(_blocks(text), min_chars, max_chars)


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str | None = None


def is_heading(block: str) -> bool:
    """A short single line with no sentence punctuation, e.g. 'Storage and the Grid'."""
    return (
        len(block) <= MAX_HEADING_CHARS
        and len(block.split()) <= MAX_HEADING_WORDS
        and not block.endswith((".", "!", "?", ",", ";", ":"))
    )


def split_document(
    text: str,
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> tuple[Chunk, ...]:
    """Split text into chunks, tagging each with the nearest preceding heading."""
    _validate_bounds(min_chars, max_chars)
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for block in _blocks(text):
        if is_heading(block):
            sections.append((block, []))
        else:
            sections[-1][1].append(block)

    chunks = tuple(
        Chunk(piece, heading)
        for heading, body in sections
        for piece in _chunk_blocks(tuple(body), min_chars, max_chars)
    )
    # A document made only of short lines: fall back to plain paragraphs.
    return chunks or tuple(Chunk(p) for p in split_paragraphs(text, min_chars, max_chars))
