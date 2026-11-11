import io
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "viewer"))
import install_mac  # noqa: E402
import native_host as host  # noqa: E402

GOOD = {"type": "save", "url": "https://example.com/a", "title": "Heat / Pumps: A Guide?",
        "byline": "Ada", "lang": "en", "html": "<p>Body</p>"}


def framed(payload) -> io.BytesIO:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return io.BytesIO(struct.pack("=I", len(body)) + body)


def test_message_round_trip():
    out = io.BytesIO()
    host.write_message(out, {"ok": True})
    out.seek(0)
    assert host.read_message(out) == {"ok": True}


def test_read_message_end_of_stream():
    assert host.read_message(io.BytesIO(b"")) is None


@pytest.mark.parametrize("stream, error", [
    (io.BytesIO(b"\x01\x00"), "header"),
    (io.BytesIO(struct.pack("=I", 10) + b"abc"), "Truncated"),
    (io.BytesIO(struct.pack("=I", 0)), "too large"),
    (framed(b"not json"), "JSON"),
    (framed([1, 2]), "object"),
])
def test_read_message_rejects_bad_frames(stream, error):
    with pytest.raises(host.MessageError, match=error):
        host.read_message(stream)


def test_read_message_rejects_oversized(monkeypatch):
    monkeypatch.setattr(host, "MAX_MESSAGE_BYTES", 4)
    with pytest.raises(host.MessageError, match="too large"):
        host.read_message(framed({"a": 1}))


@pytest.mark.parametrize("change, error", [
    ({"type": "delete"}, "Unknown"),
    ({"html": "  "}, "readable"),
    ({"html": 5}, "readable"),
    ({"url": "file:///etc/passwd"}, "http"),
    ({"url": None}, "http"),
])
def test_validate_rejects(change, error):
    with pytest.raises(host.MessageError, match=error):
        host.validate({**GOOD, **change})


def test_validate_normalizes_text_fields():
    article = host.validate({**GOOD, "title": "  A \n title ", "byline": 7, "lang": "x" * 50})
    assert article["title"] == "A title" and article["byline"] == "" and len(article["lang"]) == 20
    assert host.validate({**GOOD, "title": ""})["title"] == "example.com"


def test_article_document_escapes_metadata():
    doc = host.article_document(host.validate({**GOOD, "title": '<b>"x"</b>', "byline": 'a"b'}))
    assert "<title>&lt;b&gt;&quot;x&quot;&lt;/b&gt;</title>" in doc
    assert 'content="a&quot;b"' in doc
    assert '<link rel="canonical" href="https://example.com/a">' in doc
    assert doc.endswith("<body><p>Body</p></body></html>")


def test_safe_filename():
    assert host.safe_filename("Heat / Pumps: A Guide?") == "Heat Pumps A Guide"
    assert host.safe_filename("../../etc/passwd") == "etc passwd"
    assert host.safe_filename("???") == "Article"
    assert len(host.safe_filename("x" * 500)) == host.MAX_FILENAME_CHARS


def test_unique_path(tmp_path):
    (tmp_path / "A.mvpdf").write_bytes(b"")
    (tmp_path / "A (2).mvpdf").write_bytes(b"")
    assert host.unique_path(tmp_path, "A").name == "A (3).mvpdf"


def test_save_article_converts_and_publishes(tmp_path):
    seen = {}

    def convert(source, output):
        seen["html"] = source.read_text()
        output.write_bytes(b"mvpdf")
        return output

    reply = host.save_article(GOOD, save_dir=tmp_path, convert=convert,
                              publish=lambda path: f"http://127.0.0.1:8765/?doc=t&name={path.name}")
    assert reply == {"ok": True, "url": "http://127.0.0.1:8765/?doc=t&name=Heat Pumps A Guide.mvpdf",
                     "file": str(tmp_path / "Heat Pumps A Guide.mvpdf")}
    assert "<p>Body</p>" in seen["html"]


def test_handle_reports_errors(monkeypatch):
    def failing(message):
        raise RuntimeError("Could not convert a.html: boom")

    monkeypatch.setattr(host, "save_article", failing)
    assert host.handle(GOOD) == {"ok": False, "error": "Could not convert a.html: boom"}


def test_main_replies_over_stdio(monkeypatch):
    out = io.BytesIO()
    monkeypatch.setattr(host, "handle", lambda message: {"ok": True, "echo": message["type"]})
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": framed(GOOD)})())
    monkeypatch.setattr(sys, "stdout", type("S", (), {"buffer": out})())
    assert host.main() == 0
    out.seek(0)
    assert host.read_message(out) == {"ok": True, "echo": "save"}


def test_extension_id_matches_chrome_algorithm():
    # Chrome: SHA-256 of the DER public key, first 128 bits, hex digits mapped 0-f -> a-p.
    ext_id = install_mac.extension_id("AAAA")
    assert len(ext_id) == 32 and set(ext_id) <= set("abcdefghijklmnop")
    assert ext_id == "hajoiamiieihkcebbobooenpljpcckig"  # regression value


def test_host_manifest_allows_only_the_extension():
    manifest = install_mac.host_manifest("abc")
    assert manifest["allowed_origins"] == ["chrome-extension://abc/"]
    assert manifest["type"] == "stdio" and manifest["name"] == host.HOST_NAME
