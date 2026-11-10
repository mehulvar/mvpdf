import pytest

from mvpdf.chunking import Chunk, is_heading, split_document, split_paragraphs, split_sentences


def test_splits_on_blank_lines_and_normalizes_whitespace():
    text = "First paragraph is long enough to stand alone.\n\n\nSecond   paragraph\nwraps lines here."
    assert split_paragraphs(text, min_chars=10) == (
        "First paragraph is long enough to stand alone.",
        "Second paragraph wraps lines here.",
    )


def test_merges_short_heading_into_following_paragraph():
    text = "Intro\n\nThis is the body paragraph that follows the heading."
    assert split_paragraphs(text, min_chars=20) == (
        "Intro This is the body paragraph that follows the heading.",
    )


def test_trailing_short_fragment_appends_to_previous_chunk():
    text = "A long enough opening paragraph for the test.\n\nEnd."
    assert split_paragraphs(text, min_chars=20) == ("A long enough opening paragraph for the test. End.",)


def test_only_short_text_still_yields_one_chunk():
    assert split_paragraphs("Tiny.", min_chars=20) == ("Tiny.",)


def test_long_paragraph_split_on_sentence_boundaries():
    para = " ".join(f"Sentence number {i} is here." for i in range(10))
    chunks = split_paragraphs(para, min_chars=0, max_chars=60)
    assert all(len(c) <= 60 for c in chunks)
    assert " ".join(chunks) == para


def test_handles_crlf_and_empty_input():
    assert split_paragraphs("") == ()
    assert split_paragraphs("Line one is reasonably long here.\r\n\r\nLine two is also long enough.", min_chars=5) == (
        "Line one is reasonably long here.",
        "Line two is also long enough.",
    )


def test_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        split_paragraphs("x", min_chars=100, max_chars=10)


def test_split_document_attaches_headings_and_excludes_them_from_text():
    text = (
        "Preface text that appears before any heading at all.\n\n"
        "Storage and the Grid\n\n"
        "Batteries store energy for later use in the evening.\n\n"
        "Grid operators balance supply and demand constantly."
    )
    assert split_document(text) == (
        Chunk("Preface text that appears before any heading at all.", None),
        Chunk("Batteries store energy for later use in the evening.", "Storage and the Grid"),
        Chunk("Grid operators balance supply and demand constantly.", "Storage and the Grid"),
    )


def test_split_document_with_only_headings_falls_back_to_paragraphs():
    assert split_document("Title Only\n\nAnother Line") == (Chunk("Title Only Another Line"),)


def test_is_heading():
    assert is_heading("Efficiency and Cost")
    assert not is_heading("This ends with a period.")
    assert not is_heading(" ".join(["word"] * 12))


def test_split_sentences():
    assert split_sentences("One here. Two there! Three? ok") == ("One here.", "Two there!", "Three? ok")
    assert split_sentences("   ") == ()
