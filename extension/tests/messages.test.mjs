import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_HTML_CHARS, canSave, friendlyError, isViewerUrl, saveRequest } from "../messages.js";

test("only http and https pages can be saved", () => {
  assert.ok(canSave("https://example.com/post"));
  assert.ok(canSave("http://example.com"));
  assert.ok(!canSave("chrome://extensions"));
  assert.ok(!canSave("file:///Users/me/a.html"));
  assert.ok(!canSave(undefined));
});

test("opens tabs only on the local viewer with a document token", () => {
  assert.ok(isViewerUrl("http://127.0.0.1:8765/?doc=abcdefghijklmnop&name=a.mvpdf"));
  assert.ok(!isViewerUrl("http://127.0.0.1:8765/?name=a.mvpdf"));
  assert.ok(!isViewerUrl("https://evil.example/?doc=abc"));
  assert.ok(!isViewerUrl("http://127.0.0.1:8765/api/doc/abc?doc=1"));
  assert.ok(!isViewerUrl("javascript:alert(1)"));
  assert.ok(!isViewerUrl(undefined));
});

test("builds a save request with only the expected fields", () => {
  const request = saveRequest({
    url: "https://example.com/a", title: "T", byline: "B", lang: "en", html: "<p>x</p>", extra: 1,
  });
  assert.deepEqual(request, {
    type: "save", url: "https://example.com/a", title: "T", byline: "B", lang: "en", html: "<p>x</p>",
  });
});

test("rejects empty or oversized pages", () => {
  assert.throws(() => saveRequest({ html: "   " }), /readable content/);
  assert.throws(() => saveRequest(undefined), /readable content/);
  assert.throws(() => saveRequest({ html: "x".repeat(MAX_HTML_CHARS + 1) }), /too large/);
});

test("explains a missing native host install", () => {
  assert.match(friendlyError(new Error("Specified native messaging host not found.")), /install_mac\.py/);
  assert.match(friendlyError(new Error("Access to the specified native messaging host is forbidden.")), /install_mac/);
  assert.equal(friendlyError(new Error("Cannot access contents of url")), "This page can't be saved.");
  assert.equal(friendlyError(new Error("boom")), "boom");
});

test("detects PDFs by document type, or by URL when the page can't be scripted", async () => {
  const { isPdf } = await import("../messages.js");
  assert.ok(isPdf({ contentType: "application/pdf" }, "https://ex.com/view?id=3"));
  assert.ok(!isPdf({ contentType: "text/html" }, "https://ex.com/paper.pdf"));
  assert.ok(isPdf(null, "https://ex.com/files/Paper.PDF?dl=1"));
  assert.ok(!isPdf(null, "https://ex.com/article"));
  assert.ok(!isPdf(null, "not a url"));
});

test("builds a PDF save request for web URLs only", async () => {
  const { pdfRequest } = await import("../messages.js");
  assert.deepEqual(pdfRequest("https://ex.com/a.pdf"), { type: "save_pdf", url: "https://ex.com/a.pdf" });
  assert.throws(() => pdfRequest("file:///Users/me/a.pdf"), /http/);
});
