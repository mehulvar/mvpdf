# .mvpdf — a document format that looks like a PDF and understands its text

Convert any PDF to `.mvpdf` and you keep the exact page look (vector graphics, images, links,
table of contents), plus a built-in AI layer: a summary, topic tags, a reflow view for phones,
and search by meaning. Everything runs on your machine.

## Quick start (macOS)

```bash
cd compiler && pip install -r requirements.txt
python -m mvpdf ../samples/solar_energy.pdf          # writes ../samples/solar_energy.mvpdf
cd .. && python viewer/install_mac.py                 # one-time: installs "MVPDF Viewer.app"
```

Then:

- **Double-click any `.mvpdf`** to open it in a new browser tab, the way a PDF opens.
- **Right-click a PDF → Open With → MVPDF Viewer** to convert it next to the original and open it.

Run the installer with the same Python that has the converter's packages. If you move this
folder, re-run the installer; `--uninstall` removes everything it installed.

## Save web pages and online PDFs as .mvpdf (Chrome, Brave, Edge)

1. Run `python viewer/install_mac.py` (it also registers the extension's native host).
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, pick `extension/`.
3. On any article, or any PDF open in the browser, click the **Save as MVPDF** toolbar button
   (or press **Alt+Shift+S**).

The article opens in a new tab a few seconds later and is saved to `~/Downloads/<title>.mvpdf`.
The extension uses [Readability](https://github.com/mozilla/readability) (Firefox Reader View's
engine) to keep the article and drop ads, menus and comments. Pages, links, images, tables, and a
real outline from the article's section headings all carry over.

How it fits together: the extension hands the article to `viewer/native_host.py` through Chrome
native messaging. Chrome only lets the extension ID listed in the host manifest start that
program; web pages can't reach it. The host converts the article, then registers it with the
local viewer service. Images are downloaded by the converter only from public internet
addresses, never from `localhost` or your LAN.

For a PDF tab, the converter downloads the PDF itself and converts it page for page (a
263-page book takes about 15 seconds). PDFs that need you to be signed in won't download,
because the converter doesn't have your browser's cookies; save those and use Open With.

You can also convert a saved page directly: `python -m mvpdf page.html`.

## Features

| | PDF | .mvpdf |
|---|---|---|
| Exact page look at any zoom | ✓ | ✓ (vector pages, glyphs as paths) |
| Select / copy text, Ctrl+F | ✓ | ✓ (invisible text layer over the page) |
| Clickable links, internal jumps, outline | ✓ | ✓ (only `http`, `https` and `mailto` links are kept) |
| Reflow for small screens | ✗ | ✓ headings, paragraphs, images, links |
| Search by meaning | ✗ | ✓ dot product against stored MiniLM vectors |
| Summary + topic tags | ✗ | ✓ computed once at conversion |
| Corruption detection | partial | ✓ CRC32 on every block |
| Shared images stored once | ✗ | ✓ content-addressed blocks |

## The storage system

A PDF is a text-syntax object graph with a cross-reference table at the end of the file.
`.mvpdf` is a flat binary sequence of typed blocks, each named by the hash of its content:

```
header   \x89MVPDF\r\n\x1a\n | u16 major=2 | u16 minor | u32 block_count
block*   type(4) | flags(1) | pad(3) | id(16, BLAKE2b of content) | u32 stored | u32 raw | u32 crc32
         payload (zlib-deflated when flags & 1)
```

| Block | Contents |
|---|---|
| `META` | JSON: title, author, summary, tags, outline, page ids, image types, search settings |
| `PAGE` | JSON: one page's size, graphics id, text layer `[x, baseline, width, size, text]`, links |
| `SVGG` | the page's vector graphics (images referenced as `mvpdf:<id>`) |
| `IMAG` | a PNG/JPEG/GIF/WebP image, shared by every page that uses it |
| `FLOW` | JSON: reading-order headings/paragraphs/images with link runs, and the search chunks |
| `VECT` | float32 little-endian embeddings, one row per search chunk |

Because ids are content hashes, an image or page repeated anywhere in a document is stored once.
Readers verify the checksum of every block, and refuse blocks that would decompress past their
declared size.

## How conversion works

1. **Pages:** MuPDF renders each page to SVG with text as glyph outlines, so the page looks exact
   without embedding fonts. Inline images are pulled out into shared `IMAG` blocks.
2. **Text layer and links:** span positions (baseline-aligned) and link rectangles come from the PDF.
3. **Flow:** text blocks become paragraphs. Ligatures are folded (ﬁ → fi) and words split by
   hyphens at line ends are rejoined. Headings are detected from font size or bold text, and
   headers/footers that repeat across pages are dropped. Link text stays clickable.
4. **AI layer:** paragraphs are embedded with `all-MiniLM-L6-v2`. The summary uses extractive
   MMR, and the tags are KeyBERT-style phrases. The browser runs the matching ONNX model to embed
   your queries.

Web pages (`.html`) are first laid out onto Letter pages by MuPDF, then follow the same steps;
their outline comes from the article's `<h2>`–`<h4>` headings. `.txt` files get the reflow view only.

## Without the Mac app

```bash
python viewer/mvpdf_viewer.py open file.mvpdf   # any OS: starts the local viewer, opens a tab
python3 -m http.server 8000                    # or serve the repo and drop files onto /reader/
```

The viewer service listens on `127.0.0.1:8765` only. It serves a file only after the local
`open` command registers it under a random token. It rejects requests with a foreign Host
header (DNS rebinding) and cross-site registration attempts, and it shuts down after 3 hours idle.

## Limitations

- Rotated text is visible and searchable but can't be selected.
- Scanned PDFs (images only) convert to pages without search; there is no OCR yet.
- Forms, annotations other than links, and digital signatures are not carried over.
- The first search downloads the ~90 MB model, which the browser then caches.
- PyMuPDF is AGPL-licensed. That's fine for personal use; check the license before distributing.

## Tests

```bash
cd compiler && python -m pytest -q --cov=mvpdf   # fake embedder; no model download
cd reader && npm test                            # node:test, no dependencies
node --test extension/tests/messages.test.mjs    # extension helpers
```
