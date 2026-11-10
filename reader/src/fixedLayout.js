// Fixed-layout page view: vector graphics + invisible selectable text + clickable links.
// Pages are painted lazily as they scroll near the viewport.

const PRELOAD_MARGIN = "800px 0px";
const MEASURE_FONT = "sans-serif";

const el = (tag, className) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
};
const pct = (value, total) => `${(value / total) * 100}%`;

function placeRect(node, [x0, y0, x1, y1], page) {
  node.style.left = pct(x0, page.width);
  node.style.top = pct(y0, page.height);
  node.style.width = pct(x1 - x0, page.width);
  node.style.height = pct(y1 - y0, page.height);
}

const FALLBACK_ASCENT = 0.8;
const ASCENT_PROBE_PX = 100;

let measureContext;
function measurer() {
  measureContext ??= document.createElement("canvas").getContext("2d");
  return measureContext;
}

function textWidth(text, size) {
  const ctx = measurer();
  ctx.font = `${size}px ${MEASURE_FONT}`;
  return ctx.measureText(text).width;
}

let ascentRatio;
/** Fraction of the font size that sits above the baseline in the fallback font. */
function fontAscent() {
  if (ascentRatio === undefined) {
    const ctx = measurer();
    ctx.font = `${ASCENT_PROBE_PX}px ${MEASURE_FONT}`;
    const ascent = ctx.measureText("Hg").fontBoundingBoxAscent;
    ascentRatio = ascent > 0 ? ascent / ASCENT_PROBE_PX : FALLBACK_ASCENT;
  }
  return ascentRatio;
}

function buildTextLayer(page) {
  const layer = el("div", "text-layer");
  page.text.forEach(({ x, baseline, w, size, text }) => {
    const span = el("span");
    span.textContent = text;
    span.style.left = pct(x, page.width);
    // Align the fallback font's baseline with the PDF glyph baseline.
    span.style.top = pct(baseline - fontAscent() * size, page.height);
    span.style.fontSize = `calc(var(--zoom) * ${size}px)`;
    const natural = textWidth(text, size);
    // Stretch the fallback font so selection boxes line up with the real glyphs.
    if (natural > 0) span.style.transform = `scaleX(${w / natural})`;
    layer.append(span);
  });
  return layer;
}

function buildLinkLayer(page, onInternalLink) {
  const layer = el("div", "link-layer");
  page.links.forEach(({ rect, target }) => {
    const link = el("a", "page-link");
    placeRect(link, rect, page);
    if (target.href) {
      link.href = target.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = target.href;
    } else {
      link.href = `#page-${target.page + 1}`;
      link.title = `Go to page ${target.page + 1}`;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        onInternalLink(target.page, target.y);
      });
    }
    layer.append(link);
  });
  return layer;
}

function paint(frame, page, doc, onInternalLink) {
  if (frame.dataset.painted) return;
  frame.dataset.painted = "true";
  const svg = doc.pageSvg(page);
  if (svg) {
    const img = el("img", "page-graphics");
    img.alt = `Page ${page.index + 1}`;
    img.draggable = false;
    img.src = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    frame.prepend(img);
  }
  frame.append(buildTextLayer(page), buildLinkLayer(page, onInternalLink));
}

/** Render page frames into `container`. Returns a controller for navigation and highlights. */
export function renderPages(container, doc, onInternalLink) {
  const frames = doc.pages.map((page) => {
    const frame = el("section", "page");
    frame.id = `page-${page.index + 1}`;
    frame.style.width = `calc(var(--zoom) * ${page.width}px)`;
    frame.style.height = `calc(var(--zoom) * ${page.height}px)`;
    frame.setAttribute("aria-label", `Page ${page.index + 1} of ${doc.pages.length}`);
    frame.append(el("div", "hit-layer"));
    return frame;
  });
  container.replaceChildren(...frames);

  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    const index = frames.indexOf(entry.target);
    paint(entry.target, doc.pages[index], doc, onInternalLink);
    observer.unobserve(entry.target);
  }), { rootMargin: PRELOAD_MARGIN });
  frames.forEach((frame) => observer.observe(frame));

  return {
    scrollTo(pageIndex, y = 0, zoom = 1) {
      const frame = frames[pageIndex];
      if (!frame) return;
      const top = frame.getBoundingClientRect().top + window.scrollY + y * zoom - 80;
      window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    },
    highlight(hits) {
      frames.forEach((frame) => frame.querySelector(".hit-layer").replaceChildren());
      hits.forEach(({ page, bbox, best }) => {
        const frame = frames[page];
        if (!frame || !bbox) return;
        const mark = el("div", best ? "hit best" : "hit");
        placeRect(mark, bbox, doc.pages[page]);
        frame.querySelector(".hit-layer").append(mark);
      });
    },
    destroy: () => observer.disconnect(),
  };
}

/** Fit-to-width zoom for the widest page, capped so small pages don't balloon. */
export function fitZoom(doc, availableWidth, maxZoom = 1.6) {
  const widest = Math.max(...doc.pages.map((p) => p.width), 1);
  return Math.min(maxZoom, Math.max(0.25, availableWidth / widest));
}
