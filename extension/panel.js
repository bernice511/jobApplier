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

function statTileHtml(score) {
  if (score === undefined || score === null || score === "") return "";
  const color = scoreColor(score);
  const pct = Math.max(0, Math.min(10, score)) * 10;
  return `
    <div class="stat-row">
      <div>
        <div class="stat-label">Match score</div>
        <div class="stat-value">${score}<span class="stat-value-sub">/10</span></div>
      </div>
      <div class="meter-track"><div class="meter-fill" style="width:${pct}%; background:${color};"></div></div>
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
    html += `
      <button class="primary" id="autofill-btn">Autofill this application</button>
      <p class="preview-note">Fills in what it can match on the open tab - never clicks Submit/Apply, never checks agreement/consent boxes. Review before you submit.</p>
      <div id="autofill-status"></div>
      <div id="autofill-result"></div>
    `;
    html += `</div>`;

    currentGenerateResult = data;
    generateResult.innerHTML = html;
    document.getElementById("autofill-btn").addEventListener("click", onAutofill);
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

async function onAutofill() {
  const autofillBtn = document.getElementById("autofill-btn");
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

    const files = [];
    if (currentGenerateResult.resume_filename) {
      files.push({
        filename: currentGenerateResult.resume_filename,
        mimeType: "application/pdf",
        bytes: await fetchFileAsArrayBuffer(currentGenerateResult.resume_filename),
      });
    }
    if (currentGenerateResult.cover_letter_filename) {
      files.push({
        filename: currentGenerateResult.cover_letter_filename,
        mimeType: "application/pdf",
        bytes: await fetchFileAsArrayBuffer(currentGenerateResult.cover_letter_filename),
      });
    }

    const tab = await getActiveTab();
    let response;
    try {
      response = await sendAutofillMessage(tab.id, profile, files);
    } catch (err) {
      throw new Error(
        "Couldn't find the application form on this tab - open the actual application page (not just the job listing) and try again."
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

checkBackend();
loadStoredJob();
