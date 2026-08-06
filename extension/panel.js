const BACKEND_URL = "http://127.0.0.1:5050";

const backendWarning = document.getElementById("backend-warning");
const detectedStatus = document.getElementById("detected-status");
const detectedJobDiv = document.getElementById("detected-job");
const jobTitleEl = document.getElementById("job-title");
const jobCompanyEl = document.getElementById("job-company");
const jdTextEl = document.getElementById("jd-text");
const analyzeBtn = document.getElementById("analyze-btn");
const analyzeStatus = document.getElementById("analyze-status");
const analyzeResult = document.getElementById("analyze-result");
const generateResult = document.getElementById("generate-result");
const autofillBtn = document.getElementById("autofill-btn");
const profileDialog = document.getElementById("profile-dialog");
const profileOpenBtn = document.getElementById("profile-open-btn");
const profileCloseBtn = document.getElementById("profile-close-btn");
const profileSaveBtn = document.getElementById("profile-save-btn");

const PROFILE_FIELDS = [
  "first_name", "last_name", "phone", "email", "work_authorization", "requires_sponsorship",
  "notice_period_days", "salary_expectation", "years_experience_default", "linkedin_url",
  "github_url", "website_url",
];

let currentJob = null;
let currentAnalysis = null;
let currentGenerateResult = null;

function scoreColor(score) {
  if (score >= 8) return "var(--good)";
  if (score >= 5) return "var(--warning)";
  return "var(--critical)";
}

function fileLink(filename, label) {
  return `<a class="pill-link" href="${BACKEND_URL}/files/${encodeURIComponent(filename)}" target="_blank">${label}</a>`;
}

function matchLabel(score) {
  if (score >= 8) return "STRONG MATCH";
  if (score >= 5) return "GOOD MATCH";
  return "WEAK MATCH";
}

function statTileHtml(score) {
  if (score === undefined || score === null || score === "") return "";
  const color = scoreColor(score);
  return `
    <div class="job-card-score">
      <div class="job-card-score-ring" style="border-color:${color};">
        <div class="job-card-score-value">${score}<span class="job-card-score-sub">/10</span></div>
      </div>
      <div class="job-card-score-label" style="color:${color};">${matchLabel(score)}</div>
    </div>
  `;
}

function startLoading(container, label) {
  let seconds = 0;
  const captionId = container.id + "-caption";
  container.innerHTML = `
    <div class="loading-box">
      <div class="spinner-lg"></div>
      <div id="${captionId}">${label}</div>
    </div>
  `;
  const caption = document.getElementById(captionId);
  return setInterval(() => {
    seconds += 1;
    if (caption) caption.textContent = `${label} ${seconds}s`;
  }, 1000);
}

async function checkBackend() {
  try {
    const resp = await fetch(`${BACKEND_URL}/`, { method: "GET" });
    backendWarning.style.display = resp.ok ? "none" : "block";
  } catch {
    backendWarning.style.display = "block";
  }
}

function showJob(job) {
  // content.js can republish the *same* job (e.g. a spurious re-extraction triggered by a
  // DOM mutation when DevTools opens/closes and resizes the page) - only treat it as a new
  // job, and clear any in-progress analysis/results, if the title or company actually
  // changed. Otherwise this would wipe your analyze/generate results just from LinkedIn's
  // page reflowing, with nothing the user did actually changing.
  const isNewJob = !currentJob || currentJob.title !== job.title || currentJob.company !== job.company;

  currentJob = job;
  detectedStatus.style.display = "none";
  detectedJobDiv.style.display = "block";
  jobTitleEl.textContent = job.title || "(title not detected)";
  jobCompanyEl.textContent = job.company || "(company not detected)";
  jdTextEl.value = job.description || "";

  if (isNewJob) {
    analyzeResult.innerHTML = "";
    generateResult.innerHTML = "";
    document.getElementById("autofill-result").innerHTML = "";
    // A freshly-tailored resume/cover letter belongs to the PREVIOUS job - don't attach it to
    // this new one. Autofill falls back to the active resume's raw PDF until Generate is run
    // again for this job.
    currentGenerateResult = null;
  }
}

function loadStoredJob() {
  chrome.storage.local.get("jobapplier_current_job", (data) => {
    if (data.jobapplier_current_job) showJob(data.jobapplier_current_job);
  });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.jobapplier_current_job) {
    showJob(changes.jobapplier_current_job.newValue);
  }
});

analyzeBtn.addEventListener("click", async () => {
  const jdText = jdTextEl.value.trim();
  if (!jdText) return;
  analyzeResult.innerHTML = "";
  generateResult.innerHTML = "";
  analyzeBtn.disabled = true;
  const timer = startLoading(analyzeStatus, "Checking fit...");

  try {
    const resp = await fetch(`${BACKEND_URL}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd_text: jdText }),
    });
    const data = await resp.json();
    clearInterval(timer);
    analyzeStatus.innerHTML = "";

    if (!resp.ok) {
      analyzeResult.innerHTML = `<div class="error-box">${data.error || "Something went wrong."}</div>`;
      return;
    }
    currentAnalysis = data;
    renderAnalysis(data);
  } catch (e) {
    clearInterval(timer);
    analyzeStatus.innerHTML = "";
    analyzeResult.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    analyzeBtn.disabled = false;
  }
});

function renderAnalysis(data) {
  let html = `<div class="card">`;
  html += `${statTileHtml(data.match_score)}`;

  if (data.matched_keywords && data.matched_keywords.length) {
    html += `<div class="changes-title">Already matches</div><div class="chip-row">`;
    data.matched_keywords.forEach((k) => { html += `<span class="chip">${k}</span>`; });
    html += `</div>`;
  }

  if (data.suggested_keywords && data.suggested_keywords.length) {
    html += `<div class="changes-title">Suggested keywords — check any that genuinely apply</div>`;
    data.suggested_keywords.forEach((kw, i) => {
      html += `
        <label class="keyword-option">
          <input type="checkbox" class="keyword-checkbox" data-index="${i}">
          <span>
            <span class="keyword-term">${kw.term}</span>
            <span class="keyword-basis">${kw.based_on}</span>
          </span>
        </label>
      `;
    });
  }

  html += `
    <div class="changes-title">Anything else to emphasize? (optional)</div>
    <textarea id="notes-text" style="min-height:60px;" placeholder="e.g. I also have hands-on experience with..."></textarea>
    <div class="radio-group" id="generate-options">
      <label><input type="radio" name="generate" value="both" checked> Resume + cover letter</label>
      <label><input type="radio" name="generate" value="resume"> Resume only</label>
      <label><input type="radio" name="generate" value="cover_letter"> Cover letter only</label>
    </div>
    <button class="primary" id="generate-btn">Generate</button>
    <div id="generate-status"></div>
  `;
  html += `</div>`;

  analyzeResult.innerHTML = html;
  document.getElementById("generate-btn").addEventListener("click", onGenerate);
}

async function onGenerate() {
  const generateBtn = document.getElementById("generate-btn");
  const generateStatus = document.getElementById("generate-status");
  const generate = document.querySelector('input[name="generate"]:checked').value;
  const notes = document.getElementById("notes-text").value;

  const approvedKeywords = [];
  document.querySelectorAll(".keyword-checkbox:checked").forEach((cb) => {
    const idx = parseInt(cb.dataset.index, 10);
    approvedKeywords.push(currentAnalysis.suggested_keywords[idx]);
  });

  generateResult.innerHTML = "";
  generateBtn.disabled = true;
  const timer = startLoading(generateStatus, "Generating via Claude...");

  try {
    const resp = await fetch(`${BACKEND_URL}/api/tailor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd_text: jdTextEl.value,
        company: currentAnalysis.company,
        title: currentAnalysis.title,
        location: currentAnalysis.location,
        generate,
        approved_keywords: approvedKeywords,
        notes,
        matched_keyword_count: (currentAnalysis.matched_keywords || []).length,
        suggested_keyword_count: (currentAnalysis.suggested_keywords || []).length,
        core_requirement_count: currentAnalysis.core_requirement_count || 0,
      }),
    });
    const data = await resp.json();
    clearInterval(timer);
    generateStatus.innerHTML = "";

    if (!resp.ok) {
      generateResult.innerHTML = `<div class="error-box">${data.error || "Something went wrong."}</div>`;
      return;
    }

    let downloads = "";
    if (data.resume_filename) downloads += fileLink(data.resume_filename, "Download resume");
    if (data.cover_letter_filename) downloads += fileLink(data.cover_letter_filename, "Download cover letter");

    let html = `<div class="card">`;
    html += `<div class="downloads">${downloads}</div>`;
    html += statTileHtml(data.match_score);

    if (data.changes && data.changes.length) {
      html += `<div class="changes-title">What changed</div><ul class="changes-list">`;
      data.changes.forEach((c) => { html += `<li>${c}</li>`; });
      html += `</ul>`;
    }

    if (data.resume_preview_html) {
      html += `
        <p class="preview-note">Highlighted = reworded from your master resume. Preview only - the downloaded PDF is clean.</p>
        <div class="preview-box">${data.resume_preview_html}</div>
      `;
    }
    html += `</div>`;

    currentGenerateResult = data;
    generateResult.innerHTML = html;
  } catch (e) {
    clearInterval(timer);
    generateStatus.innerHTML = "";
    generateResult.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    generateBtn.disabled = false;
  }
}

async function fetchFileAsArrayBuffer(filename) {
  const resp = await fetch(`${BACKEND_URL}/files/${encodeURIComponent(filename)}`);
  if (!resp.ok) throw new Error(`Could not fetch ${filename}`);
  return resp.arrayBuffer();
}

async function fetchActiveResumeAsArrayBuffer(resumeId) {
  const resp = await fetch(`${BACKEND_URL}/resume-files/${encodeURIComponent(resumeId)}`);
  if (!resp.ok) throw new Error("Could not fetch your active resume.");
  return resp.arrayBuffer();
}

function getActiveTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!tabs || !tabs[0]) {
        reject(new Error("No active tab found."));
        return;
      }
      resolve(tabs[0]);
    });
  });
}

function sendAutofillMessage(tabId, profile, files) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, { type: "JOBAPPLIER_AUTOFILL", profile, files }, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

function injectAutofillScript(tabId) {
  return chrome.scripting.executeScript({ target: { tabId }, files: ["autofill.js"] });
}

// autofill.js isn't statically declared for every possible domain (application forms can live
// on a company's own site, not just the known ATS platforms) - try messaging first (works
// immediately on the sites where it's already statically injected), and only pay the cost of
// an on-demand injection if nothing responds. autofill.js's own top-level loaded-guard makes a
// repeat injection on the same page (e.g. clicking Autofill twice) a safe no-op.
async function sendAutofillMessageWithInject(tabId, profile, files) {
  try {
    return await sendAutofillMessage(tabId, profile, files);
  } catch {
    await injectAutofillScript(tabId);
    return sendAutofillMessage(tabId, profile, files);
  }
}

async function onAutofill() {
  const autofillStatus = document.getElementById("autofill-status");
  const autofillResultEl = document.getElementById("autofill-result");
  autofillResultEl.innerHTML = "";
  autofillBtn.disabled = true;
  const timer = startLoading(autofillStatus, "Filling in the form...");

  try {
    const profileResp = await fetch(`${BACKEND_URL}/api/autofill-profile`);
    const profile = await profileResp.json();
    if (!profileResp.ok) {
      throw new Error(profile.error || "Could not load your profile data.");
    }

    // Prefer a freshly-tailored resume/cover letter generated for THIS job this session
    // (currentGenerateResult); otherwise fall back to the active resume's raw, untailored PDF
    // so autofill still works without running Analyze/Generate first. There's no "generic"
    // cover letter to fall back to - it's always JD-tailored, so it's simply omitted here.
    const files = [];
    if (currentGenerateResult && currentGenerateResult.resume_filename) {
      files.push({
        filename: currentGenerateResult.resume_filename,
        mimeType: "application/pdf",
        bytes: await fetchFileAsArrayBuffer(currentGenerateResult.resume_filename),
      });
    } else if (profile.resume_id) {
      files.push({
        filename: "resume.pdf",
        mimeType: "application/pdf",
        bytes: await fetchActiveResumeAsArrayBuffer(profile.resume_id),
      });
    }
    if (currentGenerateResult && currentGenerateResult.cover_letter_filename) {
      files.push({
        filename: currentGenerateResult.cover_letter_filename,
        mimeType: "application/pdf",
        bytes: await fetchFileAsArrayBuffer(currentGenerateResult.cover_letter_filename),
      });
    }

    const tab = await getActiveTab();
    let response;
    try {
      response = await sendAutofillMessageWithInject(tab.id, profile, files);
    } catch (err) {
      throw new Error(
        "Couldn't run autofill on this tab - it may be a page the browser doesn't allow extensions on (e.g. a chrome:// or the Chrome Web Store page). Open the actual application page and try again."
      );
    }

    clearInterval(timer);
    autofillStatus.innerHTML = "";
    renderAutofillResult(response || {});
  } catch (e) {
    clearInterval(timer);
    autofillStatus.innerHTML = "";
    autofillResultEl.innerHTML = `<div class="error-box">${e.message || e}</div>`;
  } finally {
    autofillBtn.disabled = false;
  }
}

function renderAutofillResult(result) {
  const autofillResultEl = document.getElementById("autofill-result");
  const filledCount = result.filledCount || 0;
  const filledLabels = result.filledLabels || [];
  const unmatched = result.unmatchedQuestions || [];

  let html = `<div class="changes-title">Filled ${filledCount} field${filledCount === 1 ? "" : "s"}</div>`;
  if (filledLabels.length) {
    html += `<ul class="changes-list">${filledLabels.map((l) => `<li>${l}</li>`).join("")}</ul>`;
  }

  if (result.fileUploadStatus) {
    html += `<div class="changes-title">Resume/cover letter upload</div><p class="preview-note">${result.fileUploadStatus}</p>`;
  }

  if (unmatched.length) {
    html += `<div class="changes-title">Couldn't match ${unmatched.length} question${unmatched.length === 1 ? "" : "s"} - answer on the page, then save it here so it's never asked again</div>`;
    unmatched.forEach((question, i) => {
      html += `
        <div class="unmatched-question">
          <div class="unmatched-question-text">${question}</div>
          <input type="text" class="unmatched-answer-input" data-index="${i}" placeholder="Your answer">
          <button class="secondary save-answer-btn" data-index="${i}">Save answer</button>
        </div>
      `;
    });
  }

  autofillResultEl.innerHTML = html;

  unmatched.forEach((question, i) => {
    const btn = autofillResultEl.querySelector(`.save-answer-btn[data-index="${i}"]`);
    const input = autofillResultEl.querySelector(`.unmatched-answer-input[data-index="${i}"]`);
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
      const answer = input.value.trim();
      if (!answer) return;
      btn.disabled = true;
      try {
        const resp = await fetch(`${BACKEND_URL}/api/screening-answers/patterns`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, answer }),
        });
        if (!resp.ok) throw new Error("Save failed");
        btn.textContent = "Saved - click Autofill again to apply it";
      } catch {
        btn.disabled = false;
        btn.textContent = "Save answer (failed, retry)";
      }
    });
  });
}

let profileLoaded = false;

async function loadProfileFields() {
  const statusEl = document.getElementById("profile-save-status");
  try {
    const resp = await fetch(`${BACKEND_URL}/api/profile`);
    const data = await resp.json();
    if (!resp.ok) {
      statusEl.innerHTML = `<div class="error-box">${data.error || "Could not load your profile."}</div>`;
      return;
    }
    PROFILE_FIELDS.forEach((key) => {
      const el = document.getElementById(`profile-${key}`);
      if (el) el.value = data[key] || "";
    });
    profileLoaded = true;
  } catch (e) {
    statusEl.innerHTML = `<div class="error-box">${e}</div>`;
  }
}

profileOpenBtn.addEventListener("click", () => {
  profileDialog.showModal();
  if (!profileLoaded) loadProfileFields();
});

profileCloseBtn.addEventListener("click", () => profileDialog.close());

// Click on the backdrop (the dialog element itself, not any of its children) closes it too -
// <dialog> only closes via .close()/Escape by default, not a backdrop click.
profileDialog.addEventListener("click", (e) => {
  if (e.target === profileDialog) profileDialog.close();
});

profileSaveBtn.addEventListener("click", async () => {
  const statusEl = document.getElementById("profile-save-status");
  const fields = {};
  PROFILE_FIELDS.forEach((key) => {
    const el = document.getElementById(`profile-${key}`);
    if (el) fields[key] = el.value.trim();
  });

  profileSaveBtn.disabled = true;
  const timer = startLoading(statusEl, "Saving...");
  try {
    const resp = await fetch(`${BACKEND_URL}/api/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    const data = await resp.json();
    clearInterval(timer);
    if (!resp.ok) {
      statusEl.innerHTML = `<div class="error-box">${data.error || "Save failed."}</div>`;
      return;
    }
    statusEl.innerHTML = "";
    profileDialog.close();
  } catch (e) {
    clearInterval(timer);
    statusEl.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    profileSaveBtn.disabled = false;
  }
});

autofillBtn.addEventListener("click", onAutofill);

checkBackend();
loadStoredJob();
