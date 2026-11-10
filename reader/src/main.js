import { MvpdfError, itemText, loadDocument, scoreAll, topK } from "./document.js";
import { getEmbedder } from "./embedder.js";
import { fitZoom, renderPages } from "./fixedLayout.js";
import {
  flowIndexFor, highlightFlow, renderOutline, renderOverview, renderReflow, renderResults,
} from "./render.js";

const RESULT_COUNT = 3;
const SEARCH_DEBOUNCE_MS = 350;
const ZOOM_STEP = 1.2;
const ZOOM_LIMITS = [0.25, 4];
const PAGE_GUTTER_PX = 48;
const VIEW_KEY = "mvpdf.view";

const $ = (selector) => document.querySelector(selector);
const ui = {
  intro: $("#intro"), viewer: $("#viewer"), fileInput: $("#file-input"), dropzone: $("#dropzone"),
  status: $("#status"), searchForm: $("#search-form"), searchInput: $("#search-input"),
  results: $("#results"), pages: $("#pages"), reflow: $("#reflow"), outline: $("#outline"),
  outlineList: $("#outline-list"), viewButtons: [...document.querySelectorAll("[data-view]")],
  zoomControls: $("#zoom-controls"), zoomLabel: $("#zoom-label"), docTools: $("#doc-tools"),
};

let state = Object.freeze({
  doc: null, view: "pages", zoom: 1, fit: true, pageCtl: null, token: 0, hits: [],
});
const setState = (patch) => { state = Object.freeze({ ...state, ...patch }); };

function setStatus(message, tone = "info") {
  ui.status.textContent = message;
  ui.status.dataset.tone = tone;
}

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

function applyZoom(zoom, fit = false) {
  const clamped = Math.min(ZOOM_LIMITS[1], Math.max(ZOOM_LIMITS[0], zoom));
  ui.pages.style.setProperty("--zoom", String(clamped));
  ui.zoomLabel.textContent = `${Math.round(clamped * 100)}%`;
  setState({ zoom: clamped, fit });
}

function fitToWidth() {
  if (state.doc?.pages.length) applyZoom(fitZoom(state.doc, ui.pages.clientWidth - PAGE_GUTTER_PX), true);
}

function setView(view) {
  const hasPages = Boolean(state.doc?.pages.length);
  const next = hasPages ? view : "reflow";
  if (next === "reflow" && !ui.reflow.childElementCount) {
    renderReflow(ui.reflow, state.doc, goTo);
    highlightFlow(ui.reflow, state.hits); // reflow is built lazily, after any search
  }
  ui.pages.hidden = next !== "pages";
  ui.reflow.hidden = next !== "reflow";
  ui.zoomControls.hidden = next !== "pages";
  ui.viewButtons.forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.view === next));
    b.disabled = !hasPages && b.dataset.view === "pages";
  });
  setState({ view: next });
  storage.set(VIEW_KEY, next);
  if (next === "pages" && state.fit) fitToWidth();
}

function goTo(page, y = 0) {
  if (state.view === "pages") {
    state.pageCtl?.scrollTo(page, y, state.zoom);
    return;
  }
  const index = flowIndexFor(state.doc, page, y);
  ui.reflow.querySelector(`#flow-${index}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function warmUpModel(modelId) {
  setStatus("Loading search model (first time only; cached afterwards)…");
  return getEmbedder(modelId, (event) => {
    if (event.status === "progress" && event.file?.endsWith(".onnx")) {
      setStatus(`Downloading search model… ${Math.round(event.progress ?? 0)}%`);
    }
  }).then(
    () => setStatus("Ready. Search by meaning, not just keywords.", "ok"),
    (err) => {
      console.error("Model load failed", err);
      setStatus("Could not load the search model. Check your connection and reload.", "error");
    },
  );
}

function showDocument(doc) {
  state.pageCtl?.destroy();
  setState({ doc, pageCtl: null, hits: [] });
  document.title = doc.title;
  renderOverview(document, doc, runTagSearch);
  const outlineCount = renderOutline(ui.outlineList, doc, goTo);
  ui.outline.hidden = outlineCount === 0;
  ui.reflow.replaceChildren();
  ui.results.replaceChildren();
  ui.searchInput.value = "";
  ui.searchForm.hidden = !doc.search;
  ui.intro.hidden = true;
  ui.viewer.hidden = false;
  ui.docTools.hidden = false;
  setState({ pageCtl: doc.pages.length ? renderPages(ui.pages, doc, goTo) : null });
  setView(storage.get(VIEW_KEY) ?? "pages");
  fitToWidth();
  if (doc.search) warmUpModel(doc.search.browserModel);
  else setStatus("This document has no searchable text.", "info");
}

async function openBuffer(buffer, label) {
  setStatus(`Opening ${label}…`);
  try {
    showDocument(await loadDocument(buffer));
  } catch (err) {
    console.error("Failed to open document", err);
    setStatus(err instanceof MvpdfError ? err.message : "Unexpected error reading the file.", "error");
  }
}

async function openFromUrl(url, label) {
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await openBuffer(await response.arrayBuffer(), label);
  } catch (err) {
    console.error("Failed to fetch document", err);
    setStatus(`Could not load ${label}. The viewer link may have expired; open the file again.`, "error");
  }
}

function hitsFor(scores) {
  const { flow } = state.doc;
  return topK(scores, RESULT_COUNT).map(({ index, score }, rank) => {
    const chunk = flow.chunks[index];
    const item = flow.items[chunk.item];
    return {
      item: chunk.item, page: item.page, bbox: item.bbox, score,
      heading: chunk.heading, text: itemText(item), best: rank === 0,
    };
  });
}

function showHits(hits) {
  setState({ hits });
  highlightFlow(ui.reflow, hits);
  state.pageCtl?.highlight(hits);
  renderResults(ui.results, hits, revealHit);
  if (hits[0]) revealHit(hits[0]);
}

function revealHit(hit) {
  if (state.view === "pages" && hit.page !== null) {
    state.pageCtl?.scrollTo(hit.page, hit.bbox?.[1] ?? 0, state.zoom);
    return;
  }
  ui.reflow.querySelector(`#flow-${hit.item}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function search(query) {
  const { doc } = state;
  const trimmed = query.trim();
  if (!doc?.search) return;
  if (!trimmed) {
    showHits([]);
    return;
  }
  const token = state.token + 1;
  setState({ token });
  try {
    const embed = await getEmbedder(doc.search.browserModel);
    const vector = await embed(trimmed);
    if (token !== state.token) return; // a newer query superseded this one
    showHits(hitsFor(scoreAll(vector, doc.search.vectors, doc.search.dim)));
  } catch (err) {
    console.error("Search failed", err);
    setStatus(err instanceof MvpdfError ? err.message : "Search failed. Is the model loaded?", "error");
  }
}

function runTagSearch(tag) {
  ui.searchInput.value = tag;
  search(tag);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function bindEvents() {
  ui.fileInput.addEventListener("change", async () => {
    const file = ui.fileInput.files[0];
    if (file) await openBuffer(await file.arrayBuffer(), file.name);
  });
  ui.searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    search(ui.searchInput.value);
  });
  ui.searchInput.addEventListener("input", debounce(() => search(ui.searchInput.value), SEARCH_DEBOUNCE_MS));
  ui.viewButtons.forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#zoom-in").addEventListener("click", () => applyZoom(state.zoom * ZOOM_STEP));
  $("#zoom-out").addEventListener("click", () => applyZoom(state.zoom / ZOOM_STEP));
  $("#zoom-fit").addEventListener("click", fitToWidth);
  window.addEventListener("resize", debounce(() => { if (state.fit) fitToWidth(); }, 150));

  ["dragenter", "dragover"].forEach((type) => document.addEventListener(type, (event) => {
    event.preventDefault();
    ui.dropzone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((type) => document.addEventListener(type, (event) => {
    event.preventDefault();
    ui.dropzone.classList.remove("dragging");
  }));
  document.addEventListener("drop", async (event) => {
    const file = event.dataTransfer?.files[0];
    if (file) await openBuffer(await file.arrayBuffer(), file.name);
  });
}

/** ?doc=<token> comes from the Mac viewer app; ?src=<path> loads a same-origin file. */
function openFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("doc");
  if (token && /^[A-Za-z0-9_-]{16,64}$/.test(token)) {
    openFromUrl(`/api/doc/${token}`, params.get("name") ?? "document");
    return;
  }
  const src = params.get("src");
  if (!src) return;
  const url = new URL(src, window.location.href);
  if (url.origin !== window.location.origin) {
    setStatus("Only files from this site can be opened by link.", "error");
    return;
  }
  openFromUrl(url.href, url.pathname.split("/").pop());
}

bindEvents();
openFromQuery();
