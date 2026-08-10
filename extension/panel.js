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
// The exact /api/tailor request body that produced currentGenerateResult - needed to open the
// full-tab review page (see openReviewTab()) with enough context to regenerate from there.
// Persisted alongside generateResult in the job cache so this survives a panel reload too.
let currentGenerateRequestBody = null;

function scoreColor(score) {
  if (score >= 8) return "var(--good)";
  if (score >= 6) return "var(--warning)";
  if (score >= 4) return "var(--serious)";
  return "var(--critical)";
}

function fileLink(filename, label) {
  return `<a class="pill-link" href="${BACKEND_URL}/files/${encodeURIComponent(filename)}" target="_blank">${label}</a>`;
}

function matchLabel(score) {
  if (score >= 8) return "STRONG MATCH";
  if (score >= 6) return "GOOD MATCH";
  if (score >= 4) return "FAIR MATCH";
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

// Mirrors content.js's jobIdentity() - job.id (LinkedIn's stable job id) when present,
// otherwise the page url. NOT title/company: on a LinkedIn search-results page the sidebar
// list can make title/company extraction latch onto the wrong job (see content.js), so
// comparing those strings here would silently mask a real job switch - the panel would keep
// showing the previous job's analysis until a full page reload reset everything.
function jobIdentity(job) {
  return job && (job.id || job.url);
}

// Persists analysis + generated-resume results per job (chrome.storage.local, so it survives
// closing/reopening the panel and even a browser restart) - so revisiting a job you've already
// analyzed/generated for shows everything immediately, instead of waiting on a fresh
// /api/analyze round-trip or, worse, needing another click on Generate. The backend already
// caches both of those calls (analyze_cache.py, tailor_cache.py), so a miss here still avoids
// re-running Claude - this layer is what avoids the round-trip entirely and the resulting flash
// of empty state on every revisit.
const JOB_CACHE_STORAGE_KEY = "jobapplier_job_cache";
const MAX_CACHED_JOBS = 100;

function loadJobCacheEntry(identity) {
  return new Promise((resolve) => {
    chrome.storage.local.get(JOB_CACHE_STORAGE_KEY, (data) => {
      const cache = data[JOB_CACHE_STORAGE_KEY] || {};
      resolve(cache[identity] || null);
    });
  });
}

function saveJobCacheEntry(identity, partial) {
  if (!identity) return;
  chrome.storage.local.get(JOB_CACHE_STORAGE_KEY, (data) => {
    const cache = data[JOB_CACHE_STORAGE_KEY] || {};
    cache[identity] = { ...cache[identity], ...partial, savedAt: Date.now() };

    const entries = Object.entries(cache);
    if (entries.length > MAX_CACHED_JOBS) {
      entries.sort((a, b) => (a[1].savedAt || 0) - (b[1].savedAt || 0));
      chrome.storage.local.set({
        [JOB_CACHE_STORAGE_KEY]: Object.fromEntries(entries.slice(entries.length - MAX_CACHED_JOBS)),
      });
      return;
    }
    chrome.storage.local.set({ [JOB_CACHE_STORAGE_KEY]: cache });
  });
}

// Fire-and-forget: by the time the async storage lookup resolves, the user may already have
// clicked through to a DIFFERENT job (or back to this one, re-triggering isNewJob) - the
// jobIdentity(currentJob) check re-confirms this restore is still relevant before touching the
// UI, same guard shape as runAnalyze()'s requestId check.
async function restoreFromJobCache(job) {
  const identity = jobIdentity(job);
  const cached = await loadJobCacheEntry(identity);
  if (!cached || jobIdentity(currentJob) !== identity) return;

  // A cached result only means anything if it was actually computed from the description
  // we're looking at right now. Without this check, a job cached back when content.js's
  // scraper had a bug (e.g. capturing sidebar text instead of the real JD - see its own
  // history for examples) would restore that same wrong score/resume forever, even after
  // the scraper itself gets fixed and would now extract the job correctly - the stale
  // browser-local cache entry silently outlives the bug that produced it. Comparing against
  // the CURRENT extraction means a scraper fix (or the page just finishing loading late)
  // naturally self-heals the next time you open the job, no manual cache-clearing needed.
  const currentDescription = (job.description || "").trim();

  if (cached.analysis && cached.analyzedDescription === currentDescription) {
    currentAnalysis = cached.analysis;
    renderAnalysis(cached.analysis);
    // Nothing changed since this was cached - skip the redundant auto-analyze the debounce
    // would otherwise still fire in the background (see scheduleAutoAnalyze()).
    lastAutoAnalyzedDescription = currentDescription;
  }
  if (cached.generateResult && (cached.generateRequestBody?.jd_text || "").trim() === currentDescription) {
    currentGenerateResult = cached.generateResult;
    currentGenerateRequestBody = cached.generateRequestBody;
    renderGenerateResult(cached.generateResult);
  }
}

function showJob(job) {
  // content.js can republish the *same* job (e.g. a spurious re-extraction triggered by a
  // DOM mutation when DevTools opens/closes and resizes the page) - only treat it as a new
  // job, and clear any in-progress analysis/results, if it's actually a different job.
  // Otherwise this would wipe your analyze/generate results just from LinkedIn's page
  // reflowing, with nothing the user did actually changing.
  const isNewJob = !currentJob || jobIdentity(currentJob) !== jobIdentity(job);

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
    currentAnalysis = null;
    currentGenerateResult = null;
    currentGenerateRequestBody = null;
    lastAutoAnalyzedDescription = null;
    restoreFromJobCache(job);
  }

  // Analyze automatically instead of waiting for a click - analyze_jd() is cached server-side
  // (see analyze_cache.py), so re-opening a job already analyzed against the same resume
  // returns instantly rather than re-running the pipeline. Debounced rather than firing on
  // every publish: long job descriptions stream in over multiple DOM mutations, and this can
  // get called again for the SAME job (title/company unchanged) as the description grows -
  // firing immediately on the first, still-partial snapshot would analyze incomplete text and
  // never get a second chance once the full text settles.
  scheduleAutoAnalyze();
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

// Auto-analyze-on-detection means clicking through several jobs quickly can have multiple
// /api/analyze requests in flight at once - a SLOWER request for a job you've since clicked
// away from can resolve AFTER a faster request for the job you're now looking at, and
// without this guard its response would silently overwrite the correct one already on
// screen. Each call captures its own requestId at start; only the most recent one is allowed
// to actually render.
let analyzeRequestId = 0;

// Waits for the description to stop changing before analyzing it, rather than firing on every
// publish - see the comment at the scheduleAutoAnalyze() call site in showJob() for why.
let autoAnalyzeTimer = null;
let lastAutoAnalyzedDescription = null;

function scheduleAutoAnalyze() {
  const description = jdTextEl.value.trim();
  if (!description) return;
  clearTimeout(autoAnalyzeTimer);
  autoAnalyzeTimer = setTimeout(() => {
    // Bail if the text moved again since this timer was scheduled (a newer call already
    // superseded it - clearTimeout above should have caught that, this is just a backstop) or
    // if we've already analyzed this exact text (e.g. re-opening the same still-cached job).
    const current = jdTextEl.value.trim();
    if (current !== description || current === lastAutoAnalyzedDescription) return;
    lastAutoAnalyzedDescription = current;
    runAnalyze();
  }, 1000);
}

async function runAnalyze() {
  const jdText = jdTextEl.value.trim();
  if (!jdText) return;

  const requestId = ++analyzeRequestId;
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
    clearInterval(timer); // always stop OUR OWN timer, even if stale - otherwise a discarded
    // request's caption-update interval keeps firing forever, fighting the current one for
    // the same "Checking fit... Ns" text.
    if (requestId !== analyzeRequestId) return; // a newer request has already taken over
    analyzeStatus.innerHTML = "";

    if (!resp.ok) {
      analyzeResult.innerHTML = `<div class="error-box">${data.error || "Something went wrong."}</div>`;
      return;
    }
    currentAnalysis = data;
    renderAnalysis(data);
    saveJobCacheEntry(jobIdentity(currentJob), { analysis: data, analyzedDescription: jdText });
  } catch (e) {
    clearInterval(timer);
    if (requestId !== analyzeRequestId) return;
    analyzeStatus.innerHTML = "";
    analyzeResult.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    if (requestId === analyzeRequestId) analyzeBtn.disabled = false;
  }
}

// Kept as a manual re-run (e.g. after editing the JD text by hand) - the automatic call on
// job detection below covers the normal case.
analyzeBtn.addEventListener("click", runAnalyze);

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

// Opens the generated resume in a full browser tab instead of a dialog inside the side panel -
// a <dialog> here is capped by however wide the panel itself is (Chrome controls that, not
// this CSS), which was never going to be enough room to read AND comment on individual bullets
// comfortably. The webapp already runs on BACKEND_URL with the room a normal tab has, so the
// review UI (bullet comments + "regenerate with feedback") lives there instead - see
// review.html.jinja. A short-lived server-side session (POST /api/review-session) is the
// hand-off: jd_text can be tens of KB, too big for a URL, and a plain webapp tab has no access
// to chrome.storage to read it directly.
async function openReviewTab() {
  if (!currentGenerateRequestBody || !currentGenerateResult) return;
  const resp = await fetch(`${BACKEND_URL}/api/review-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...currentGenerateRequestBody,
      analysis: currentAnalysis,
      generate_result: currentGenerateResult,
    }),
  });
  if (!resp.ok) return;
  const { token } = await resp.json();
  chrome.tabs.create({ url: `${BACKEND_URL}/review/${token}` });
}

// Shared by a live /api/tailor response and by restoreFromJobCache() re-displaying a
// previously generated result for a job you're revisiting - both need the exact same card.
function renderGenerateResult(data) {
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
    html += `<button class="secondary preview-open-btn" style="width:100%;">Open full resume review &#8599;</button>`;
  }
  html += `</div>`;

  generateResult.innerHTML = html;

  if (data.resume_preview_html) {
    generateResult.querySelector(".preview-open-btn").addEventListener("click", openReviewTab);
  }
}

async function generateTailored(notes) {
  const generateBtn = document.getElementById("generate-btn");
  const generateStatus = document.getElementById("generate-status");
  const generate = document.querySelector('input[name="generate"]:checked').value;

  const approvedKeywords = [];
  document.querySelectorAll(".keyword-checkbox:checked").forEach((cb) => {
    const idx = parseInt(cb.dataset.index, 10);
    approvedKeywords.push(currentAnalysis.suggested_keywords[idx]);
  });

  const requestBody = {
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
  };

  generateResult.innerHTML = "";
  generateBtn.disabled = true;
  const timer = startLoading(generateStatus, "Generating via Claude...");

  try {
    const resp = await fetch(`${BACKEND_URL}/api/tailor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const data = await resp.json();
    clearInterval(timer);
    generateStatus.innerHTML = "";

    if (!resp.ok) {
      generateResult.innerHTML = `<div class="error-box">${data.error || "Something went wrong."}</div>`;
      return;
    }

    currentGenerateResult = data;
    currentGenerateRequestBody = requestBody;
    renderGenerateResult(data);
    saveJobCacheEntry(jobIdentity(currentJob), { generateResult: data, generateRequestBody: requestBody });
  } catch (e) {
    clearInterval(timer);
    if (generateStatus) generateStatus.innerHTML = "";
    generateResult.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    generateBtn.disabled = false;
  }
}

async function onGenerate() {
  const notes = document.getElementById("notes-text").value;
  await generateTailored(notes);
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
