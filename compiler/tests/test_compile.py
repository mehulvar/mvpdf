import json
from datetime import datetime, timezone

import numpy as np
import pytest

from mvpdf import __main__ as cli
from mvpdf.compile import compile_file, compile_parts
from mvpdf.container import unpack
from mvpdf.document import DocumentParts, build_document, vectors_to_bytes


def _blocks_by_kind(path):
    blocks = unpack(path.read_bytes())
    meta = json.loads(next(b for b in blocks.values() if b.kind == "META").data)
    return blocks, meta


def test_vectors_to_bytes_is_little_endian_float32_row_major():
    raw = vectors_to_bytes(np.array([[1.0, 2.0], [3.0, 4.0]]))
    assert np.frombuffer(raw, dtype="<f4").tolist() == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize("bad", [np.zeros(3), np.array([[np.nan, 1.0]])])
def test_vectors_to_bytes_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        vectors_to_bytes(bad)


def test_build_document_meta_shape():
    parts = DocumentParts(title="T", source="t.txt", flow=[{"k": "p", "runs": [{"t": "a"}]}],
                          chunks=[{"item": 0, "heading": None}])
    data = build_document(parts, summary="S", tags=("x",), search=None,
                          created=datetime(2026, 1, 1, tzinfo=timezone.utc))
    meta = json.loads(next(b for b in unpack(data).values() if b.kind == "META").data)
    assert meta["format"] == "mvpdf" and meta["version"] == 2
    assert meta["created"] == "2026-01-01T00:00:00+00:00"
    assert meta["search"] is None and meta["pages"] == []


def test_compile_pdf_writes_complete_document(tmp_path, sample_pdf, embed):
    out = tmp_path / "doc.mvpdf"

    result = compile_file(sample_pdf, output=out, embed=embed)

    blocks, meta = _blocks_by_kind(out)
    assert result.page_count == 2 and result.size_bytes == out.stat().st_size
    assert all(page_id in blocks for page_id in meta["pages"])
    flow = json.loads(blocks[meta["flow"]].data)
    search = meta["search"]
    assert search["count"] == len(flow["chunks"]) == result.chunk_count
    vectors = np.frombuffer(blocks[search["vectors"]].data, dtype="<f4").reshape(search["count"], -1)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert set(flow["chunks"][0]) == {"item", "heading"}, "chunk text is not stored twice"
    assert meta["summary"] and len(meta["tags"]) == 3
    assert not out.with_suffix(".mvpdf.tmp").exists()


def test_compile_txt_has_flow_but_no_pages(tmp_path, embed):
    src = tmp_path / "notes.txt"
    src.write_text("Heading\n\nA first paragraph that is long enough to count.\n\nAnother one here too.")
    result = compile_file(src, output=tmp_path / "notes.mvpdf", embed=embed)
    _, meta = _blocks_by_kind(tmp_path / "notes.mvpdf")
    assert result.page_count == 0 and meta["pages"] == [] and meta["title"] == "Notes"


def test_compile_without_text_skips_the_model(tmp_path):
    def exploding_embed(_texts):
        raise AssertionError("model must not load for a text-less document")

    parts = DocumentParts(title="Scan", source="scan.pdf", flow=[], chunks=[],
                          pages=(), resources=())
    with pytest.raises(ValueError, match="no text"):
        compile_parts(parts, output=tmp_path / "x.mvpdf", embed=exploding_embed)


def test_compile_rejects_wrong_vector_count(tmp_path, sample_pdf):
    with pytest.raises(ValueError, match="wrong number"):
        compile_file(sample_pdf, output=tmp_path / "x.mvpdf",
                     embed=lambda texts: np.zeros((1, 4), dtype=np.float32))


def test_cli_converts_and_reports(tmp_path, sample_pdf, embed, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_embedder", lambda name: embed)
    out = tmp_path / "out.mvpdf"
    assert cli.main([str(sample_pdf), "-o", str(out)]) == 0
    assert out.exists()
    assert "2 pages" in capsys.readouterr().out


def test_cli_defaults_output_next_to_input(tmp_path, embed, monkeypatch):
    monkeypatch.setattr(cli, "load_embedder", lambda name: embed)
    src = tmp_path / "my_notes.txt"
    src.write_text("Some meaningful content that is long enough to be a chunk of text.")
    assert cli.main([str(src)]) == 0
    assert (tmp_path / "my_notes.mvpdf").exists()


def test_cli_reports_errors(tmp_path, capsys):
    assert cli.main([str(tmp_path / "missing.pdf")]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_records_source_url(tmp_path, sample_pdf, embed, monkeypatch):
    from mvpdf.container import unpack

    monkeypatch.setattr(cli, "load_embedder", lambda name: embed)
    out = tmp_path / "out.mvpdf"
    assert cli.main([str(sample_pdf), "-o", str(out), "--source-url", "https://ex.com/a.pdf"]) == 0
    meta = next(b for b in unpack(out.read_bytes()).values() if b.kind == "META")
    assert json.loads(meta.data)["source"] == "https://ex.com/a.pdf"


def test_cli_rejects_non_web_source_url(sample_pdf):
    with pytest.raises(SystemExit):
        cli.main([str(sample_pdf), "--source-url", "javascript:alert(1)"])
