"""Build the reading-order "flow" (headings, paragraphs with links, images) from PDF pages.

The flow powers the reflow view and semantic search. Each item remembers its page and
bounding box so a search hit can also be highlighted on the original page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .container import Block
from .pdf_pages import ALLOWED_IMAGE_TYPES, PRECISION, clean_text, safe_uri

HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 120
MAX_HEADING_LINES = 2
MAX_HEADING_LEVELS = 3
MIN_IMAGE_POINTS = 48  # smaller images are usually bullets, rules or logos
MARGIN_FRACTION = 0.08  # top/bottom band where running headers and footers live
MIN_REPEAT_PAGES = 2
BOLD_FLAG = 16

_DIGITS = re.compile(r"\d+")
_EXT_MIME = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "gif": "image/gif"}


@dataclass(frozen=True)
class Run:
    text: str
    link: tuple = ()  # ("uri", url) or ("page", index, y)

    def to_json(self) -> dict:
        out: dict = {"t": self.text}
        if self.link and self.link[0] == "uri":
            out["uri"] = self.link[1]
        elif self.link:
            out["page"], out["y"] = self.link[1], self.link[2]
        return out


@dataclass(frozen=True)
class RawText:
    page: int
    bbox: tuple[float, ...]
    runs: tuple[Run, ...]
    max_size: float
    all_bold: bool
    line_count: int
    in_margin: bool
    sizes: tuple[tuple[float, int], ...] = field(default=())  # (font size, char count)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).strip()


@dataclass(frozen=True)
class RawImage:
    page: int
    bbox: tuple[float, ...]
    block: Block
    mime: str


def _rect(values) -> tuple[float, ...]:
    return tuple(round(float(v), PRECISION) for v in values)


def _link_targets(links: list[dict], page_count: int) -> list[tuple[tuple[float, ...], tuple]]:
    targets = []
    for link in links:
        uri, page = safe_uri(link.get("uri")), link.get("page", -1)
        if uri:
            targets.append((tuple(link["from"]), ("uri", uri)))
        elif isinstance(page, int) and 0 <= page < page_count:
            to = link.get("to")
            targets.append((tuple(link["from"]), ("page", page, round(to.y, 2) if to else 0.0)))
    return targets


def _link_at(bbox, targets) -> tuple:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for (x0, y0, x1, y1), target in targets:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return target
    return ()


def merge_runs(runs: list[Run]) -> tuple[Run, ...]:
    """Join neighbouring runs that share a link target."""
    merged: list[Run] = []
    for run in runs:
        if merged and merged[-1].link == run.link:
            merged[-1] = Run(merged[-1].text + run.text, run.link)
        else:
            merged.append(run)
    return tuple(merged)


def _line_runs(line: dict, targets) -> list[Run]:
    return [
        Run(clean_text(s["text"]), _link_at(s["bbox"], targets)) for s in line["spans"] if s["text"]
    ]


def _join_line(runs: list[Run], line_runs: list[Run]) -> list[Run]:
    """Append a line, de-hyphenating words split across lines ("infor-" + "mation")."""
    if not runs:
        return list(line_runs)
    prev, first = runs[-1], line_runs[0]
    if prev.text.endswith("-") and first.text[:1].islower():
        return [*runs[:-1], Run(prev.text[:-1], prev.link), *line_runs]
    return [*runs[:-1], Run(prev.text.rstrip() + " ", prev.link), *line_runs]


def _text_block(block: dict, page: int, page_height: float, targets) -> RawText | None:
    lines = [ln for ln in block.get("lines", []) if any(s["text"].strip() for s in ln["spans"])]
    if not lines:
        return None
    runs: list[Run] = []
    for line in lines:
        line_runs = _line_runs(line, targets)
        if line_runs:
            runs = _join_line(runs, line_runs)
    spans = [s for ln in lines for s in ln["spans"] if s["text"].strip()]
    y0, y1 = block["bbox"][1], block["bbox"][3]
    margin = page_height * MARGIN_FRACTION
    return RawText(
        page=page,
        bbox=_rect(block["bbox"]),
        runs=merge_runs(runs),
        max_size=max(s["size"] for s in spans),
        all_bold=all(s["flags"] & BOLD_FLAG for s in spans),
        line_count=len(lines),
        in_margin=y1 <= margin or y0 >= page_height - margin,
        sizes=tuple((round(s["size"], 1), len(s["text"])) for s in spans),
    )


def _image_block(block: dict, page: int, to_png) -> RawImage | None:
    x0, y0, x1, y1 = block["bbox"]
    if min(x1 - x0, y1 - y0) < MIN_IMAGE_POINTS:
        return None
    data, mime = block.get("image", b""), _EXT_MIME.get(block.get("ext", ""))
    if mime not in ALLOWED_IMAGE_TYPES:
        data, mime = to_png(data), "image/png"  # e.g. JPEG 2000 / JBIG2: browsers can't show
    if not data:
        return None
    return RawImage(page, _rect(block["bbox"]), Block("IMAG", data), mime)


def page_items(page_dict: dict, links: list[dict], page: int, page_count: int, to_png) -> list:
    """Raw text/image items for one page, in MuPDF's reading order."""
    targets = _link_targets(links, page_count)
    height = page_dict.get("height", 0.0)
    items = []
    for block in page_dict.get("blocks", []):
        item = (
            _text_block(block, page, height, targets)
            if block.get("type") == 0
            else _image_block(block, page, to_png)
        )
        if item:
            items.append(item)
    return items


def body_font_size(items) -> float:
    """The font size most characters are set in."""
    weights: Counter[float] = Counter()
    for item in items:
        for size, chars in getattr(item, "sizes", ()):
            weights[size] += chars
    return weights.most_common(1)[0][0] if weights else 0.0


def _margin_key(item: RawText) -> str:
    return _DIGITS.sub("#", item.text.lower())


def running_furniture(items) -> frozenset[str]:
    """Header/footer texts (page numbers normalised) repeated on many pages."""
    pages: dict[str, set[int]] = {}
    for item in items:
        if isinstance(item, RawText) and item.in_margin:
            pages.setdefault(_margin_key(item), set()).add(item.page)
    return frozenset(k for k, p in pages.items() if len(p) >= MIN_REPEAT_PAGES)


def _is_heading(item: RawText, body: float) -> bool:
    short = len(item.text) <= MAX_HEADING_CHARS and item.line_count <= MAX_HEADING_LINES
    larger = body > 0 and item.max_size >= body * HEADING_SIZE_RATIO
    bold_label = item.all_bold and not item.text.endswith((".", ",", ";", ":"))
    return short and (larger or bold_label)


def heading_levels(sizes: list[float]) -> dict[float, int]:
    """Biggest heading size -> level 1, next -> 2, everything smaller -> 3."""
    ranked = sorted(set(sizes), reverse=True)
    return {size: min(rank + 1, MAX_HEADING_LEVELS) for rank, size in enumerate(ranked)}


def build_flow(raw_items) -> tuple[list[dict], list[Block], dict[str, str]]:
    """Classify raw items into flow JSON. Returns (items, IMAG blocks, image mime map)."""
    body = body_font_size(raw_items)
    furniture = running_furniture(raw_items)
    kept = [
        i for i in raw_items
        if not (isinstance(i, RawText) and (not i.text or _margin_key(i) in furniture))
    ]
    heads = [i for i in kept if isinstance(i, RawText) and _is_heading(i, body)]
    levels = heading_levels([round(h.max_size, 1) for h in heads])
    head_ids = {id(h) for h in heads}

    flow, blocks, images = [], [], {}
    for item in kept:
        base = {"page": item.page, "bbox": list(item.bbox)}
        if isinstance(item, RawImage):
            blocks.append(item.block)
            images[item.block.id] = item.mime
            flow.append({"k": "img", "ref": item.block.id, **base})
        elif id(item) in head_ids:
            lvl = levels[round(item.max_size, 1)]
            flow.append({"k": "h", "lvl": lvl, "runs": [r.to_json() for r in item.runs], **base})
        else:
            flow.append({"k": "p", "runs": [r.to_json() for r in item.runs], **base})
    return flow, blocks, images


def item_text(item: dict) -> str:
    return "".join(run["t"] for run in item.get("runs", [])).strip()


def search_chunks(flow: list[dict], min_chars: int) -> list[dict]:
    """Paragraphs worth embedding, each tagged with the nearest heading above it."""
    chunks, heading = [], None
    for index, item in enumerate(flow):
        if item["k"] == "h":
            heading = item_text(item)
        elif item["k"] == "p" and len(item_text(item)) >= min_chars:
            chunks.append({"item": index, "heading": heading, "text": item_text(item)})
    return chunks
