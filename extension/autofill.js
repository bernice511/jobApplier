// Fills in application-form fields on the current job page from data supplied by panel.js
// (GET /api/autofill-profile + the tailored resume/cover-letter PDFs) - NEVER clicks
// Submit/Apply/Next/Review, never auto-advances a multi-step form, and never guesses an
// answer for a question it can't confidently match. The user reviews every filled field
// (each gets a visible outline so a wrong match isn't easy to skim past) and submits by hand.
//
// Deliberately NOT statically declared in manifest.json's content_scripts (unlike content.js)
// - company application forms can live on ANY domain (a company's own site, not just the
// known ATS platforms), so panel.js injects this on demand via chrome.scripting.executeScript
// at the moment "Autofill" is clicked, rather than needing every possible domain listed ahead
// of time. That means a repeat click on the same page (without a reload in between) could
// inject this file again - the loaded-guard below makes that a no-op instead of registering a
// second message listener and double-filling everything.
if (!window.__jobapplierAutofillLoaded) {
window.__jobapplierAutofillLoaded = true;

// Consent/attestation/certification checkboxes and radios are a different category from
// screening questions - auto-answering them is functionally closer to submitting on the
// user's behalf than filling a field, so anything matching these is skipped BEFORE the
// matcher ever sees it, regardless of field type.
const CONSENT_DENYLIST = [
  "certify",
  "i agree",
  "agree to the",
  "consent",
  "acknowledge",
  "terms and condition",
  "terms of service",
  "true and correct",
  "background check",
  "e-verify",
];

// Page chrome unrelated to the application itself (cookie-consent widgets, chat/support
// launchers, newsletter signups) sometimes has real <input>/<label> pairs that satisfy the
// generic label-walker just as well as an actual screening question would - e.g. a
// cookie-preference-center's own "search cookies" filter box. Skipped outright, same as the
// consent denylist, rather than reported as an unmatched question needing an answer.
// Deliberately NOT including a bare "search" term - some legitimate screening questions
// mention it (e.g. "How did you search for this job?"), so it'd cause more harm than good.
const NOISE_DENYLIST = ["cookie", "newsletter", "subscribe", "live chat", "chat with us"];

function isNoiseQuestion(questionText) {
  const q = questionText.trim().toLowerCase();
  return NOISE_DENYLIST.some((term) => q.includes(term));
}

// Question-text substring -> profile field, ported from apply_easy.py's QUESTION_KEY_HINTS
// (kept as a plain duplicated JS table rather than shared codegen infra - it's ~15 entries).
const QUESTION_KEY_HINTS = [
  ["first name", "first_name"],
  ["given name", "first_name"],
  ["last name", "last_name"],
  ["family name", "last_name"],
  ["surname", "last_name"],
  ["phone", "phone"],
  ["email", "email"],
  ["authorized to work", "work_authorization"],
  ["legally authorized", "work_authorization"],
  ["sponsorship", "requires_sponsorship"],
  ["notice period", "notice_period_days"],
  ["salary", "salary_expectation"],
  ["linkedin", "linkedin_url"],
  ["github", "github_url"],
  ["portfolio", "website_url"],
  ["website", "website_url"],
  ["hear about", "how_heard"],
  ["how did you find", "how_heard"],
  ["relocate", "willing_to_relocate"],
  ["currently employed", "currently_employed"],
  ["start date", "available_start_date"],
  ["available to start", "available_start_date"],
  ["when can you start", "available_start_date"],
];

function isConsentQuestion(questionText) {
  const q = questionText.trim().toLowerCase();
  return CONSENT_DENYLIST.some((term) => q.includes(term));
}

function matchAnswer(questionText, profile) {
  const q = questionText.trim().toLowerCase();

  for (const [substring, answer] of Object.entries(profile.patterns || {})) {
    if (q.includes(substring.toLowerCase())) return answer;
  }

  for (const [hint, key] of QUESTION_KEY_HINTS) {
    if (q.includes(hint) && profile[key]) return profile[key];
  }

  if (q.includes("years of experience") && profile.years_experience_default) {
    return profile.years_experience_default;
  }

  return null;
}

// Generic label association, used for every site except LinkedIn's own form_group markup
// below - priority order matches the most common ways a form associates a label with a
// field across arbitrary sites, checked in order of how unambiguous each signal is.
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

function dispatchInputEvents(el) {
  // Dispatch BOTH events - React-controlled inputs listen for "input", some legacy/plain
  // forms only listen for "change".
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function markFilled(el) {
  el.style.outline = "2px solid #2563eb";
  el.title = "Filled by jobApplier - review before submitting";
}

function fillTextLike(el, value) {
  el.value = value;
  dispatchInputEvents(el);
  markFilled(el);
}

function fillSelect(el, value) {
  const options = Array.from(el.options);
  const target = value.trim().toLowerCase();
  let match = options.find((o) => o.text.trim().toLowerCase() === target);
  if (!match) {
    match = options.find(
      (o) => o.text.toLowerCase().includes(target) || target.includes(o.text.trim().toLowerCase())
    );
  }
  if (!match) return false;
  el.value = match.value;
  dispatchInputEvents(el);
  markFilled(el);
  return true;
}

// radios: [{input, labelText}, ...] - one entry per option in the group. Bidirectional
// substring match against the answer, same as apply_easy.py's Playwright version. If nothing
// matches, leave it unset rather than defaulting to the first option - that default is fine
// when a human is about to review the whole modal synchronously (apply_easy.py's case), but
// risks silently picking a wrong option nobody notices in a fire-and-forget extension click.
function fillRadioGroup(radios, value) {
  const target = value.trim().toLowerCase();
  for (const { input, labelText } of radios) {
    if (!labelText) continue;
    const label = labelText.trim().toLowerCase();
    if (label.includes(target) || target.includes(label)) {
      input.click(); // click the label's control, not .checked=, so the site's own listeners fire
      markFilled(input);
      return true;
    }
  }
  return false;
}

// ---- LinkedIn (form_group markup, ported from apply_easy.py's SELECTORS) ----
const LINKEDIN_FORM_GROUP_SELECTOR =
  "div.fb-dash-form-element, div.jobs-easy-apply-form-section__grouping";

function collectLinkedInFields() {
  const fields = [];
  document.querySelectorAll(LINKEDIN_FORM_GROUP_SELECTOR).forEach((group) => {
    const labelEl = group.querySelector("label");
    const questionText = labelEl ? labelEl.innerText.trim() : "";
    if (questionText) fields.push({ group, questionText });
  });
  return fields;
}

function linkedInGetters(group) {
  return {
    getTextLike: () => group.querySelector("input[type='text'], input[type='number'], textarea"),
    getSelect: () => group.querySelector("select"),
    getRadios: () =>
      Array.from(group.querySelectorAll("input[type='radio']")).map((input) => {
        const forLabel = input.id
          ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`)
          : null;
        const labelText = forLabel
          ? forLabel.innerText.trim()
          : input.closest("label")
          ? input.closest("label").innerText.trim()
          : "";
        return { input, labelText };
      }),
  };
}

// ---- Generic (every other site) ----
const GENERIC_FIELD_SELECTOR =
  "input[type='text'], input[type='email'], input[type='tel'], input[type='number'], input:not([type]), textarea, select";

function collectGenericFields() {
  const fields = [];
  document.querySelectorAll(GENERIC_FIELD_SELECTOR).forEach((el) => {
    const questionText = resolveLabelText(el);
    if (questionText) fields.push({ el, questionText });
  });
  return fields;
}

function genericGetters(el) {
  return {
    getTextLike: () => (el.tagName === "SELECT" ? null : el),
    getSelect: () => (el.tagName === "SELECT" ? el : null),
    getRadios: () => [],
  };
}

// Radio groups need consolidating by `name` first (each <input> is one OPTION, not the
// question) - the question text comes from a <fieldset>/<legend> ancestor if present.
// Deliberately conservative: sites without a fieldset/legend just get reported as an
// unidentified radio group rather than guessing at a question from surrounding text, since a
// wrong question match here risks clicking the wrong radio silently.
function collectGenericRadioGroups() {
  const byName = new Map();
  document.querySelectorAll("input[type='radio']").forEach((input) => {
    const key = input.name || input.id;
    if (!key) return;
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key).push(input);
  });

  const groups = [];
  byName.forEach((inputs) => {
    const fieldset = inputs[0].closest("fieldset");
    const legend = fieldset ? fieldset.querySelector("legend") : null;
    const questionText = legend ? legend.innerText.trim() : "";
    const radios = inputs.map((input) => ({
      input,
      labelText:
        resolveLabelText(input) ||
        (input.closest("label") ? input.closest("label").innerText.trim() : ""),
    }));
    groups.push({ questionText, radios });
  });
  return groups;
}

function processQuestionField(questionText, profile, result, getters) {
  if (!questionText || isConsentQuestion(questionText) || isNoiseQuestion(questionText)) return;

  const answer = matchAnswer(questionText, profile);
  if (!answer) {
    result.unmatchedQuestions.push(questionText);
    return;
  }

  const textLike = getters.getTextLike();
  if (textLike) {
    fillTextLike(textLike, answer);
    result.filledCount++;
    result.filledLabels.push(questionText);
    return;
  }

  const select = getters.getSelect();
  if (select) {
    if (fillSelect(select, answer)) {
      result.filledCount++;
      result.filledLabels.push(questionText);
    } else {
      result.unmatchedQuestions.push(questionText);
    }
    return;
  }

  const radios = getters.getRadios();
  if (radios.length) {
    if (fillRadioGroup(radios, answer)) {
      result.filledCount++;
      result.filledLabels.push(questionText);
    } else {
      result.unmatchedQuestions.push(questionText);
    }
    return;
  }

  result.unmatchedQuestions.push(questionText);
}

// files: [{filename, mimeType, bytes: ArrayBuffer}, ...], sent by panel.js (which fetched
// them from the local backend - a content script fetching cross-origin PDF bytes itself would
// be subject to the JOB SITE's own CSP, not just this extension's host_permissions, which
// isn't worth the risk when panel.js can just pass the bytes over sendMessage instead).
function fillFileInputs(files) {
  if (!files || !files.length) return "no files provided";

  const fileInputs = Array.from(document.querySelectorAll("input[type='file']"));
  if (!fileInputs.length) return "skipped: no file input found - attach manually";

  let uploaded = 0;
  files.forEach((fileData, i) => {
    const input = fileInputs[i];
    if (!input) return;
    try {
      const file = new File([new Uint8Array(fileData.bytes)], fileData.filename, {
        type: fileData.mimeType || "application/pdf",
      });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      dispatchInputEvents(input);
      markFilled(input);
      uploaded++;
    } catch (err) {
      console.warn("jobApplier autofill: file upload failed", err);
    }
  });

  return uploaded > 0 ? `uploaded ${uploaded} file${uploaded > 1 ? "s" : ""}` : "skipped: no file input found - attach manually";
}

function runAutofill(profile, files) {
  const result = { filledCount: 0, filledLabels: [], unmatchedQuestions: [], fileUploadStatus: "" };

  if (location.hostname.endsWith("linkedin.com")) {
    collectLinkedInFields().forEach(({ group, questionText }) => {
      processQuestionField(questionText, profile, result, linkedInGetters(group));
    });
  } else {
    collectGenericFields().forEach(({ el, questionText }) => {
      processQuestionField(questionText, profile, result, genericGetters(el));
    });
    collectGenericRadioGroups().forEach(({ questionText, radios }) => {
      if (!questionText) {
        result.unmatchedQuestions.push("(radio group - could not determine question)");
        return;
      }
      processQuestionField(questionText, profile, result, {
        getTextLike: () => null,
        getSelect: () => null,
        getRadios: () => radios,
      });
    });
  }

  result.fileUploadStatus = fillFileInputs(files);
  return result;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "JOBAPPLIER_AUTOFILL") return;
  try {
    sendResponse(runAutofill(message.profile || {}, message.files || []));
  } catch (err) {
    sendResponse({
      filledCount: 0,
      filledLabels: [],
      unmatchedQuestions: [],
      fileUploadStatus: "error",
      error: String(err),
    });
  }
});

} // window.__jobapplierAutofillLoaded guard
