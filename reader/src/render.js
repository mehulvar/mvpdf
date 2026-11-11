// Reflow view, overview panel, outline and search results.
// All document text goes through textContent, never innerHTML.

import { itemText, safeHref } from "./document.js";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function linkNode(run, onInternalLink) {
  const a = el("a", "flow-link", run.text);
  if (run.href) {
    a.href = run.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = run.href;
    return a;
  }
  a.href = `#page-${run.page + 1}`;
  a.addEventListener("click", (event) => {
    event.preventDefault();
    onInternalLink(run.page, run.y);
  });
  return a;
}

function runsInto(node, runs, onInternalLink) {
  runs.forEach((run) => {
    node.append(run.href || run.page !== undefined ? linkNode(run, onInternalLink) : run.text);
  });
  return node;
}

function flowNode(item, index, doc, onInternalLink) {
  if (item.kind === "img") {
    const src = doc.imageUrl(item.ref);
    if (!src) return null;
    const figure = el("figure", "flow-figure");
    const img = el("img");
    img.src = src;
    img.alt = "";
    img.loading = "lazy";
    figure.append(img);
    return figure;
  }
  const node = item.kind === "h" ? el(`h${item.level + 1}`, "flow-heading") : el("p", "chunk");
  node.id = `flow-${index}`;
  return runsInto(node, item.runs, onInternalLink);
}

export function renderReflow(container, doc, onInternalLink) {
  const nodes = [];
  let lastPage = null;
  doc.flow.items.forEach((item, index) => {
    if (item.page !== null && item.page !== lastPage && doc.pages.length > 1) {
      nodes.push(el("div", "page-marker", `Page ${item.page + 1}`));
      lastPage = item.page;
    }
    const node = flowNode(item, index, doc, onInternalLink);
    if (node) nodes.push(node);
  });
  container.replaceChildren(...nodes);
}

/** Flow index of the first item at or below (page, y): used for internal links in reflow. */
export function flowIndexFor(doc, page, y) {
  const onPage = doc.flow.items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.page === page && item.kind !== "img");
  const below = onPage.find(({ item }) => (item.bbox?.[3] ?? 0) >= y);
  return (below ?? onPage[0])?.index ?? null;
}

export function highlightFlow(container, hits) {
  container.querySelectorAll(".chunk.match, .chunk.best").forEach((node) => {
    node.classList.remove("match", "best");
    node.removeAttribute("data-score");
  });
  hits.forEach(({ item, score, best }) => {
    const node = container.querySelector(`#flow-${item}`);
    if (!node) return;
    node.classList.add(best ? "best" : "match");
    node.dataset.score = `${Math.round(score * 100)}% match`;
  });
}

/** A saved web page links back to where it came from; a file name stays plain text. */
function sourceNode(source) {
  const href = /^https?:/i.test(source ?? "") ? safeHref(source) : null;
  if (!href) return source;
  const link = el("a", "source-link", new URL(href).hostname.replace(/^www\./, ""));
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = href;
  return link;
}

export function renderOverview(root, doc, onTagClick) {
  root.querySelector("#doc-title").textContent = doc.title;
  const meta = [
    doc.author,
    sourceNode(doc.source),
    doc.pages.length ? `${doc.pages.length} page${doc.pages.length === 1 ? "" : "s"}` : "",
    doc.created && new Date(doc.created).toLocaleDateString(),
  ].filter(Boolean);
  root.querySelector("#doc-meta").replaceChildren(
    ...meta.flatMap((part, i) => (i ? [" · ", part] : [part])),
  );
  root.querySelector("#doc-summary").textContent = doc.summary || "No summary available.";
  root.querySelector("#doc-tags").replaceChildren(...doc.tags.map((tag) => {
    const button = el("button", "tag", tag);
    button.type = "button";
    button.addEventListener("click", () => onTagClick(tag));
    const item = el("li");
    item.append(button);
    return item;
  }));
}

export function renderOutline(list, doc, onPick) {
  const entries = doc.toc.length
    ? doc.toc
    : doc.flow.items
      .filter((item) => item.kind === "h" && item.page !== null)
      .map((item) => ({ level: item.level, title: itemText(item), page: item.page, y: item.bbox?.[1] ?? 0 }));
  list.replaceChildren(...entries.map((entry) => {
    const button = el("button", "outline-item", entry.title);
    button.type = "button";
    button.style.paddingLeft = `${0.5 + (entry.level - 1) * 0.9}rem`;
    button.addEventListener("click", () => onPick(entry.page, entry.y));
    const item = el("li");
    item.append(button);
    return item;
  }));
  return entries.length;
}

export function renderResults(list, hits, onPick) {
  list.replaceChildren(...hits.map((hit) => {
    const button = el("button", "result");
    button.type = "button";
    const where = hit.page !== null ? `p. ${hit.page + 1}` : "";
    button.append(
      el("span", "result-score", `${Math.round(hit.score * 100)}%`),
      el("span", "result-text", hit.heading ? `${hit.heading} — ${hit.text}` : hit.text),
      el("span", "result-page", where),
    );
    button.addEventListener("click", () => onPick(hit));
    const item = el("li");
    item.append(button);
    return item;
  }));
}
