"""Read input files (PDF or plain text) into DocumentParts."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .chunking import MIN_CHUNK_CHARS, split_document
from .document import DocumentParts
from .pdf_flow import build_flow, page_items, search_chunks
from .pdf_pages import convert_page

SUPPORTED_SUFFIXES = (".pdf", ".txt")
ProgressFn = Callable[[int, int], None]


def _title_from_name(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip().title() or "Untitled"


def _to_png(data: bytes) -> bytes:
    """Re-encode an image MuPDF can read but browsers can't (JPX, JBIG2, ...)."""
    import pymupdf

    try:
        pix = pymupdf.Pixmap(data)
        if pix.alpha or pix.colorspace not in (None, pymupdf.csRGB, pymupdf.csGRAY):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("png")
    except (RuntimeError, ValueError):
        return b""


def _toc(doc) -> list[dict]:
    entries = []
    for level, title, page_number, *rest in doc.get_toc(simple=False):
        dest = rest[0] if rest else {}
        point = dest.get("to") if isinstance(dest, dict) else None
        if 1 <= page_number <= doc.page_count and title.strip():
            entries.append({
                "lvl": int(level),
                "title": title.strip(),
                "page": page_number - 1,
                "y": round(point.y, 2) if point else 0.0,
            })
    return entries


def _open_pdf(path: Path):
    import pymupdf  # heavy import, keep lazy

    try:
        doc = pymupdf.open(path)
    except (RuntimeError, ValueError) as err:
        raise ValueError(f"{path.name} is not a readable PDF: {err}") from err
    if not doc.is_pdf:
        raise ValueError(f"{path.name} is not a PDF")
    if doc.needs_pass:
        raise ValueError(f"{path.name} is password-protected; remove the password first")
    if doc.page_count == 0:
        raise ValueError(f"{path.name} has no pages")
    return doc


def read_pdf(path: Path, progress: ProgressFn = lambda done, total: None) -> DocumentParts:
    doc = _open_pdf(path)
    pages, resources, images, raw_items = [], [], {}, []
    with doc:
        for index, page in enumerate(doc):
            page_dict = page.get_text("dict", sort=True)
            links = page.get_links()
            result = convert_page(page, page_dict, links, doc.page_count)
            pages.append(result.page_block)
            resources.extend(result.blocks)
            images.update(result.images)
            raw_items.extend(page_items(page_dict, links, index, doc.page_count, _to_png))
            progress(index + 1, doc.page_count)
        meta, toc = doc.metadata or {}, _toc(doc)

    flow, flow_images, flow_mimes = build_flow(raw_items)
    return DocumentParts(
        title=(meta.get("title") or "").strip() or _title_from_name(path),
        author=(meta.get("author") or "").strip(),
        source=path.name,
        flow=flow,
        chunks=search_chunks(flow, MIN_CHUNK_CHARS),
        pages=tuple(pages),
        resources=(*resources, *flow_images),
        images={**images, **flow_mimes},
        toc=toc,
    )


def text_flow(text: str) -> list[dict]:
    """Plain text has no pages: headings and paragraphs only."""
    flow, heading = [], None
    for chunk in split_document(text):
        if chunk.heading and chunk.heading != heading:
            flow.append({"k": "h", "lvl": 1, "runs": [{"t": chunk.heading}]})
            heading = chunk.heading
        flow.append({"k": "p", "runs": [{"t": chunk.text}]})
    return flow


def read_txt(path: Path) -> DocumentParts:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(f"{path.name} is not valid UTF-8 text") from err
    flow = text_flow(text)
    if not flow:
        raise ValueError("Input contains no text to compile")
    return DocumentParts(
        title=_title_from_name(path),
        source=path.name,
        flow=flow,
        chunks=search_chunks(flow, min_chars=0),
    )


def read_source(path: Path, progress: ProgressFn = lambda done, total: None) -> DocumentParts:
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path, progress)
    if suffix == ".txt":
        return read_txt(path)
    raise ValueError(f"Unsupported input {suffix or '(none)'}; expected one of {SUPPORTED_SUFFIXES}")
