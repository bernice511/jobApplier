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

function injectFloatingButton() {
  if (document.getElementById("jobapplier-floating-btn")) return;
  const btn = document.createElement("button");
  btn.id = "jobapplier-floating-btn";
  btn.title = "Open jobApplier";
  btn.type = "button";
  const img = document.createElement("img");
  img.src = chrome.runtime.getURL("icons/icon32.png");
  img.style.width = "22px";
  img.style.height = "22px";
  img.style.pointerEvents = "none";
  btn.appendChild(img);
  Object.assign(btn.style, {
    position: "fixed", top: "50%", right: "0", transform: "translateY(-50%)",
    zIndex: "2147483647", width: "44px", height: "44px", borderRadius: "50% 0 0 50%",
    border: "none", background: "#9333ea", cursor: "pointer",
    display: "flex", alignItems: "center", justifyContent: "center",
    boxShadow: "0 2px 10px rgba(0,0,0,0.25)", padding: "0",
  });
  btn.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "JOBAPPLIER_OPEN_PANEL" });
  });
  document.body.appendChild(btn);
}

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
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "✨ AI answer";
  btn.className = "jobapplier-ai-answer-btn";
  styleAnswerButton(btn);

  btn.addEventListener("click", () => {
    btn.disabled = true;
    btn.textContent = "Generating...";
    chrome.runtime.sendMessage(
      { type: "JOBAPPLIER_GENERATE_ANSWER", question: questionText },
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

  textarea.insertAdjacentElement("afterend", btn);
}

function scanForEssayQuestions() {
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
}

injectFloatingButton();
scanForEssayQuestions();

// Most application forms are SPAs that render fields after the initial page load - a single
// document_idle pass would miss those, so keep watching for new textareas showing up.
new MutationObserver(() => scanForEssayQuestions()).observe(document.documentElement, {
  childList: true, subtree: true,
});
}
