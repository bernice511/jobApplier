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
    html += `</div>`;

    generateResult.innerHTML = html;
  } catch (e) {
    clearInterval(timer);
    generateStatus.innerHTML = "";
    generateResult.innerHTML = `<div class="error-box">${e}</div>`;
  } finally {
    generateBtn.disabled = false;
  }
}

checkBackend();
loadStoredJob();
