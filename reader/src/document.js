// Turns verified container blocks into a validated, frozen document model.
// Every field comes from an untrusted file, so each one is type-checked here.

import { MvpdfError, readContainer } from "./container.js";

export const FORMAT_VERSION = 2;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const LINK_SCHEMES = new Set(["http:", "https:", "mailto:"]);
const IMAGE_REF = /xlink:href="mvpdf:([0-9a-f]{32})"/g;
const BYTES_PER_FLOAT32 = 4;
const BASE64_CHUNK = 0x8000;

const utf8 = new TextDecoder("utf-8");
const isStr = (v) => typeof v === "string";
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isId = (v) => isStr(v) && /^[0-9a-f]{32}$/.test(v);
const nums = (arr, n) => Array.isArray(arr) && arr.length === n && arr.every(isNum);
const list = (v) => (Array.isArray(v) ? v : []);

/** Only web and mail links survive; javascript:, data:, file: etc. are dropped. */
export function safeHref(uri) {
  if (!isStr(uri)) return null;
  try {
    const url = new URL(uri.trim());
    return LINK_SCHEMES.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function parseJson(block, label) {
  try {
    return JSON.parse(utf8.decode(block.data));
  } catch {
    throw new MvpdfError(`${label} is not valid JSON`);
  }
}

function linkTarget(raw, pageCount) {
  const href = safeHref(raw.uri);
  if (href) return { href };
  if (Number.isInteger(raw.page) && raw.page >= 0 && raw.page < pageCount) {
    return { page: raw.page, y: isNum(raw.y) ? raw.y : 0 };
  }
  return null;
}

function parseRuns(runs, pageCount) {
  return list(runs).filter((r) => r && isStr(r.t)).map((r) => {
    const target = linkTarget(r, pageCount);
    return Object.freeze(target ? { text: r.t, ...target } : { text: r.t });
  });
}

function parseTextSpan(span) {
  if (!Array.isArray(span) || span.length < 5 || !span.slice(0, 4).every(isNum) || !isStr(span[4])) {
    return null;
  }
  const [x, baseline, w, size, text] = span;
  return Object.freeze({ x, baseline, w, size, text });
}

function parsePage(block, index, pageCount) {
  const raw = parseJson(block, `Page ${index + 1}`);
  if (!isNum(raw.w) || !isNum(raw.h) || raw.w <= 0 || raw.h <= 0 || !isId(raw.svg)) {
    throw new MvpdfError(`Page ${index + 1} has an invalid size or graphics reference`);
  }
  const links = list(raw.links)
    .filter((l) => l && nums(l.r, 4))
    .map((l) => ({ rect: l.r, target: linkTarget(l, pageCount) }))
    .filter((l) => l.target);
  return Object.freeze({
    index,
    width: raw.w,
    height: raw.h,
    svgId: raw.svg,
    text: Object.freeze(list(raw.text).map(parseTextSpan).filter(Boolean)),
    links: Object.freeze(links),
  });
}

function parseItem(raw, pageCount) {
  const page = Number.isInteger(raw?.page) && raw.page >= 0 && raw.page < pageCount ? raw.page : null;
  const bbox = nums(raw?.bbox, 4) ? raw.bbox : null;
  if (raw?.k === "img" && isId(raw.ref)) return Object.freeze({ kind: "img", ref: raw.ref, page, bbox });
  if (raw?.k === "h" || raw?.k === "p") {
    const level = raw.k === "h" ? Math.min(Math.max(Number.parseInt(raw.lvl, 10) || 1, 1), 3) : 0;
    return Object.freeze({ kind: raw.k, level, runs: Object.freeze(parseRuns(raw.runs, pageCount)), page, bbox });
  }
  return null;
}

export const itemText = (item) => (item?.runs ?? []).map((r) => r.text).join("").trim();

function parseFlow(block, pageCount) {
  const raw = block ? parseJson(block, "Flow") : {};
  const items = list(raw.items).map((i) => parseItem(i, pageCount)).filter(Boolean);
  const chunks = list(raw.chunks)
    .filter((c) => Number.isInteger(c?.item) && items[c.item])
    .map((c) => Object.freeze({ item: c.item, heading: isStr(c.heading) ? c.heading : null }));
  return { items: Object.freeze(items), chunks: Object.freeze(chunks) };
}

export function decodeVectors(bytes, count, dim) {
  const expected = count * dim * BYTES_PER_FLOAT32;
  if (bytes.byteLength !== expected) {
    throw new MvpdfError(`Search vectors are ${bytes.byteLength} bytes; expected ${expected}`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Float32Array.from({ length: count * dim }, (_, i) => view.getFloat32(i * BYTES_PER_FLOAT32, true));
}

function parseSearch(raw, blocks, chunkCount) {
  if (!raw || !isId(raw.vectors) || !blocks.has(raw.vectors)) return null;
  if (!Number.isInteger(raw.dim) || raw.dim <= 0 || raw.count !== chunkCount || !isStr(raw.browserModel)) {
    throw new MvpdfError("Search index metadata is invalid");
  }
  const vectors = decodeVectors(blocks.get(raw.vectors).data, raw.count, raw.dim);
  return Object.freeze({ dim: raw.dim, browserModel: raw.browserModel, vectors });
}

function parseToc(toc, pageCount) {
  return list(toc)
    .filter((t) => isStr(t?.title) && Number.isInteger(t.page) && t.page >= 0 && t.page < pageCount)
    .map((t) => Object.freeze({
      level: Math.min(Math.max(Number.parseInt(t.lvl, 10) || 1, 1), 4),
      title: t.title,
      page: t.page,
      y: isNum(t.y) ? t.y : 0,
    }));
}

function findMeta(blocks) {
  const metas = [...blocks.values()].filter((b) => b.kind === "META");
  if (metas.length !== 1) throw new MvpdfError("File must contain exactly one META block");
  const meta = parseJson(metas[0], "Metadata");
  if (meta?.format !== "mvpdf" || meta.version !== FORMAT_VERSION) {
    throw new MvpdfError(`Unsupported document version ${meta?.version}`);
  }
  return meta;
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += BASE64_CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + BASE64_CHUNK));
  }
  return btoa(binary);
}

function imageResolver(blocks, imageTypes) {
  const cache = new Map();
  return (id) => {
    const mime = imageTypes[id];
    const block = blocks.get(id);
    if (!block || block.kind !== "IMAG" || !IMAGE_TYPES.has(mime)) return null;
    if (!cache.has(id)) cache.set(id, `data:${mime};base64,${bytesToBase64(block.data)}`);
    return cache.get(id);
  };
}

/** Build the document model from verified blocks. */
export function buildDocument(blocks) {
  const meta = findMeta(blocks);
  const pageIds = list(meta.pages).filter(isId);
  const pageCount = pageIds.length;
  const pages = pageIds.map((id, i) => {
    if (!blocks.has(id)) throw new MvpdfError(`Page ${i + 1} is missing from the file`);
    return parsePage(blocks.get(id), i, pageCount);
  });
  const flow = parseFlow(isId(meta.flow) ? blocks.get(meta.flow) : null, pageCount);
  const imageUrl = imageResolver(blocks, meta.images && typeof meta.images === "object" ? meta.images : {});

  const pageSvg = (page) => {
    const block = blocks.get(page.svgId);
    if (!block || block.kind !== "SVGG") return null;
    // Images are inlined as data URIs: SVG shown through <img> may not fetch anything else.
    return utf8.decode(block.data).replace(IMAGE_REF, (_, id) => `xlink:href="${imageUrl(id) ?? ""}"`);
  };

  return Object.freeze({
    title: isStr(meta.title) && meta.title.trim() ? meta.title.trim() : "Untitled document",
    author: isStr(meta.author) ? meta.author : "",
    source: isStr(meta.source) ? meta.source : "",
    created: isStr(meta.created) ? meta.created : "",
    summary: isStr(meta.summary) ? meta.summary : "",
    tags: Object.freeze(list(meta.tags).filter(isStr)),
    toc: Object.freeze(parseToc(meta.toc, pageCount)),
    pages: Object.freeze(pages),
    flow,
    search: parseSearch(meta.search, blocks, flow.chunks.length),
    imageUrl,
    pageSvg,
  });
}

export async function loadDocument(buffer) {
  return buildDocument(await readContainer(buffer));
}

/** Dot product of the query against every row. With unit vectors this is cosine similarity. */
export function scoreAll(query, vectors, dim) {
  if (query.length !== dim) throw new MvpdfError(`Query has ${query.length} dims but document uses ${dim}`);
  const scores = new Float32Array(vectors.length / dim);
  for (let row = 0; row < scores.length; row += 1) {
    let sum = 0;
    for (let d = 0; d < dim; d += 1) sum += query[d] * vectors[row * dim + d];
    scores[row] = sum;
  }
  return scores;
}

/** The k best { index, score } pairs, highest first. */
export function topK(scores, k) {
  return Array.from(scores, (score, index) => ({ index, score }))
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(0, k));
}

export { MvpdfError };
