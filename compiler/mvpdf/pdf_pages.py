"""Turn each PDF page into the .mvpdf page model: vector graphics, a text layer, and links.

Graphics are rendered by MuPDF to SVG with every glyph as a vector path, so pages look
identical to the PDF at any zoom without shipping fonts. Embedded raster images are
pulled out of the SVG into separate content-addressed IMAG blocks (deduplicated across
pages) and replaced by `mvpdf:<id>` references.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from .container import Block

PRECISION = 2
ALLOWED_URI_SCHEMES = frozenset({"http", "https", "mailto"})
ALLOWED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
IMAGE_REF_PREFIX = "mvpdf:"

_DATA_URI = re.compile(r'xlink:href="data:(image/[a-z+.-]+);base64,([A-Za-z0-9+/=\s]+)"')
_DATA_TEXT_ATTR = re.compile(r'\sdata-text="[^"]*"')
_HORIZONTAL_TOLERANCE = 1e-3


@dataclass(frozen=True)
class PageResult:
    page_block: Block
    blocks: tuple[Block, ...]
    images: dict[str, str]  # IMAG block id -> mime type


def clean_text(text: str) -> str:
    """NFKC folds typographic ligatures (ﬁ, ﬃ) back into letters so search and copy work."""
    return unicodedata.normalize("NFKC", text)


def _r(value: float) -> float:
    return round(float(value), PRECISION)


def safe_uri(uri: str | None) -> str | None:
    """Keep only web/mail links; drop javascript:, file:, launch actions and the like."""
    if not uri:
        return None
    scheme = urlparse(uri.strip()).scheme.lower()
    return uri.strip() if scheme in ALLOWED_URI_SCHEMES else None


def extract_svg_images(svg: str) -> tuple[str, tuple[Block, ...], dict[str, str]]:
    """Replace inline base64 images with block references. Unsupported types are dropped."""
    blocks: list[Block] = []
    images: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        mime = match.group(1)
        try:
            data = base64.b64decode(re.sub(r"\s", "", match.group(2)), validate=True)
        except (binascii.Error, ValueError):
            return 'xlink:href=""'
        if mime not in ALLOWED_IMAGE_TYPES:
            return 'xlink:href=""'
        block = Block("IMAG", data)
        blocks.append(block)
        images[block.id] = mime
        return f'xlink:href="{IMAGE_REF_PREFIX}{block.id}"'

    cleaned = _DATA_URI.sub(replace, svg)
    # Glyph-level text annotations duplicate the text layer; dropping them saves ~30%.
    return _DATA_TEXT_ATTR.sub("", cleaned), tuple(blocks), images


def text_layer(page_dict: dict) -> list[list]:
    """Horizontal spans as [x, baseline_y, width, font_size, text] in page points."""
    spans = []
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > _HORIZONTAL_TOLERANCE or dx <= 0:
                continue  # rotated text still renders and is searchable, just not selectable
            for span in line.get("spans", []):
                text = clean_text(span.get("text", ""))
                if not text.strip():
                    continue
                x0, _y0, x1, _y1 = span["bbox"]
                baseline = span["origin"][1]
                spans.append([_r(x0), _r(baseline), _r(x1 - x0), _r(span["size"]), text])
    return spans


def page_links(links: list[dict], page_count: int) -> list[dict]:
    """External links (validated URIs) and internal jumps (page index + y)."""
    out = []
    for link in links:
        rect = [_r(v) for v in link["from"]]
        uri = safe_uri(link.get("uri"))
        target = link.get("page", -1)
        if uri:
            out.append({"r": rect, "uri": uri})
        elif isinstance(target, int) and 0 <= target < page_count:
            point = link.get("to")
            out.append({"r": rect, "page": target, "y": _r(point.y) if point else 0.0})
    return out


def convert_page(page, page_dict: dict, links: list[dict], page_count: int) -> PageResult:
    """Build the PAGE block (plus its SVGG graphics and IMAG resources) for one page."""
    svg, image_blocks, images = extract_svg_images(page.get_svg_image(text_as_path=True))
    graphics = Block("SVGG", svg.encode("utf-8"))
    model = {
        "w": _r(page.rect.width),
        "h": _r(page.rect.height),
        "svg": graphics.id,
        "text": text_layer(page_dict),
        "links": page_links(links, page_count),
    }
    page_block = Block("PAGE", json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode())
    return PageResult(page_block, (graphics, *image_blocks), images)
