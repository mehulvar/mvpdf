// extractPage() runs inside the web page (chrome.scripting.executeScript({ func })), after
// vendor/Readability.js has been injected into the same isolated world. Chrome serializes the
// function's source, so it must be self-contained: no imports or outer variables.

export function extractPage() {
  const HEADINGS = "h1, h2, h3, h4, h5, h6";
  const WRAPPER_SLACK_CHARS = 24; // room for "[edit]"-style controls next to a heading
  const MIN_READABLE_CHARS = 200;

  const meta = (key) =>
    document.querySelector(`meta[name="${key}"], meta[property="${key}"]`)?.content?.trim() ?? "";

  // Sites like Wikipedia wrap section headings in a <div> with an "[edit]" link. Readability
  // scores that wrapper as link-heavy boilerplate and drops the heading with it, so lift the
  // heading out first. Works on a clone; the live page is never modified.
  function liftWrappedHeadings(doc) {
    for (const heading of doc.querySelectorAll(HEADINGS)) {
      const wrapper = heading.parentElement;
      if (!wrapper || wrapper.tagName !== "DIV" || wrapper.firstElementChild !== heading) continue;
      if (wrapper.textContent.length - heading.textContent.length <= WRAPPER_SLACK_CHARS) {
        wrapper.replaceWith(heading);
      }
    }
    return doc;
  }

  // Last resort for pages Readability can't parse: the visible text, one paragraph per line.
  function visibleTextHtml() {
    const escapeHtml = (text) =>
      text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return (document.body?.innerText ?? "")
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => `<p>${escapeHtml(line)}</p>`)
      .join("");
  }

  let article = null;
  let problem = "";
  if (typeof Readability !== "function") {
    problem = "Readability did not load";
  } else {
    try {
      article = new Readability(liftWrappedHeadings(document.cloneNode(true))).parse();
    } catch (err) {
      problem = `Readability failed: ${err?.message ?? err}`;
    }
  }

  const readable = (article?.textContent?.trim().length ?? 0) >= MIN_READABLE_CHARS;
  return {
    url: location.href,
    title: (article?.title || meta("og:title") || document.title || "").trim(),
    byline: (article?.byline || meta("author") || "").trim(),
    lang: article?.lang || document.documentElement.lang || "",
    html: readable ? article.content : visibleTextHtml(),
    readable,
    problem,
  };
}
