// Scrapes the job title/company/location/description off a LinkedIn job page (either the
// standalone /jobs/view/<id> page or the detail pane on /jobs/search/...) and stores the
// latest snapshot for panel.js to read.
//
// LinkedIn's CSS classes are build-generated hashes (e.g. "_1e5f23a7", "ab436114") with no
// semantic meaning and no stability across builds - matching on class names doesn't work
// here. Title/company are instead matched by href pattern (the URL structure is functional,
// not a style artifact, so it's stable): the title is inside a link to "/jobs/view/<id>",
// the company inside a link to "linkedin.com/company/<slug>". Note LinkedIn's markup nests
// an inner <a> inside an outer one for these (invalid HTML), which browsers auto-split into
// extra *empty* anchors sharing the same href - so each lookup below scans all matches for
// the first one with actual text, not just the first match.
const HREF_PATTERNS = {
  title: "a[href*='/jobs/view/']",
  company: "a[href*='linkedin.com/company/']",
};

// The description container's id/classes aren't confirmed against a live page yet - if this
// stops matching, findLargestTextBlock() below is the fallback, and the fix is to add the
// real selector here once someone inspects a live job page and reports it back.
const DESCRIPTION_SELECTORS = ["#job-details", "article"];

function firstNonEmptyText(selector) {
  for (const el of document.querySelectorAll(selector)) {
    const text = el.innerText && el.innerText.trim();
    if (text) return text;
  }
  return "";
}

function extractLocation() {
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

// LinkedIn bundles the actual job description together with a lot of surrounding panels
// (its own "AI apply assistant" prompt, "people you can reach out to", hiring-insights
// charts, company boilerplate, DEI/sustainability blurbs, "I'm interested" solicitation) in
// one shared container with no stable DOM boundary between them - so whatever raw text gets
// extracted, trim it down to just the real JD using the heading text as a marker.
const DESCRIPTION_START_MARKERS = ["About the job", "About the Job"];
const DESCRIPTION_END_MARKERS = [
  "See how you compare",
  "Candidates who clicked apply",
  "Exclusive Job Seeker Insights",
  "About the company",
  "Show Premium Insights",
  "Interested in working with us",
  "People you can reach out to",
];

function cleanDescription(text) {
  let start = 0;
  for (const marker of DESCRIPTION_START_MARKERS) {
    const idx = text.indexOf(marker);
    if (idx !== -1) {
      start = idx + marker.length;
      break;
    }
  }

  let end = text.length;
  for (const marker of DESCRIPTION_END_MARKERS) {
    const idx = text.indexOf(marker, start);
    if (idx !== -1 && idx < end) end = idx;
  }

  return text.slice(start, end).trim();
}

function findLargestTextBlock(minLength = 300) {
  // Last-resort fallback for the description: the tightest container that still holds a big
  // chunk of text (i.e. stop descending once the text starts fragmenting across children -
  // that's the real content wrapper, not a pass-through div).
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

function extractJob() {
  const rawDescription = firstNonEmptyText(DESCRIPTION_SELECTORS.join(", ")) || findLargestTextBlock();
  return {
    title: firstNonEmptyText(HREF_PATTERNS.title),
    company: firstNonEmptyText(HREF_PATTERNS.company),
    location: extractLocation(),
    description: cleanDescription(rawDescription),
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

  chrome.storage.local.set({ jobapplier_current_job: job });
}

// LinkedIn is a single-page app - clicking a different job in the results list swaps the
// detail pane without a navigation event, so poll via MutationObserver rather than relying
// on page load alone.
const observer = new MutationObserver(() => maybePublish());
observer.observe(document.body, { childList: true, subtree: true });

maybePublish();
setTimeout(maybePublish, 1500); // catch late-rendering SPA content on first load
