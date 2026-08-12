// Runs on every page (unlike content.js, which is scoped to the known job-listing sites) -
// application forms live on arbitrary company/ATS domains that can't be listed ahead of time.
// Two independent things live here:
//   1. A floating button (like the reference "click the logo to open the panel" widgets) so
//      the side panel can be opened from ANY page, not just via the toolbar icon.
//   2. Small "AI answer" buttons injected next to essay-type <textarea> questions, so a
//      free-response question can be answered in place without leaving the page - but only
//      once a job is actually being tracked (chrome.storage.local's jobapplier_current_job),
//      so unrelated pages (Gmail, Twitter, random forms) don't get cluttered with buttons that
//      have no job/resume context to draw from.
if (!window.__jobapplierFloatingWidgetLoaded) {
window.__jobapplierFloatingWidgetLoaded = true;

// Same denylist as autofill.js (duplicated - each content script file is standalone, no
// shared module loading set up for these) - never offer to auto-generate an answer for a
// consent/attestation question, and skip page chrome unrelated to the application itself.
const CONSENT_DENYLIST = [
  "certify", "i agree", "agree to the", "consent", "acknowledge",
  "terms and condition", "terms of service", "true and correct",
  "background check", "e-verify",
];
const NOISE_DENYLIST = ["cookie", "newsletter", "subscribe", "live chat", "chat with us"];

function isSkippableQuestion(questionText) {
  const q = questionText.trim().toLowerCase();
  return CONSENT_DENYLIST.some((t) => q.includes(t)) || NOISE_DENYLIST.some((t) => q.includes(t));
}

// Reloading the extension in chrome://extensions invalidates the connection for any content
// script already injected into an already-open tab - every chrome.* call in it then throws
// "Extension context invalidated" until that tab itself is reloaded. Every chrome.* call below
// goes through this so that hits a soft failure (and stops the observer re-triggering it on
// every DOM mutation) instead of an uncaught exception in the page's console.
let contextInvalidated = false;

function safeCall(fn) {
  if (contextInvalidated) return;
  try {
    fn();
  } catch (e) {
    if (String(e).includes("Extension context invalidated")) {
      contextInvalidated = true;
      if (widgetObserver) widgetObserver.disconnect();
    } else {
      throw e;
    }
  }
}

let widgetObserver = null;

// Same generic label-resolution priority order as autofill.js's resolveLabelText().
function resolveLabelText(field) {
  if (field.id) {
    const forLabel = document.querySelector(`label[for="${CSS.escape(field.id)}"]`);
    if (forLabel && forLabel.innerText.trim()) return forLabel.innerText.trim();
  }
  const wrappingLabel = field.closest("label");
  if (wrappingLabel && wrappingLabel.innerText.trim()) return wrappingLabel.innerText.trim();
  const ariaLabel = field.getAttribute("aria-label");
  if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
  const labelledBy = field.getAttribute("aria-labelledby");
  if (labelledBy) {
    const el = document.getElementById(labelledBy);
    if (el && el.innerText.trim()) return el.innerText.trim();
  }
  const placeholder = field.getAttribute("placeholder");
  if (placeholder && placeholder.trim()) return placeholder.trim();
  return "";
}

// --- 1. Floating button that opens the side panel ---
//
// Hides itself once the panel is actually open (tracked via chrome.storage.local's
// jobapplier_panel_open, set by panel.js on load/unload) and reappears once it's closed again -
// no point offering a second way to open something that's already open. A separate small "x"
// lets the user dismiss the widget entirely (persisted, so it stays gone across page loads).

const PANEL_OPEN_KEY = "jobapplier_panel_open";
const WIDGET_DISMISSED_KEY = "jobapplier_widget_dismissed";

function injectFloatingButton() {
  if (document.getElementById("jobapplier-floating-widget")) return;

  const wrapper = document.createElement("div");
  wrapper.id = "jobapplier-floating-widget";
  Object.assign(wrapper.style, {
    position: "fixed", top: "50%", right: "0", transform: "translateY(-50%)",
    zIndex: "2147483647",
  });

  const openBtn = document.createElement("button");
  openBtn.id = "jobapplier-floating-btn";
  openBtn.title = "Open jobApplier";
  openBtn.type = "button";
  openBtn.textContent = "‹"; // ‹ - points toward the page, hinting "opens a panel from here"
  Object.assign(openBtn.style, {
    width: "32px", height: "44px", borderRadius: "8px 0 0 8px",
    border: "none", background: "#9333ea", color: "#fff", cursor: "pointer",
    fontSize: "20px", fontWeight: "700", lineHeight: "1",
    boxShadow: "0 2px 10px rgba(0,0,0,0.25)", padding: "0",
  });
  openBtn.addEventListener("click", () => {
    wrapper.style.display = "none"; // optimistic - panel.js's own load signal confirms this
    safeCall(() => chrome.runtime.sendMessage({ type: "JOBAPPLIER_OPEN_PANEL" }));
  });

  const closeBtn = document.createElement("button");
  closeBtn.id = "jobapplier-floating-close";
  closeBtn.title = "Hide this button";
  closeBtn.type = "button";
  closeBtn.textContent = "✕";
  Object.assign(closeBtn.style, {
    position: "absolute", top: "-8px", left: "-8px", width: "16px", height: "16px",
    borderRadius: "50%", border: "none", background: "#4b1d80", color: "#fff",
    cursor: "pointer", fontSize: "9px", lineHeight: "16px", padding: "0",
  });
  closeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    wrapper.style.display = "none";
    safeCall(() => chrome.storage.local.set({ [WIDGET_DISMISSED_KEY]: true }));
  });

  wrapper.style.position = "fixed";
  wrapper.style.padding = "8px 0 0 8px"; // room for the close button to overhang top-left
  const inner = document.createElement("div");
  inner.style.position = "relative";
  inner.appendChild(openBtn);
  inner.appendChild(closeBtn);
  wrapper.appendChild(inner);
  document.body.appendChild(wrapper);

  safeCall(() => {
    chrome.storage.local.get([PANEL_OPEN_KEY, WIDGET_DISMISSED_KEY], (data) => {
      if (data[PANEL_OPEN_KEY] || data[WIDGET_DISMISSED_KEY]) wrapper.style.display = "none";
    });
  });
}

safeCall(() => {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    const wrapper = document.getElementById("jobapplier-floating-widget");
    if (!wrapper) return;
    if (PANEL_OPEN_KEY in changes) {
      wrapper.style.display = changes[PANEL_OPEN_KEY].newValue ? "none" : "";
    }
    if (WIDGET_DISMISSED_KEY in changes && changes[WIDGET_DISMISSED_KEY].newValue) {
      wrapper.style.display = "none";
    }
  });
});

// --- 2. Per-field "AI answer" buttons for essay-type questions ---

function styleAnswerButton(btn) {
  Object.assign(btn.style, {
    display: "inline-block", marginTop: "4px", padding: "4px 10px",
    fontSize: "12px", fontWeight: "600", color: "#9333ea", background: "rgba(147,51,234,0.1)",
    border: "none", borderRadius: "999px", cursor: "pointer",
  });
}

function injectAnswerButton(textarea, questionText) {
  textarea.dataset.jobapplierIconAdded = "1";

  const row = document.createElement("div");
  row.className = "jobapplier-ai-row";
  Object.assign(row.style, { display: "flex", gap: "6px", marginTop: "4px", alignItems: "center" });

  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "✨ AI answer";
  btn.className = "jobapplier-ai-answer-btn";
  styleAnswerButton(btn);

  // Optional tweak instructions (e.g. "mention my internship at X", "keep it under 80 words") -
  // read fresh on every click, so the same input works for both the first generation and any
  // regenerate that follows, no separate "edit" mode needed.
  const notesInput = document.createElement("input");
  notesInput.type = "text";
  notesInput.placeholder = "Add instructions (optional)";
  notesInput.className = "jobapplier-ai-notes-input";
  Object.assign(notesInput.style, {
    flex: "1", minWidth: "140px", maxWidth: "260px", fontSize: "12px",
    padding: "4px 8px", borderRadius: "999px", border: "1px solid rgba(147,51,234,0.3)",
  });

  btn.addEventListener("click", () => {
    btn.disabled = true;
    btn.textContent = "Generating...";
    safeCall(() => {
      chrome.runtime.sendMessage(
        { type: "JOBAPPLIER_GENERATE_ANSWER", question: questionText, notes: notesInput.value.trim() },
        (response) => {
          if (chrome.runtime.lastError || !response || response.error) {
            btn.textContent = "Failed - retry";
            btn.disabled = false;
            return;
          }
          textarea.value = response.answer;
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          textarea.dispatchEvent(new Event("change", { bubbles: true }));
          btn.textContent = "✨ Regenerate";
          btn.disabled = false;
        }
      );
    });
    if (contextInvalidated) {
      btn.textContent = "Reload this page to use AI answer";
      btn.disabled = true;
    }
  });

  row.appendChild(btn);
  row.appendChild(notesInput);
  textarea.insertAdjacentElement("afterend", row);
}

function scanForEssayQuestions() {
  safeCall(() => {
    chrome.storage.local.get("jobapplier_current_job", (data) => {
      if (!data.jobapplier_current_job) return; // no job being tracked - nothing to answer from
      document.querySelectorAll("textarea").forEach((textarea) => {
        if (textarea.dataset.jobapplierIconAdded) return;
        if (textarea.offsetHeight < 40 || textarea.offsetWidth < 100) return; // skip tiny/hidden ones
        const questionText = resolveLabelText(textarea);
        if (!questionText || isSkippableQuestion(questionText)) return;
        injectAnswerButton(textarea, questionText);
      });
    });
  });
}

injectFloatingButton();
scanForEssayQuestions();

// Most application forms are SPAs that render fields after the initial page load - a single
// document_idle pass would miss those, so keep watching for new textareas showing up.
widgetObserver = new MutationObserver(() => scanForEssayQuestions());
widgetObserver.observe(document.documentElement, { childList: true, subtree: true });
}
