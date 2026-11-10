import base64
import json

import pymupdf
import pytest

from mvpdf.container import Block
from mvpdf.pdf_flow import Run, _join_line, build_flow, heading_levels, merge_runs, search_chunks
from mvpdf.pdf_pages import clean_text, extract_svg_images, page_links, safe_uri, text_layer
from mvpdf.sources import read_pdf, read_source, text_flow

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.mark.parametrize("uri,expected", [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("mailto:someone@example.com", "mailto:someone@example.com"),
    ("javascript:alert(1)", None),
    ("file:///etc/passwd", None),
    ("  ", None),
    (None, None),
])
def test_safe_uri_keeps_only_web_and_mail_links(uri, expected):
    assert safe_uri(uri) == expected


def test_clean_text_folds_ligatures():
    assert clean_text("eﬃcient ﬁle") == "efficient file"


def test_extract_svg_images_moves_images_into_blocks():
    encoded = base64.b64encode(PNG_1PX).decode()
    svg = (f'<svg><use data-text="A" xlink:href="#g"/>'
           f'<image xlink:href="data:image/png;base64,\n{encoded[:20]}\n{encoded[20:]}"/>'
           f'<image xlink:href="data:image/x-weird;base64,AAAA"/></svg>')

    cleaned, blocks, images = extract_svg_images(svg)

    assert blocks == (Block("IMAG", PNG_1PX),)
    assert images == {blocks[0].id: "image/png"}
    assert f'xlink:href="mvpdf:{blocks[0].id}"' in cleaned
    assert "data:" not in cleaned and "data-text" not in cleaned


def test_text_layer_skips_rotated_and_blank_spans():
    span = {"text": "Hello", "bbox": (10, 20, 60, 32), "origin": (10, 30), "size": 12}
    page = {"blocks": [{"lines": [
        {"dir": (1, 0), "spans": [span, {**span, "text": "  "}]},
        {"dir": (0, 1), "spans": [span]},
    ]}]}
    assert text_layer(page) == [[10.0, 30.0, 50.0, 12.0, "Hello"]]


def test_page_links_validates_targets():
    rect = pymupdf.Rect(1, 2, 3, 4)
    links = [
        {"from": rect, "uri": "https://ok.example"},
        {"from": rect, "uri": "javascript:bad()"},
        {"from": rect, "page": 1, "to": pymupdf.Point(0, 50)},
        {"from": rect, "page": 99},
    ]
    assert page_links(links, page_count=2) == [
        {"r": [1.0, 2.0, 3.0, 4.0], "uri": "https://ok.example"},
        {"r": [1.0, 2.0, 3.0, 4.0], "page": 1, "y": 50.0},
    ]


def test_merge_runs_joins_neighbours_with_same_link():
    runs = [Run("a "), Run("b"), Run("link", ("uri", "u")), Run(" too", ("uri", "u")), Run(".")]
    assert merge_runs(runs) == (Run("a b"), Run("link too", ("uri", "u")), Run("."))


def test_join_line_dehyphenates_split_words():
    assert _join_line([Run("infor-")], [Run("mation")]) == [Run("infor"), Run("mation")]
    assert _join_line([Run("well-")], [Run("Known")]) == [Run("well- "), Run("Known")]


def test_heading_levels_rank_sizes():
    assert heading_levels([24.0, 14.0, 18.0, 14.0, 12.0]) == {24.0: 1, 18.0: 2, 14.0: 3, 12.0: 3}


def test_build_flow_handles_empty_input():
    assert build_flow([]) == ([], [], {})


def test_read_pdf_extracts_pages_links_toc_and_flow(sample_pdf):
    parts = read_pdf(sample_pdf)

    assert parts.title == "Solar Energy: A Practical Overview"
    assert parts.author == "mvpdf samples"
    assert len(parts.pages) == 2
    assert [t["title"] for t in parts.toc] == ["Solar Energy", "Storage and the Grid"]

    page = json.loads(parts.pages[0].data)
    assert page["w"] == 612 and page["h"] == 792
    assert any(link.get("uri", "").startswith("https://www.energy.gov") for link in page["links"])
    assert page["svg"] in {b.id for b in parts.resources}

    kinds = [item["k"] for item in parts.flow]
    headings = [item for item in parts.flow if item["k"] == "h"]
    assert "img" in kinds
    assert headings[0]["lvl"] == 1 and headings[1]["lvl"] == 2
    texts = ["".join(r["t"] for r in item.get("runs", [])) for item in parts.flow]
    assert not any("Page 1" in t for t in texts), "running footer should be removed"
    linked = [r for item in parts.flow for r in item.get("runs", []) if "uri" in r]
    assert any(r["t"].strip() == "U.S. Department of Energy" for r in linked)
    assert all(parts.images[b.id] == "image/png" for b in parts.resources if b.kind == "IMAG")


def test_search_chunks_point_at_paragraphs_with_headings(sample_pdf):
    parts = read_pdf(sample_pdf)
    for chunk in parts.chunks:
        assert parts.flow[chunk["item"]]["k"] == "p"
    assert any(c["heading"] == "Storage and the Grid" for c in parts.chunks)
    assert search_chunks(parts.flow, min_chars=10_000) == []


def test_read_source_rejects_bad_inputs(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_source(tmp_path / "missing.pdf")
    other = tmp_path / "x.docx"
    other.write_text("hi")
    with pytest.raises(ValueError, match="Unsupported"):
        read_source(other)
    fake = tmp_path / "fake.pdf"
    fake.write_text("not a pdf at all")
    with pytest.raises(ValueError):
        read_source(fake)


def test_read_source_rejects_encrypted_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    locked = tmp_path / "locked.pdf"
    doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(ValueError, match="password"):
        read_source(locked)


def test_text_flow_builds_headings_and_paragraphs(sample_text):
    flow = text_flow(sample_text)
    assert flow[0]["k"] in {"h", "p"}
    assert any(item["k"] == "h" for item in flow)
    assert all("page" not in item for item in flow)
