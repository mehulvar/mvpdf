// Pure helpers shared by background.js and its tests.

export const HOST_NAME = "com.mvpdf.host";
export const VIEWER_ORIGIN = "http://127.0.0.1:8765";
export const MAX_HTML_CHARS = 30 * 1024 * 1024; // the native host refuses messages over 32 MiB

const INSTALL_HINT = "MVPDF isn't installed for this browser. Run: python viewer/install_mac.py";

export function canSave(url) {
  return /^https?:\/\//i.test(url ?? "");
}

/** Only ever open tabs on the local viewer, whatever the host replies. */
export function isViewerUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin === VIEWER_ORIGIN && parsed.pathname === "/" && parsed.searchParams.has("doc");
  } catch {
    return false;
  }
}

export function saveRequest(extracted) {
  if (!extracted?.html?.trim()) throw new Error("Couldn't find readable content on this page.");
  if (extracted.html.length > MAX_HTML_CHARS) throw new Error("This page is too large to save.");
  const { url, title, byline, lang, html } = extracted;
  return { type: "save", url, title, byline, lang, html };
}

export function friendlyError(err) {
  const message = String(err?.message ?? err ?? "Unknown error");
  if (/native messaging host not found|forbidden/i.test(message)) return INSTALL_HINT;
  if (/Cannot access|cannot be scripted|chrome:\/\//i.test(message)) return "This page can't be saved.";
  return message;
}
