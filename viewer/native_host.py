"""Chrome native-messaging host for the "Save as .mvpdf" browser extension.

Chrome starts this process when the extension button is clicked and sends one message:
    {"type": "save", "url", "title", "byline", "lang", "html"}   (html = the article, via Readability)
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
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mvpdf_viewer as viewer  # noqa: E402

HOST_NAME = "com.mvpdf.host"
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_TITLE_CHARS = 300
MAX_FILENAME_CHARS = 80
SAVE_DIR = Path.home() / "Downloads"
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


def validate(message: dict) -> dict:
    if message.get("type") != "save":
        raise MessageError("Unknown request")
    html, url = message.get("html"), message.get("url")
    if not isinstance(html, str) or not html.strip():
        raise MessageError("The page has no readable content")
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
        raise MessageError("Only web pages (http/https) can be saved")
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


Converter = Callable[[Path, Path], Path]
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


def handle(message: dict) -> dict:
    try:
        return save_article(message)
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
