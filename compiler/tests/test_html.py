import json

import pymupdf
import pytest

from mvpdf import html_source
from mvpdf.compile import compile_file
from mvpdf.container import unpack
from mvpdf.html_clean import clean_body, image_sources, page_info
from mvpdf.html_source import decode_data_uri, html_to_pdf, normalize_image
from mvpdf.sources import read_source

ARTICLE = """<!doctype html><html lang="en"><head><title> How Heat
Pumps Work </title><meta name="author" content="Ada Writer">
<link rel="canonical" href="https://example.com/heat-pumps"><script>alert(1)</script></head>
<body><nav><a href="/home">Home</a></nav><article>
<h2>The basic idea</h2><p>A heat pump moves heat instead of making it, using a refrigerant
that absorbs heat from outdoor air even in winter.</p>
<figure><img data-src="/img/diagram.png" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="
alt="Diagram"><figcaption>How it works</figcaption></figure>
<h2>Efficiency</h2><p>It delivers three units of heat per unit of electricity; see
<a href="https://en.wikipedia.org/wiki/Coefficient_of_performance">coefficient of performance</a>
and <a href="javascript:alert(1)">this</a>.</p><button>Subscribe</button>
<form><p>Inside a form</p></form></article></body></html>"""


def png(width=120, height=80) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(180)
    return pix.tobytes("png")


def fake_fetch(images):
    calls = []

    def fetch(url, referer):
        calls.append((url, referer))
        return images.get(url)

    fetch.calls = calls
    return fetch


def test_page_info_reads_title_author_and_canonical():
    info = page_info(ARTICLE)
    assert (info.title, info.author, info.source, info.lang) == (
        "How Heat Pumps Work", "Ada Writer", "https://example.com/heat-pumps", "en")


def test_page_info_ignores_unsafe_canonical():
    info = page_info('<link rel="canonical" href="javascript:alert(1)"><p>x</p>')
    assert info.source == ""


def test_clean_body_drops_scripts_nav_and_forms_controls():
    body = clean_body(ARTICLE, "https://example.com/heat-pumps")
    assert "<script" not in body and "alert(1)" not in body
    assert "Home" not in body and "Subscribe" not in body
    assert "Inside a form" in body  # forms are unwrapped, not dropped
    assert "<head" not in body and "How Heat" not in body


def test_clean_body_keeps_only_safe_links():
    body = clean_body(ARTICLE, "https://example.com/heat-pumps")
    assert 'href="https://en.wikipedia.org/wiki/Coefficient_of_performance"' in body
    assert "javascript" not in body and ">this</a>" in body


def test_clean_body_resolves_relative_links_and_escapes_text():
    body = clean_body('<p><a href="/b?x=1&y=2">a &lt;b&gt;</a></p>', "https://ex.com/a/")
    assert body == '<p><a href="https://ex.com/b?x=1&amp;y=2">a &lt;b&gt;</a></p>'


def test_clean_body_closes_unbalanced_tags_and_unwraps_unknown():
    assert clean_body("<div><p>one<custom-tag>two</custom-tag>") == "<div><p>onetwo</p></div>"


def test_image_sources_prefer_lazy_attribute_and_resolve():
    assert image_sources(ARTICLE, "https://example.com/heat-pumps") == (
        "https://example.com/img/diagram.png",)
    srcset = '<img srcset="/s.png 300w, /l.png 1200w">'
    assert image_sources(srcset, "https://ex.com/") == ("https://ex.com/l.png",)


def test_images_missing_from_archive_are_left_out():
    html = '<p>a<img src="https://ex.com/a.png" alt="x">b</p>'
    assert clean_body(html) == "<p>ab</p>"
    assert clean_body(html, image_names={"https://ex.com/a.png": "img0.png"}) == \
        '<p>a<img src="img0.png" alt="x">b</p>'


def test_decode_data_uri():
    assert decode_data_uri("data:image/png;base64,aGk=") == b"hi"
    assert decode_data_uri("data:image/svg+xml,%3Csvg%3E") == b"<svg>"
    assert decode_data_uri("data:text/html,hi") is None
    assert decode_data_uri("data:image/png;base64") is None


def test_normalize_image_rejects_garbage_and_spacers():
    assert normalize_image(b"not an image") is None
    assert normalize_image(png(4, 4)) is None
    data, ext = normalize_image(png())
    assert ext == "jpg" and data[:3] == b"\xff\xd8\xff"


def test_normalize_image_shrinks_wide_images():
    data, _ = normalize_image(png(4000, 100))
    assert pymupdf.Pixmap(data).width <= html_source.MAX_IMAGE_PIXELS


def test_fetch_url_refuses_private_hosts_and_bad_schemes():
    assert html_source.fetch_url("http://127.0.0.1:8765/api/health", "") is None
    assert html_source.fetch_url("http://localhost/", "") is None
    assert html_source.fetch_url("ftp://example.com/a.png", "") is None
    assert html_source.fetch_url("http://192.168.1.1/a.png", "") is None


def test_html_to_pdf_lays_out_pages_with_links_and_images():
    fetch = fake_fetch({"https://example.com/img/diagram.png": png()})
    doc, info = html_to_pdf(ARTICLE, "fallback", fetch)
    assert fetch.calls == [("https://example.com/img/diagram.png", "https://example.com/heat-pumps")]
    assert doc.metadata["title"] == "How Heat Pumps Work"
    uris = {link.get("uri") for page in doc for link in page.get_links()}
    assert "https://en.wikipedia.org/wiki/Coefficient_of_performance" in uris
    assert sum(len(page.get_images()) for page in doc) == 1
    assert "Subscribe" not in "".join(page.get_text() for page in doc)


def test_html_to_pdf_rejects_empty_pages():
    with pytest.raises(ValueError, match="no readable content"):
        html_to_pdf("<html><head><title>x</title></head><body><script>1</script></body></html>",
                    "x", fake_fetch({}))


def test_html_to_pdf_rejects_huge_input(monkeypatch):
    monkeypatch.setattr(html_source, "MAX_HTML_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        html_to_pdf("<p>more than ten bytes</p>", "x", fake_fetch({}))


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr(html_source, "fetch_url", lambda url, referer: None)
    monkeypatch.setattr(html_source.html_to_pdf, "__defaults__", (html_source.fetch_url,))


def test_read_source_html_uses_canonical_url_as_source(tmp_path, no_network):
    path = tmp_path / "saved.html"
    path.write_text(ARTICLE)
    parts = read_source(path)
    assert parts.title == "How Heat Pumps Work" and parts.author == "Ada Writer"
    assert parts.source == "https://example.com/heat-pumps"
    assert parts.pages and any(item["k"] == "h" for item in parts.flow)


def test_read_source_html_rejects_non_utf8(tmp_path):
    path = tmp_path / "bad.html"
    path.write_bytes(b"\xff\xfe<p>x</p>")
    with pytest.raises(ValueError, match="UTF-8"):
        read_source(path)


def test_compile_html_end_to_end(tmp_path, embed, no_network):
    path = tmp_path / "heat-pumps.html"
    path.write_text(ARTICLE)
    result = compile_file(path, output=tmp_path / "out.mvpdf", embed=embed)
    blocks = unpack(result.output.read_bytes())
    meta = json.loads(next(b.data for b in blocks.values() if b.kind == "META"))
    assert meta["source"] == "https://example.com/heat-pumps"
    assert result.page_count == 1 and result.chunk_count >= 2


def test_toc_from_headings_normalizes_levels():
    toc = html_source.toc_from_headings([
        (2, "Intro", 1, 70.0), (4, " Deep\n one ", 1, 300.0), (3, "", 2, 10.0), (2, "End", 3, 50.0)])
    assert [(lvl, title, page) for lvl, title, page, _ in toc] == [
        (1, "Intro", 1), (2, "Deep one", 1), (1, "End", 3)]
    assert toc[0][3]["to"] == (0, 70.0)


def test_html_to_pdf_builds_outline_from_section_headings():
    html = "<h1>Title</h1>" + "".join(
        f"<h2>Section {i}</h2><p>{'words ' * 400}</p><h3>Sub {i}</h3><p>x</p>" for i in range(3))
    doc, _ = html_to_pdf(html, "T", fake_fetch({}))
    toc = doc.get_toc()
    assert [row[:2] for row in toc[:3]] == [[1, "Section 0"], [2, "Sub 0"], [1, "Section 1"]]
    assert toc[-1][2] == doc.page_count and doc.page_count > 1
