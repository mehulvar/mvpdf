"""Saved web articles (.html) -> paginated PDF laid out by MuPDF, ready for the PDF pipeline.

The HTML is cleaned to a safe subset (html_clean), images are downloaded with strict limits,
and MuPDF's Story engine flows everything onto Letter pages with real link annotations.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from html import escape
from urllib.parse import unquote_to_bytes, urlparse

from .html_clean import PageInfo, clean_body, image_sources, page_info

PAGE_MARGIN = 54  # points (0.75 in)
MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 1600  # wider images are halved until they fit
MIN_IMAGE_PIXELS = 16  # smaller ones are spacers or trackers
FETCH_TIMEOUT_SECONDS = 10
FETCH_WORKERS = 8
JPEG_QUALITY = 85
TOC_LEVELS = (2, 4)  # h1 is the title; deeper levels clutter the outline
USER_AGENT = "Mozilla/5.0 (compatible; mvpdf/1.0)"

ARTICLE_CSS = """
body { font-family: serif; font-size: 11pt; line-height: 1.5; color: #1b1b1b; }
h1 { font-family: sans-serif; font-size: 22pt; line-height: 1.2; margin: 0 0 6pt 0; }
h2 { font-family: sans-serif; font-size: 16pt; margin: 16pt 0 4pt 0; }
h3 { font-family: sans-serif; font-size: 13pt; margin: 12pt 0 4pt 0; }
h4, h5, h6 { font-family: sans-serif; font-size: 11.5pt; font-weight: bold; }
p { margin: 0 0 8pt 0; }
a { color: #1a5fb4; text-decoration: none; }
blockquote { margin: 8pt 0 8pt 18pt; color: #444; font-style: italic; }
pre, code { font-family: monospace; font-size: 9pt; }
pre { background-color: #f4f4f4; padding: 6pt; }
figcaption { font-size: 9pt; color: #555; }
.mvpdf-byline { font-family: sans-serif; font-size: 9pt; color: #666; margin-bottom: 14pt; }
"""

Fetcher = Callable[[str, str], "bytes | None"]  # (url, referer) -> body or None


def _is_public_host(host: str) -> bool:
    """Refuse loopback/private/link-local targets, so a page can't make us probe the LAN."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    return bool(addresses) and all(addr.is_global for addr in addresses)


def fetch_url(url: str, referer: str) -> bytes | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if not _is_public_host(parsed.hostname):
        return None
    headers = {"User-Agent": USER_AGENT, **({"Referer": referer} if referer else {})}
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if len(data) <= MAX_IMAGE_BYTES else None


def decode_data_uri(uri: str) -> bytes | None:
    header, sep, payload = uri.partition(",")
    if not sep or not header.startswith("data:image/"):
        return None
    try:
        data = base64.b64decode(payload, validate=False) if header.endswith(";base64") \
            else unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None
    return data if len(data) <= MAX_IMAGE_BYTES else None


def normalize_image(data: bytes) -> tuple[bytes, str] | None:
    """Validate with MuPDF and return (bytes, extension) in a format every reader handles."""
    import pymupdf

    try:
        pix = pymupdf.Pixmap(data)
    except Exception:  # MuPDF raises its own error types (FzErrorFormat, ...) for bad images
        return None
    if min(pix.width, pix.height) < MIN_IMAGE_PIXELS:
        return None
    if data[:3] == b"\xff\xd8\xff" and pix.width <= MAX_IMAGE_PIXELS:
        return data, "jpg"
    if pix.colorspace not in (None, pymupdf.csRGB, pymupdf.csGRAY):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    while pix.width > MAX_IMAGE_PIXELS:
        pix.shrink(1)
    if pix.alpha:
        return pix.tobytes("png"), "png"
    return pix.tobytes("jpg", jpg_quality=JPEG_QUALITY), "jpg"


def load_images(sources: tuple[str, ...], referer: str, fetch: Fetcher) -> dict[str, tuple[bytes, str]]:
    def load(source: str) -> tuple[bytes, str] | None:
        raw = decode_data_uri(source) if source.startswith("data:") else fetch(source, referer)
        return normalize_image(raw) if raw else None

    wanted = sources[:MAX_IMAGES]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        loaded = list(pool.map(load, wanted))
    return {source: image for source, image in zip(wanted, loaded) if image}


def header_html(info: PageInfo, title: str) -> str:
    parts = [escape(info.author)] if info.author else []
    if info.source:
        host = urlparse(info.source).hostname or info.source
        parts.append(f'<a href="{escape(info.source)}">{escape(host.removeprefix("www."))}</a>')
    byline = f'<p class="mvpdf-byline">{" · ".join(parts)}</p>' if parts else ""
    return f"<h1>{escape(title)}</h1>{byline}"


def toc_from_headings(headings: list[tuple[int, str, int, float]]) -> list[list]:
    """(level, text, page 1-based, y) -> PyMuPDF TOC rows. Levels start at 1 and never skip."""
    toc, previous, base = [], 0, min((h[0] for h in headings), default=1)
    for level, text, page, y in headings:
        title = " ".join(text.split())
        if not title:
            continue
        lvl = min(level - base + 1, previous + 1)
        toc.append([lvl, title, page, {"kind": 1, "to": (0, y)}])  # kind 1 = LINK_GOTO
        previous = lvl
    return toc


def _layout(story, headings: list):  # -> pymupdf.Document (imported lazily)
    import pymupdf

    def record(pos) -> None:
        if TOC_LEVELS[0] <= (pos.heading or 0) <= TOC_LEVELS[1] and pos.open_close & 1 and pos.text:
            headings.append((pos.heading, pos.text, pos.page_num, pos.rect[1]))

    page = pymupdf.paper_rect("letter")
    content = page + (PAGE_MARGIN, PAGE_MARGIN, -PAGE_MARGIN, -PAGE_MARGIN)
    laid_out = story.write_with_links(lambda _num, _filled: (page, content, None), positionfn=record)
    return pymupdf.open("pdf", laid_out.tobytes())


def html_to_pdf(html: str, fallback_title: str, fetch: Fetcher = fetch_url):
    """Return (pymupdf.Document, PageInfo) for an HTML article."""
    import pymupdf

    if len(html.encode("utf-8", "ignore")) > MAX_HTML_BYTES:
        raise ValueError("HTML input is too large")
    info = page_info(html)
    title = info.title or fallback_title
    images = load_images(image_sources(html, info.source), info.source, fetch)
    archive = pymupdf.Archive()
    names = {}
    for index, (source, (data, ext)) in enumerate(images.items()):
        names[source] = f"img{index}.{ext}"
        archive.add(data, names[source])
    body = clean_body(html, info.source, names)
    if not body.strip():
        raise ValueError("The page has no readable content")
    story = pymupdf.Story(
        f"{header_html(info, title)}{body}",
        archive=archive,
        user_css=ARTICLE_CSS + "img { max-width: 100%; }",
    )
    headings: list[tuple[int, str, int, float]] = []
    doc = _layout(story, headings)
    doc.set_metadata({"title": title, "author": info.author})
    toc = toc_from_headings(headings)
    if toc:
        doc.set_toc([[lvl, text, page, {**dest, "to": pymupdf.Point(*dest["to"])}]
                     for lvl, text, page, dest in toc])
    return doc, info
