// Injected into the active tab after vendor/Readability.js (same isolated world).
// The value of the final expression is handed back to background.js.
(() => {
  const HEADINGS = "h1, h2, h3, h4, h5, h6";
  const WRAPPER_SLACK_CHARS = 24; // room for "[edit]"-style controls next to the heading

  const meta = (key) =>
    document.querySelector(`meta[name="${key}"], meta[property="${key}"]`)?.content?.trim() ?? "";

  // Sites (e.g. Wikipedia) wrap section headings in a <div> with an "[edit]" link. Readability
  // scores that wrapper as link-heavy boilerplate and drops it, heading included, so we lift the
  // heading out and drop the wrapper first. Runs on a clone; the live page is never touched.
  function liftWrappedHeadings(doc) {
    for (const heading of doc.querySelectorAll(HEADINGS)) {
      const wrapper = heading.parentElement;
      if (!wrapper || wrapper.tagName !== "DIV" || wrapper.firstElementChild !== heading) continue;
      const extra = wrapper.textContent.length - heading.textContent.length;
      if (extra <= WRAPPER_SLACK_CHARS) wrapper.replaceWith(heading);
    }
    return doc;
  }

  let article = null;
  try {
    article = new Readability(liftWrappedHeadings(document.cloneNode(true))).parse();
  } catch (err) {
    console.warn("MVPDF: Readability failed, saving the whole page instead", err);
  }
  return {
    url: location.href,
    title: (article?.title || meta("og:title") || document.title || "").trim(),
    byline: (article?.byline || meta("author") || "").trim(),
    lang: article?.lang || document.documentElement.lang || "",
    html: article?.content || document.body?.innerHTML || "",
    readable: Boolean(article?.content),
  };
})();
