import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { deflateSync } from "node:zlib";

import { MAGIC, MvpdfError, crc32, readContainer } from "../src/container.js";
import { buildDocument, decodeVectors, itemText, loadDocument, safeHref, scoreAll, topK } from "../src/document.js";

const SAMPLE = new URL("../../samples/solar_energy.mvpdf", import.meta.url);
const encoder = new TextEncoder();

/** Minimal container writer mirroring compiler/mvpdf/container.py (ids are arbitrary here). */
function makeContainer(blocks, { major = 2 } = {}) {
  const parts = [];
  const header = new Uint8Array(18);
  header.set(MAGIC);
  const hv = new DataView(header.buffer);
  hv.setUint16(10, major, true);
  hv.setUint32(14, blocks.length, true);
  parts.push(header);
  blocks.forEach(({ kind, id, data, deflate = false }) => {
    const raw = typeof data === "string" ? encoder.encode(data) : data;
    const payload = deflate ? new Uint8Array(deflateSync(raw)) : raw;
    const head = new Uint8Array(36);
    head.set(encoder.encode(kind), 0);
    head[4] = deflate ? 1 : 0;
    head.set(Buffer.from(id, "hex"), 8);
    const v = new DataView(head.buffer);
    v.setUint32(24, payload.length, true);
    v.setUint32(28, raw.length, true);
    v.setUint32(32, crc32(payload), true);
    parts.push(head, payload);
  });
  return Buffer.concat(parts);
}

const id = (n) => n.toString(16).padStart(32, "0");

function minimalDoc(overrides = {}) {
  const meta = {
    format: "mvpdf", version: 2, title: "T", pages: [id(2)], flow: id(3),
    images: { [id(5)]: "image/png" }, toc: [{ lvl: 1, title: "Start", page: 0, y: 10 }],
    search: null, ...overrides,
  };
  const page = {
    w: 100, h: 200, svg: id(4),
    text: [[1, 2, 3, 4, "hi"], ["bad"]],
    links: [
      { r: [0, 0, 1, 1], uri: "https://ok.example/" },
      { r: [0, 0, 1, 1], uri: "javascript:alert(1)" },
      { r: [0, 0, 1, 1], page: 0, y: 5 },
      { r: [0, 0, 1, 1], page: 7 },
    ],
  };
  const flow = {
    items: [
      { k: "h", lvl: 1, runs: [{ t: "Title" }], page: 0, bbox: [0, 0, 10, 10] },
      { k: "p", runs: [{ t: "Go " }, { t: "here", uri: "https://x.example/" }, { t: " now", uri: "data:text/html,x" }] },
      { k: "img", ref: id(5) },
      { k: "script", runs: [] },
    ],
    chunks: [{ item: 1, heading: "Title" }, { item: 99 }],
  };
  return makeContainer([
    { kind: "META", id: id(1), data: JSON.stringify(meta) },
    { kind: "PAGE", id: id(2), data: JSON.stringify(page) },
    { kind: "FLOW", id: id(3), data: JSON.stringify(flow), deflate: true },
    { kind: "SVGG", id: id(4), data: `<svg><image xlink:href="mvpdf:${id(5)}"/></svg>` },
    { kind: "IMAG", id: id(5), data: new Uint8Array([1, 2, 3]) },
  ]);
}

test("crc32 matches the standard check value", () => {
  assert.equal(crc32(encoder.encode("123456789")), 0xcbf43926);
});

test("safeHref keeps only web and mail links", () => {
  assert.equal(safeHref("https://a.example/x"), "https://a.example/x");
  assert.equal(safeHref("mailto:me@example.com"), "mailto:me@example.com");
  ["javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "not a url", 42, null]
    .forEach((bad) => assert.equal(safeHref(bad), null));
});

test("readContainer rejects non-mvpdf, wrong versions, corruption and truncation", async () => {
  await assert.rejects(readContainer(encoder.encode("%PDF-1.7 hello world....")), /Not an .mvpdf/);
  await assert.rejects(readContainer(new Uint8Array(4)), /too short/);
  await assert.rejects(readContainer(makeContainer([], { major: 9 })), /version 9/);

  const good = minimalDoc();
  const corrupt = Buffer.from(good);
  corrupt[corrupt.length - 2] ^= 0xff;
  await assert.rejects(readContainer(corrupt), /Checksum mismatch/);
  await assert.rejects(readContainer(good.subarray(0, good.length - 5)), /truncated/);
});

test("readContainer refuses blocks that inflate past their declared size", async () => {
  const raw = new Uint8Array(100_000);
  const payload = new Uint8Array(deflateSync(raw));
  const bytes = makeContainer([{ kind: "SVGG", id: id(1), data: payload }]);
  const view = new DataView(bytes.buffer, bytes.byteOffset);
  bytes[18 + 4] = 1; // mark deflated
  view.setUint32(18 + 28, 10, true); // lie: claims 10 bytes raw
  await assert.rejects(readContainer(bytes), /more data than it declares/);
});

test("buildDocument validates and sanitises untrusted content", async () => {
  const doc = buildDocument(await readContainer(minimalDoc()));

  assert.equal(doc.title, "T");
  assert.equal(doc.pages[0].text.length, 1, "malformed text spans are dropped");
  assert.deepEqual(doc.pages[0].links.map((l) => l.target), [
    { href: "https://ok.example/" }, { page: 0, y: 5 },
  ]);
  assert.deepEqual(doc.flow.items.map((i) => i.kind), ["h", "p", "img"]);
  assert.deepEqual(doc.flow.items[1].runs, [
    { text: "Go " }, { text: "here", href: "https://x.example/" }, { text: " now" },
  ]);
  assert.equal(itemText(doc.flow.items[1]), "Go here now");
  assert.equal(doc.flow.chunks.length, 1, "chunks pointing at missing items are dropped");
  assert.equal(doc.toc[0].title, "Start");
  assert.equal(doc.search, null);
  assert.equal(doc.imageUrl(id(5)), "data:image/png;base64,AQID");
  assert.match(doc.pageSvg(doc.pages[0]), /xlink:href="data:image\/png;base64,AQID"/);
});

test("buildDocument requires exactly one supported META block", async () => {
  const noMeta = makeContainer([{ kind: "FLOW", id: id(1), data: "{}" }]);
  assert.throws(() => buildDocument(new Map()), /exactly one META/);
  await assert.rejects(loadDocument(noMeta), /exactly one META/);
  const oldVersion = makeContainer([{ kind: "META", id: id(1), data: '{"format":"mvpdf","version":1}' }]);
  await assert.rejects(loadDocument(oldVersion), MvpdfError);
});

test("decodeVectors checks the byte length", () => {
  const bytes = new Uint8Array(new Float32Array([1, 2, 3, 4]).buffer);
  assert.deepEqual(Array.from(decodeVectors(bytes, 2, 2)), [1, 2, 3, 4]);
  assert.throws(() => decodeVectors(bytes, 3, 2), /expected 24/);
});

test("scoreAll and topK rank by cosine similarity", () => {
  const vectors = new Float32Array([1, 0, 0, 1, 0.6, 0.8]);
  const ranked = topK(scoreAll(new Float32Array([0, 1]), vectors, 2), 2);
  assert.deepEqual(ranked.map((r) => r.index), [1, 2]);
  assert.throws(() => scoreAll(new Float32Array(3), vectors, 2), /3 dims/);
});

test("the converted sample PDF loads end to end", async () => {
  const doc = await loadDocument(await readFile(SAMPLE));
  assert.equal(doc.title, "Solar Energy: A Practical Overview");
  assert.equal(doc.pages.length, 2);
  assert.ok(doc.pages[0].links.some((l) => l.target.href?.startsWith("https://www.energy.gov")));
  assert.ok(doc.pages[0].text.length > 10);
  assert.ok(doc.pageSvg(doc.pages[0]).includes("data:image/png;base64,"));
  assert.equal(doc.search.dim, 384);
  assert.equal(doc.search.vectors.length, doc.flow.chunks.length * 384);
  assert.ok(doc.toc.length >= 2);
});
