// Opens the side panel when the toolbar icon is clicked (MV3 requires opting into this
// explicitly - it's not the default action for a browser-action click).
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

const BACKEND_URL = "http://127.0.0.1:5050";
const JOB_CACHE_STORAGE_KEY = "jobapplier_job_cache";
const PANEL_OPEN_KEY = "jobapplier_panel_open";

function jobIdentity(job) {
  return job && (job.id || job.url);
}

// panel.js opens a long-lived port on load (name: "jobapplier-panel") purely so this
// onDisconnect fires - Chrome guarantees that when the panel's document is torn down, which is
// the reliable way to know the panel actually closed (a page-level "pagehide" listener in
// panel.js itself was tried first and wasn't reliable for side panel documents - the "open
// panel" flag could get stuck true forever if it didn't fire, permanently hiding the floating
// widget's button). Set true as soon as the port connects, not just on background.js startup,
// so a stale true from a crashed/killed previous panel session can't linger past a new open.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "jobapplier-panel") return;
  chrome.storage.local.set({ [PANEL_OPEN_KEY]: true });
  port.onDisconnect.addListener(() => {
    chrome.storage.local.set({ [PANEL_OPEN_KEY]: false });
  });
});

// The floating widget's "open panel" button (floating_widget.js) - sidePanel.open() must be
// called in direct response to a user gesture, which this message handler is (it only ever
// fires as the immediate result of the button's click listener).
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "JOBAPPLIER_OPEN_PANEL" && sender.tab) {
    chrome.sidePanel.open({ tabId: sender.tab.id }).catch(() => {});
    return;
  }

  if (message.type === "JOBAPPLIER_GENERATE_ANSWER") {
    generateAnswer(message.question, message.notes)
      .then((answer) => sendResponse({ answer }))
      .catch((err) => sendResponse({ error: String(err) }));
    return true; // keep the message channel open for the async response above
  }
});

// Pulls whatever job/resume context is already known (set by content.js's extraction and
// panel.js's analyze/generate calls, both via chrome.storage.local) so a question can be
// answered from the SAME context the side panel already has, without requiring the panel to
// be open - the floating widget's button works standalone on any application page. notes is
// optional free-text tweak instructions from the floating widget's inline input (e.g. "mention
// my internship at X", "keep it under 80 words"), used for both the first generation and any
// regenerate that follows.
async function generateAnswer(question, notes) {
  const local = await chrome.storage.local.get(["jobapplier_current_job", JOB_CACHE_STORAGE_KEY]);
  const job = local.jobapplier_current_job;
  const jdText = (job && job.description) || "";

  let resumePreviewHtml = "";
  const identity = jobIdentity(job);
  if (identity) {
    const cache = local[JOB_CACHE_STORAGE_KEY] || {};
    const entry = cache[identity];
    if (entry && entry.generateResult) {
      resumePreviewHtml = entry.generateResult.resume_preview_html || "";
    }
  }

  const resp = await fetch(`${BACKEND_URL}/api/answer-question`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, jd_text: jdText, resume_preview_html: resumePreviewHtml, notes: notes || "" }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || "Failed to generate an answer.");
  return data.answer;
}
