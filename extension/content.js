// Scrapes the job title/company/location/description off the job page you're viewing and
// stores the latest snapshot for panel.js to read. Supports several sites via a small
// per-site extractor registry (see SITE_EXTRACTORS below) rather than one hardcoded set of
// selectors - each site only needs to define what its markup actually requires; any field it
// leaves empty falls back to extractGeneric()'s heuristic instead of just being blank.
//
// Company/title/location mis-detection here isn't fatal even so: the backend's analyze_jd()
// re-derives all three from the pasted JD text anyway (see tailoring_service.py), so this is a
// UI convenience (pre-filling the panel), not the source of truth. The one field that matters
// for real is "description" - that's what actually gets analyzed.

function firstNonEmptyText(selector) {
  for (const el of document.querySelectorAll(selector)) {
    const text = el.innerText && el.innerText.trim();
    if (text) return text;
  }
  return "";
}

function findLargestTextBlock(minLength = 300) {
  // Last-resort fallback for the description: the tightest container that still holds a big
  // chunk of text (i.e. stop descending once the text starts fragmenting across children -
  // that's the real content wrapper, not a pass-through div). Works reasonably well on any
  // site, which is why it's also the generic fallback below, not just a LinkedIn backstop.
  let best = null;
  let bestLen = minLength;
  document.querySelectorAll("div, section, article").forEach((el) => {
    if (el.closest("#jobapplier-ignore, [id*='jobright']")) return;
    const text = el.innerText || "";
    if (text.length <= bestLen) return;
    const maxChildLen = Math.max(0, ...[...el.children].map((c) => (c.innerText || "").length));
    if (maxChildLen > text.length * 0.9) return; // a child already captures this text - not tight enough
    best = el;
    bestLen = text.length;
  });
  return best ? best.innerText.trim() : "";
}

// ---- LinkedIn (*://*.linkedin.com/jobs/*) ----
//
// LinkedIn's CSS classes are build-generated hashes (e.g. "_1e5f23a7", "ab436114") with no
// semantic meaning and no stability across builds - matching on class names doesn't work
// here. Title/company are instead matched by href pattern (the URL structure is functional,
// not a style artifact, so it's stable): the title is inside a link to "/jobs/view/<id>",
// the company inside a link to "linkedin.com/company/<slug>". Note LinkedIn's markup nests
// an inner <a> inside an outer one for these (invalid HTML), which browsers auto-split into
// extra *empty* anchors sharing the same href - so each lookup below scans all matches for
// the first one with actual text, not just the first match.
const LINKEDIN_HREF_PATTERNS = {
  title: "a[href*='/jobs/view/']",
  company: "a[href*='linkedin.com/company/']",
};

// The description container's id/classes aren't confirmed against a live page yet - if this
// stops matching, findLargestTextBlock() below is the fallback.
const LINKEDIN_DESCRIPTION_SELECTORS = ["#job-details", "article"];

// LinkedIn bundles the actual job description together with a lot of surrounding panels
// (its own "AI apply assistant" prompt, "people you can reach out to", hiring-insights
// charts, company boilerplate, DEI/sustainability blurbs, "I'm interested" solicitation) in
// one shared container with no stable DOM boundary between them - so whatever raw text gets
// extracted, trim it down to just the real JD using the heading text as a marker. Other
// sites' description containers don't have this problem (their JD text isn't bundled with
// unrelated page furniture the same way), so this cleanup is LinkedIn-specific.
const LINKEDIN_DESCRIPTION_START_MARKERS = ["About the job", "About the Job"];
const LINKEDIN_DESCRIPTION_END_MARKERS = [
  "See how you compare",
  "Candidates who clicked apply",
  "Exclusive Job Seeker Insights",
  "About the company",
  "Show Premium Insights",
  "Interested in working with us",
  "People you can reach out to",
];

function cleanLinkedInDescription(text) {
  let start = 0;
  for (const marker of LINKEDIN_DESCRIPTION_START_MARKERS) {
    const idx = text.indexOf(marker);
    if (idx !== -1) {
      start = idx + marker.length;
      break;
    }
  }

  let end = text.length;
  for (const marker of LINKEDIN_DESCRIPTION_END_MARKERS) {
    const idx = text.indexOf(marker, start);
    if (idx !== -1 && idx < end) end = idx;
  }

  return text.slice(start, end).trim();
}

function extractLinkedInLocation() {
  // Heuristic: LinkedIn shows "City, ST · X hours/days ago · N people clicked apply" in one
  // <p> near the title. No stable class to key off, so: find a <p> containing "ago" and take
  // the text before the first middot.
  for (const p of document.querySelectorAll("p")) {
    const text = p.innerText || "";
    if (text.includes("ago")) {
      return text.split("·")[0].trim();
    }
  }
  return "";
}

function extractLinkedIn() {
  const rawDescription =
    firstNonEmptyText(LINKEDIN_DESCRIPTION_SELECTORS.join(", ")) || findLargestTextBlock();
  return {
    title: firstNonEmptyText(LINKEDIN_HREF_PATTERNS.title),
    company: firstNonEmptyText(LINKEDIN_HREF_PATTERNS.company),
    location: extractLinkedInLocation(),
    description: cleanLinkedInDescription(rawDescription),
  };
}

// ---- Indeed (*://*.indeed.com/*) ----
//
// Selectors below reflect Indeed's typical markup but aren't confirmed against a live page -
// if a field comes back empty, extractGeneric() fills it in instead (see extractJob()), so an
// outdated selector degrades gracefully rather than breaking the whole extraction.
function extractIndeed() {
  return {
    title: firstNonEmptyText(
      "h1.jobsearch-JobInfoHeader-title, [data-testid='jobsearch-JobInfoHeader-title']"
    ),
    company: firstNonEmptyText(
      "[data-testid='inline-company-name'], .jobsearch-CompanyInfoContainer a, .jobsearch-CompanyInfoContainer"
    ),
    location: firstNonEmptyText("[data-testid='job-location'], [data-testid='inline-location']"),
    description: firstNonEmptyText("#jobDescriptionText"),
  };
}

// ---- Greenhouse (*://*.greenhouse.io/*) ----
//
// Greenhouse has two generations of job board embed in the wild (older boards.greenhouse.io
// vs newer job-boards.greenhouse.io) with different markup - selectors below try both,
// unconfirmed against a live page of either.
function extractGreenhouse() {
  return {
    title: firstNonEmptyText("h1.app-title, .job__title h1, h1"),
    company: firstNonEmptyText(".company-name, .job__company, [class*='CompanyName']"),
    location: firstNonEmptyText(".location, .job__location"),
    description: firstNonEmptyText("#content, .job__description, #main"),
  };
}

// ---- Lever (*://*.lever.co/*) ----
//
// jobs.lever.co/<company-slug>/<job-id> - the URL slug is a more reliable company signal than
// anything in the page markup itself (which often only shows the employer's logo, no text).
function extractLever() {
  const slugMatch = location.pathname.match(/^\/([^/]+)/);
  const companyFromUrl = slugMatch ? slugMatch[1].replace(/-/g, " ") : "";
  return {
    title: firstNonEmptyText(".posting-headline h2, h2"),
    company: firstNonEmptyText("[class*='company-name']") || companyFromUrl,
    location: firstNonEmptyText(".posting-categories .location, .location"),
    description: firstNonEmptyText(".posting-page, .content, #content"),
  };
}

// ---- Workday (*://*.myworkdayjobs.com/*) ----
//
// Workday job postings almost never show the employer name as plain page text (branding is
// logo-only) - the hostname/path is the reliable signal instead
// (<company>.wd1.myworkdayjobs.com, or myworkdayjobs.com/wday/cxs/<company>/...).
function extractWorkday() {
  const subdomainMatch = location.hostname.match(/^([^.]+)\.(?:[^.]+\.)?myworkdayjobs\.com$/);
  const pathMatch = location.pathname.match(/^\/([^/]+)/);
  const companyFromHost = subdomainMatch ? subdomainMatch[1] : pathMatch ? pathMatch[1] : "";
  return {
    title: firstNonEmptyText("[data-automation-id='jobPostingHeader']"),
    company: companyFromHost,
    location: firstNonEmptyText("[data-automation-id='locations'], [data-automation-id='subtitle']"),
    description: firstNonEmptyText("[data-automation-id='jobPostingDescription']"),
  };
}

// ---- Generic fallback (anything not explicitly covered above) ----
function extractGeneric() {
  const ogSiteName = document.querySelector("meta[property='og:site_name']");
  return {
    title: firstNonEmptyText("h1") || document.title.split(/[-|]/)[0].trim(),
    company: ogSiteName ? ogSiteName.content : "",
    location: "",
    description: findLargestTextBlock(),
  };
}

const SITE_EXTRACTORS = [
  { test: (host) => host.endsWith("linkedin.com"), extract: extractLinkedIn },
  { test: (host) => host.endsWith("indeed.com"), extract: extractIndeed },
  { test: (host) => host.includes("greenhouse.io"), extract: extractGreenhouse },
  { test: (host) => host.includes("lever.co"), extract: extractLever },
  { test: (host) => host.includes("myworkdayjobs.com"), extract: extractWorkday },
];

function extractJob() {
  const site = SITE_EXTRACTORS.find((s) => s.test(location.hostname));
  const primary = site ? site.extract() : extractGeneric();
  const fallback = site ? extractGeneric() : primary;

  return {
    title: primary.title || fallback.title,
    company: primary.company || fallback.company,
    location: primary.location || fallback.location,
    description: primary.description || fallback.description,
    url: location.href,
    extractedAt: Date.now(),
  };
}

let lastKey = "";

function maybePublish() {
  const job = extractJob();
  if (!job.description) return; // nothing usable yet (page still loading / no job open)

  const key = `${job.title}|${job.company}|${job.description.length}`;
  if (key === lastKey) return;
  lastKey = key;

  try {
    // Content scripts now run in every frame (see manifest.json's all_frames - needed so
    // sites that embed their real application form in a same-origin iframe, e.g. ADP, are
    // actually reachable at all). That means multiple frames of the SAME page can each try to
    // publish independently - if another frame of this exact page already found a longer,
    // more-likely-real description, don't let a shorter one (an ad iframe, a cookie-consent
    // widget's own frame, etc.) clobber it. A genuinely different page/job (different url)
    // always overwrites - this guard is only about frames racing on the SAME page.
    chrome.storage.local.get("jobapplier_current_job", (data) => {
      const existing = data.jobapplier_current_job;
      if (
        existing &&
        existing.url === job.url &&
        job.description.length < existing.description.length
      ) {
        return;
      }
      chrome.storage.local.set({ jobapplier_current_job: job });
    });
  } catch (err) {
    // "Extension context invalidated" - this tab's content script is from a version of the
    // extension that was reloaded/updated since injection (e.g. via chrome://extensions'
    // reload button without also refreshing this tab). It's a stale, orphaned instance with
    // no path back to a live extension context, so stop trying and stop the console noise -
    // refreshing the page is what actually fixes it.
    observer.disconnect();
  }
}

// Every supported site here is (or can be) a single-page app - switching jobs can swap
// content without a navigation event, so poll via MutationObserver rather than relying on
// page load alone.
const observer = new MutationObserver(() => maybePublish());
observer.observe(document.body, { childList: true, subtree: true });

maybePublish();
setTimeout(maybePublish, 1500); // catch late-rendering SPA content on first load
