import { HOST_NAME, canSave, friendlyError, isViewerUrl, saveRequest } from "./messages.js";

const BADGES = {
  busy: { text: "…", color: "#6b7280" },
  done: { text: "✓", color: "#15803d" },
  error: { text: "!", color: "#b91c1c" },
};
const DEFAULT_TITLE = "Save as .mvpdf";
const CLEAR_BADGE_MS = 5000;
const busyTabs = new Set();

async function showState(tabId, state, title = DEFAULT_TITLE) {
  const badge = BADGES[state];
  await Promise.all([
    chrome.action.setBadgeText({ tabId, text: badge?.text ?? "" }),
    badge && chrome.action.setBadgeBackgroundColor({ tabId, color: badge.color }),
    chrome.action.setTitle({ tabId, title }),
  ]);
  if (state === "done" || state === "error") {
    setTimeout(() => showState(tabId, "idle").catch(() => {}), CLEAR_BADGE_MS);
  }
}

async function extractArticle(tabId) {
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId },
    files: ["vendor/Readability.js", "extract.js"],
  });
  return injection?.result;
}

async function saveTab(tab) {
  const reply = await chrome.runtime.sendNativeMessage(HOST_NAME, saveRequest(await extractArticle(tab.id)));
  if (!reply?.ok) throw new Error(reply?.error ?? "The converter didn't answer.");
  if (!isViewerUrl(reply.url)) throw new Error("The converter returned an unexpected link.");
  await chrome.tabs.create({ url: reply.url, index: tab.index + 1, openerTabId: tab.id });
  return reply.file;
}

chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id === undefined || busyTabs.has(tab.id)) return;
  if (!canSave(tab.url)) {
    await showState(tab.id, "error", "Only web pages (http/https) can be saved.");
    return;
  }
  busyTabs.add(tab.id);
  try {
    await showState(tab.id, "busy", "Saving as .mvpdf… (the first save takes a few seconds)");
    const file = await saveTab(tab);
    await showState(tab.id, "done", `Saved ${file}`);
  } catch (err) {
    console.error("MVPDF save failed", err);
    await showState(tab.id, "error", `Couldn't save: ${friendlyError(err)}`);
  } finally {
    busyTabs.delete(tab.id);
  }
});
