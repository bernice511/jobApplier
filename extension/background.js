// Opens the side panel when the toolbar icon is clicked (MV3 requires opting into this
// explicitly - it's not the default action for a browser-action click).
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

const BACKEND_URL = "http://127.0.0.1:5050";
const JOB_CACHE_STORAGE_KEY = "jobapplier_job_cache";

function jobIdentity(job) {
  return job && (job.id || job.url);
}

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
