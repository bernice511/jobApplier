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

// LinkedIn's job search-results page keeps one stable path across every job you click on
// (/jobs/search-results/) and only changes a "currentJobId" query param - alongside other,
// more volatile tracking params in the same query string (analytics tokens etc. that can
// change even when you haven't clicked a different job). So the job's true identity is this
// id, not the URL as a whole. Used below for two things: (1) disambiguating the title link
// from same-page sidebar list items that share the exact same href pattern, and (2) giving
// maybePublish()/panel.js's change-detection something stable to key on.
function currentLinkedInJobId() {
  const fromQuery = new URLSearchParams(location.search).get("currentJobId");
  if (fromQuery) return fromQuery;
  const pathMatch = location.pathname.match(/\/jobs\/view\/(\d+)/);
  return pathMatch ? pathMatch[1] : null;
}

// On a search-results page, the sidebar job list contains one "/jobs/view/<id>" link per
// card, in addition to the detail pane's own link to whichever job is currently open - a bare
// "first match on the page" query easily locks onto the first list item and never updates as
// you click through different jobs, since the list itself doesn't reorder. Preferring the
// anchor whose href carries the job id already in the URL disambiguates correctly regardless
// of DOM order; unconfirmed against a live page, falls back to the old first-match behavior
// if nothing carries the id (e.g. an older markup variant).
function findLinkedInTitleText(jobId) {
  const anchors = Array.from(document.querySelectorAll(LINKEDIN_HREF_PATTERNS.title));
  if (jobId) {
    const scoped = anchors.find((a) => a.href.includes(`/jobs/view/${jobId}`) && a.innerText.trim());
    if (scoped) return scoped.innerText.trim();
  }
  const first = anchors.find((a) => a.innerText.trim());
  return first ? first.innerText.trim() : "";
}

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
  const jobId = currentLinkedInJobId();
  const rawDescription =
    firstNonEmptyText(LINKEDIN_DESCRIPTION_SELECTORS.join(", ")) || findLargestTextBlock();
  return {
    id: jobId,
    title: findLinkedInTitleText(jobId),
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
  // Chrome injects content scripts based on the URL at the time of a real page load, and
  // never re-checks that match when a single-page app changes its own URL client-side. If
  // this script was injected while the URL matched manifest.json's pattern (a LinkedIn
  // /jobs/... page) and the page then navigates internally to something else (a profile, the
  // home feed, search results) WITHOUT a real reload, this script keeps running against a URL
  // it was never meant for - Chrome won't catch that, so urlMatches() re-checks it explicitly.
  // LinkedIn is the one site here whose manifest match is scoped to a specific path segment
  // (/jobs/*, not the whole domain) - the other sites' matches are domain-wide, so there's no
  // narrower in-domain navigation for them to drift into.
  { test: (host) => host.endsWith("linkedin.com"), urlMatches: () => location.pathname.startsWith("/jobs/"), extract: extractLinkedIn },
  { test: (host) => host.endsWith("indeed.com"), urlMatches: () => true, extract: extractIndeed },
  { test: (host) => host.includes("greenhouse.io"), urlMatches: () => true, extract: extractGreenhouse },
  { test: (host) => host.includes("lever.co"), urlMatches: () => true, extract: extractLever },
  { test: (host) => host.includes("myworkdayjobs.com"), urlMatches: () => true, extract: extractWorkday },
];

function extractJob() {
  const site = SITE_EXTRACTORS.find((s) => s.test(location.hostname));

  if (site) {
    if (!site.urlMatches()) return null;

    // A site we have a dedicated extractor for is one where we know what a real job page's
    // title signal looks like (e.g. LinkedIn's /jobs/view/ href). If that signal is missing,
    // treat this as "not currently a job page" rather than falling back to the generic
    // largest-text-block heuristic - falling back to generic scraping here was publishing
    // profile bios (and other unrelated page text) as if they were job descriptions, which
    // then triggered a real (wasted) analyze call on garbage input - see panel.js's
    // auto-analyze-on-detection. Note this alone isn't sufficient on LinkedIn specifically:
    // a "Jobs based on your preferences" widget on a profile page contains real /jobs/view/
    // links too, so the title signal can be a false positive from that widget rather than an
    // absence - the urlMatches() check above is what actually catches that case.
    const primary = site.extract();
    if (!primary.title) return null;
    return { ...primary, url: location.href, extractedAt: Date.now() };
  }

  return { ...extractGeneric(), url: location.href, extractedAt: Date.now() };
}

let lastKey = "";

// Real job descriptions are always far longer than this. Sites like LinkedIn render a loading
// skeleton/placeholder in the description container for a moment before the real text streams
// in - that placeholder is non-empty, so it used to pass the bare "!job.description" check
// below and get published immediately. panel.js's auto-analyze would then run against that
// placeholder (producing a 0 score with no detected title/company), and once the real
// description arrived seconds later, nothing re-triggered analysis for it since panel.js's
// isNewJob check only looks at title/company, not description length - the score stayed wrong
// until something reset the panel's state. Waiting for a real-length description avoids the
// race instead of trying to recover from it after the fact.
const MIN_DESCRIPTION_LENGTH = 150;

// Which job this is, for both the dedup key below and panel.js's "is this a new job" check.
// Prefers job.id (LinkedIn's currentLinkedInJobId(), immune to the sidebar-list title/company
// mismatch AND to volatile tracking params elsewhere in the URL) and falls back to the full
// url for every other site, where the path alone is already a distinct-per-job signal.
function jobIdentity(job) {
  return job.id || job.url;
}

function maybePublish() {
  const job = extractJob();
  if (!job || !job.description || job.description.length < MIN_DESCRIPTION_LENGTH) return; // nothing usable yet (page still loading / no job open)

  const key = `${jobIdentity(job)}|${job.description.length}`;
  if (key === lastKey) return;
  lastKey = key;

  try {
    // Content scripts now run in every frame (see manifest.json's all_frames - needed so
    // sites that embed their real application form in a same-origin iframe, e.g. ADP, are
    // actually reachable at all). That means multiple frames of the SAME page can each try to
    // publish independently - if another frame of this exact page already found a longer,
    // more-likely-real description, don't let a shorter one (an ad iframe, a cookie-consent
    // widget's own frame, etc.) clobber it. A genuinely different job always overwrites - this
    // guard is only about frames racing on the SAME job.
    chrome.storage.local.get("jobapplier_current_job", (data) => {
      const existing = data.jobapplier_current_job;
      if (
        existing &&
        jobIdentity(existing) === jobIdentity(job) &&
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
