"""Generate samples/solar_energy.pdf: a multi-page PDF with headings, links, an image and a TOC.

    python samples/make_sample_pdf.py
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
PAGE = pymupdf.paper_rect("letter")
MARGIN = 72
FOOTER_Y = PAGE.height - 36

HTML = """
<h1 id="top">Solar Energy: A Practical Overview</h1>
<p>This sample document exists to exercise the <b>.mvpdf</b> converter. It mixes headings,
body text, an image and both external and internal links. Read more at the
<a href="https://www.energy.gov/eere/solar/how-does-solar-work">U.S. Department of Energy</a>
or jump to the <a href="#storage">section on storage</a>.</p>

<h2>How Photovoltaic Cells Work</h2>
<p>Solar panels convert sunlight directly into electricity using photovoltaic cells. Each cell
is a sandwich of doped silicon layers. When photons strike the cell they knock electrons
loose, and the electric field at the junction pushes those electrons into a current that
flows through the attached circuit.</p>
<img src="chart.png" width="300"/>
<p>Panel efficiency has climbed steadily. Commercial modules now convert a little over a fifth
of incoming sunlight into electricity, while laboratory cells built from stacked materials
have passed forty percent. See the
<a href="https://www.nrel.gov/pv/cell-efficiency.html">NREL efficiency chart</a> for details.</p>

<h2>Costs and Manufacturing</h2>
<p>The price of solar modules has fallen by roughly ninety percent over the last decade.
Cheaper manufacturing, larger factories and better wafer cutting all contributed. In many
regions new solar farms are now the least expensive source of new electricity generation.</p>
<p>Installation labour, permitting and grid connection now make up a growing share of the
total cost of a rooftop system, which is why so-called soft costs are a focus for policy.</p>

<h2 id="storage">Storage and the Grid</h2>
<p>Grid operators must balance supply and demand every second. Because solar output peaks at
midday and drops to zero at night, batteries are increasingly paired with solar farms to
shift energy into the evening peak. Lithium-ion packs dominate today, while flow batteries
and thermal storage compete for longer durations.</p>
<p>During a grid outage, a home battery with an islanding inverter can keep essential
circuits running. Without that equipment, rooftop panels shut down for the safety of line
workers.</p>

<h2>End of Life</h2>
<p>Recycling programs are growing so that glass, metal, and silicon can be recovered when old
panels are retired. Most panels carry twenty-five year warranties, so the first large wave
of retirements is only now arriving.</p>
<p><a href="#top">Back to the top</a></p>
"""


def chart_png() -> bytes:
    """A small bar chart drawn with PyMuPDF so the sample needs no image files."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=160)
    page.draw_rect(page.rect, fill=(0.97, 0.97, 0.95), color=None)
    for i, height in enumerate((30, 48, 70, 95, 120)):
        x = 30 + i * 52
        page.draw_rect(pymupdf.Rect(x, 140 - height, x + 34, 140), fill=(0.95, 0.65, 0.1))
    page.insert_text((30, 22), "Module efficiency over time", fontsize=12)
    return page.get_pixmap(dpi=144).tobytes("png")


def add_footers(doc: pymupdf.Document) -> None:
    for number, page in enumerate(doc, start=1):
        page.insert_text((MARGIN, FOOTER_Y), f"Solar Energy Sample  ·  Page {number}", fontsize=8,
                         color=(0.45, 0.45, 0.45))


def build(output: Path) -> Path:
    archive = pymupdf.Archive()
    archive.add(chart_png(), "chart.png")
    story = pymupdf.Story(HTML, archive=archive, user_css="body {font-size: 11pt; line-height: 1.4}")
    content = PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    # write_with_links keeps <a href> as real PDF link annotations (external and #internal).
    doc = story.write_with_links(lambda _num, _filled: (PAGE, content, None))
    doc = pymupdf.open("pdf", doc.tobytes())
    add_footers(doc)
    doc.set_metadata({"title": "Solar Energy: A Practical Overview", "author": "mvpdf samples"})
    doc.set_toc([[1, "Solar Energy", 1], [2, "Storage and the Grid", doc.page_count]])
    doc.save(output, garbage=3, deflate=True)
    return output


if __name__ == "__main__":
    print(f"Wrote {build(HERE / 'solar_energy.pdf')}")
