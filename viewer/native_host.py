"""Chrome native-messaging host for the "Save as .mvpdf" browser extension.

Chrome starts this process when the extension button is clicked and sends one message:
    {"type": "save", "url", "title", "byline", "lang", "html"}   (html = the article, via Readability)
    {"type": "save_pdf", "url"}                                  (the tab is showing a PDF)
We convert it to ~/Downloads/<title>.mvpdf, register it with the local viewer service and reply
    {"ok": true, "url": "<viewer tab URL>", "file": "<path>"}   or   {"ok": false, "error": "..."}

Only the extension ID listed in the host manifest (written by install_mac.py) can start us;
web pages cannot talk to a native host. Framing is Chrome's: a 4-byte native-endian length,
then that many bytes of UTF-8 JSON, on stdin/stdout.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mvpdf_viewer as viewer  # noqa: E402

HOST_NAME = "com.mvpdf.host"
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_TITLE_CHARS = 300
MAX_FILENAME_CHARS = 80
SAVE_DIR = Path.home() / "Downloads"
MAX_PDF_BYTES = 300 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
PDF_MAGIC = b"%PDF-"
PDF_HEADER_WINDOW = 1024  # readers accept junk before the header within the first 1 KB
USER_AGENT = "Mozilla/5.0 (compatible; mvpdf/1.0)"
_LENGTH = struct.Struct("=I")
_UNSAFE_FILENAME = re.compile(r"[^\w\s.,'()&-]+")


class MessageError(ValueError):
    """The extension sent something we won't process."""


def read_message(stream: BinaryIO) -> dict | None:
    header = stream.read(_LENGTH.size)
    if not header:
        return None  # Chrome closed the pipe
    if len(header) != _LENGTH.size:
        raise MessageError("Truncated message header")
    (length,) = _LENGTH.unpack(header)
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise MessageError("Article is too large to save")
    body = stream.read(length)
    if len(body) != length:
        raise MessageError("Truncated message")
    try:
        message = json.loads(body)
    except (UnicodeDecodeError, ValueError) as err:
        raise MessageError("Message is not valid JSON") from err
    if not isinstance(message, dict):
        raise MessageError("Message must be a JSON object")
    return message


def write_message(stream: BinaryIO, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(_LENGTH.pack(len(body)) + body)
    stream.flush()


def _text(message: dict, key: str, limit: int = MAX_TITLE_CHARS) -> str:
    value = message.get(key, "")
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _web_url(message: dict) -> str:
    url = message.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
        raise MessageError("Only web pages (http/https) can be saved")
    return url


def validate(message: dict) -> dict:
    if message.get("type") != "save":
        raise MessageError("Unknown request")
    html, url = message.get("html"), _web_url(message)
    if not isinstance(html, str) or not html.strip():
        raise MessageError("The page has no readable content")
    return {
        "html": html,
        "url": url,
        "title": _text(message, "title") or urlparse(url).hostname or "Article",
        "byline": _text(message, "byline"),
        "lang": _text(message, "lang", 20),
    }


def article_document(article: dict) -> str:
    """A standalone HTML file carrying the article plus the metadata the converter reads."""
    author = f'<meta name="author" content="{escape(article["byline"])}">' if article["byline"] else ""
    return (
        f'<!doctype html><html lang="{escape(article["lang"])}"><head><meta charset="utf-8">'
        f'<title>{escape(article["title"])}</title>{author}'
        f'<link rel="canonical" href="{escape(article["url"])}"></head>'
        f'<body>{article["html"]}</body></html>'
    )


def safe_filename(title: str) -> str:
    cleaned = " ".join(_UNSAFE_FILENAME.sub(" ", title).split()).strip(" .")
    return cleaned[:MAX_FILENAME_CHARS].rstrip(" .") or "Article"


def unique_path(directory: Path, stem: str, suffix: str = ".mvpdf") -> Path:
    candidate, number = directory / f"{stem}{suffix}", 2
    while candidate.exists():
        candidate, number = directory / f"{stem} ({number}){suffix}", number + 1
    return candidate


Converter = Callable[..., Path]  # (source, output[, source_url])
Publisher = Callable[[Path], str]


def save_article(
    message: dict,
    *,
    save_dir: Path = SAVE_DIR,
    convert: Converter = viewer.convert,
    publish: Publisher = viewer.viewer_url,
) -> dict:
    article = validate(message)
    save_dir.mkdir(parents=True, exist_ok=True)
    output = unique_path(save_dir, safe_filename(article["title"]))
    with tempfile.TemporaryDirectory(prefix="mvpdf-") as tmp:
        source = Path(tmp) / f"{output.stem}.html"
        source.write_text(article_document(article), encoding="utf-8")
        convert(source, output)
    return {"ok": True, "url": publish(output), "file": str(output)}


def pdf_name(url: str) -> str:
    """'.../A_Practical_Intro.pdf?x=1' -> 'A Practical Intro'."""
    stem = unquote(Path(urlparse(url).path).name).removesuffix(".pdf").removesuffix(".PDF")
    return safe_filename(stem.replace("_", " ")) if stem else (urlparse(url).hostname or "Document")


def download_pdf(url: str, dest: Path) -> Path:
    """Fetch the PDF the tab is showing. Streams to disk with a size cap; checks it's really a PDF.

    This is the page the user is looking at and chose to save, so (unlike article images) private
    and intranet hosts are allowed. Pages behind a login won't download: we don't have the
    browser's cookies.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    total = 0
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, \
            dest.open("wb") as out:
        while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
            if total == 0 and PDF_MAGIC not in chunk[:PDF_HEADER_WINDOW]:
                raise MessageError("That link didn't return a PDF (it may need you to be signed in)")
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise MessageError("PDF is too large to save (over 300 MB)")
            out.write(chunk)
    if total == 0:
        raise MessageError("The PDF download was empty")
    return dest


Downloader = Callable[[str, Path], Path]


def save_pdf(
    message: dict,
    *,
    save_dir: Path = SAVE_DIR,
    download: Downloader = download_pdf,
    convert: Converter = viewer.convert,
    publish: Publisher = viewer.viewer_url,
) -> dict:
    url = _web_url(message)
    save_dir.mkdir(parents=True, exist_ok=True)
    output = unique_path(save_dir, pdf_name(url))
    with tempfile.TemporaryDirectory(prefix="mvpdf-") as tmp:
        source = download(url, Path(tmp) / f"{output.stem}.pdf")
        convert(source, output, url)
    return {"ok": True, "url": publish(output), "file": str(output)}


HANDLERS = {"save": save_article, "save_pdf": save_pdf}


def handle(message: dict) -> dict:
    try:
        handler = HANDLERS.get(message.get("type"))
        if handler is None:
            raise MessageError("Unknown request")
        return handler(message)
    except (MessageError, RuntimeError, OSError, urllib.error.URLError) as err:
        print(f"error: {err}", file=sys.stderr)
        return {"ok": False, "error": str(err)}


def main() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    try:
        message = read_message(stdin)
    except MessageError as err:
        write_message(stdout, {"ok": False, "error": str(err)})
        return 1
    if message is not None:
        write_message(stdout, handle(message))
    return 0


if __name__ == "__main__":
    sys.exit(main())
