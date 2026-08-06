# jobApplier

This repo was vibe coded with Claude Code.

A semi-automated LinkedIn job-search assistant, with two ways to use it:

1. **Full automation** (`linkedin_apply/`): searches LinkedIn for jobs matching your resume
   posted in the last 24 hours, tailors your resume and drafts a cover letter for each one
   with Claude, applies (Easy Apply flows are automated up to the final click, which you
   confirm yourself; external-site jobs get your documents prepared and the page opened for
   you to finish by hand), and logs every application to a CSV file.
2. **Paste-a-JD tailoring** (`webapp/` + the browser extension): found a job manually, or
   want one-click tailoring straight from a LinkedIn posting? Paste (or auto-detect) a job
   description, get a fit score and keyword suggestions, approve the ones that are honestly
   true, and generate a tailored resume/cover letter for it - no search automation involved.

## Why semi-automatic?

LinkedIn's Terms of Service don't allow automated bots to apply on your behalf. This tool
is built to minimize that risk rather than ignore it:
- A **real, headed Chrome browser** (not headless) drives everything, using a dedicated
  persistent profile you log into once.
- It drives LinkedIn's **own search UI/filters** - no scraping of undocumented APIs.
- **You must type `yes` to confirm** before any Easy Apply submission is actually clicked -
  nothing is submitted unattended.
- External-site applications (Workday, Greenhouse, etc.) are **never auto-filled** - only
  opened, with your tailored resume/cover letter ready alongside.
- Randomized delays and a hard per-run application cap (`MAX_APPLICATIONS_PER_RUN`) keep the
  activity pattern looking like a person browsing, not a bot blasting through jobs.

There's still residual risk (LinkedIn could flag unusual activity regardless), so use your
own judgment about how aggressively to run this.

## One-time setup

1. **Python deps**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium chrome
   ```

2. **WeasyPrint's native libraries** (macOS, via Homebrew) - needed for PDF rendering:
   ```bash
   brew install pango gdk-pixbuf
   ```
   Homebrew's libraries aren't always found automatically by macOS; if you hit an error like
   `cannot load library 'libgobject-2.0-0'`, run Python with:
   ```bash
   DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -m jobapplier.linkedin_apply.main
   ```

3. **Claude access**: this tool calls Claude via your existing Claude Code subscription
   (the `claude` CLI), not a separate paid API key. One-time login, in a normal terminal:
   ```bash
   claude /login
   ```
   Verify it worked:
   ```bash
   echo 'Reply with only: OK' | claude -p
   ```
   It should print `OK`. If it says "Not logged in", the login didn't stick in this shell -
   retry `claude /login` from the exact terminal you'll use to run this tool.

4. **Environment variables**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env`:
   - `JOB_TITLES` - comma-separated keywords to search for
   - `LOCATIONS` - semicolon-separated locations (use `Remote` as one if relevant)
   - `SENIORITY_LEVEL` - comma-separated, from: `Internship, Entry level, Associate, Mid-Senior level, Director, Executive`
   - `MAX_APPLICATIONS_PER_RUN`, `MIN_DELAY_SECONDS`, `MAX_DELAY_SECONDS` - safety/rate-limit knobs
   - `BROWSER_PROFILE_DIR` - leave blank to default to `data/browser_profile`

5. **Your resume**: place your resume PDF at `data/resume/master_resume.pdf`. The first run
   parses it into `data/resume/master_resume.json` (via Claude) and caches it - delete that
   JSON file and re-run if you update your resume PDF later and want to re-parse it.

6. **Screening question answers**:
   ```bash
   cp data/answers/screening_answers.example.yaml data/answers/screening_answers.yaml
   ```
   Fill in your real phone/work-authorization/salary-expectation/etc. answers. Any Easy Apply
   question the tool doesn't recognize will pause and ask you in the terminal once, then
   remember your answer for next time.

## Running it

```bash
source venv/bin/activate
DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src python3 -m jobapplier.linkedin_apply.main
```

A Chrome window will open. The first time, log into LinkedIn by hand in that window - the
tool waits (up to 5 minutes) for your feed to load, then continues automatically. On later
runs, the session persists and no re-login should be needed.

For each matching job, the tool will:
1. Tailor your resume + draft a cover letter for that specific job (saved to `data/generated/`).
2. If it's **Easy Apply**: fill the form and screening questions, then pause and ask you to
   type `yes` before actually submitting.
3. If it's an **external site**: open the application page in a new tab for you to finish
   manually, with your tailored documents ready.
4. Record the result in `data/applications.csv`.

## `data/applications.csv` columns

`job_id, title, company, location, date_found, date_applied, status, resume_path, cover_letter_path, job_url, notes`

`status` will be one of: `applied`, `cancelled_by_user` (you typed something other than
`yes` at the confirmation prompt), `manual_pending` (external site, opened for you to
finish), `manual_pending_no_url`, or `failed` (the flow hit something it couldn't handle -
check the terminal output for that job).

`job_id` is the dedupe key - anything already in this file is skipped in future searches, so
you'll never be shown or reapply to the same job twice.

## Portability across machines

Everything needed lives under this project folder: `data/resume/master_resume.pdf` (your
resume), `.env` (your config - gitignored), and `data/answers/screening_answers.yaml`
(gitignored). Copy the whole folder to another machine (`master_resume.pdf` is gitignored, so
copy it manually - it isn't in the repo), redo steps 1-3 above (dependencies and the
`claude /login` are machine-specific), and it'll pick up right where it left off via
`data/applications.csv`.

`master_resume.pdf` contains your personal contact info, so it's gitignored rather than
tracked - along with `.env`, `screening_answers.yaml`, `applications.csv`, `tailoring_log.csv`,
and everything generated at runtime.

## Web UI (paste-a-JD tailoring)

An alternative to the full search/apply automation above: paste a job description directly
and get a tailored resume/cover letter for it, without running any LinkedIn search.

```bash
source venv/bin/activate
DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src python3 -m jobapplier.webapp.app
```

Open `http://127.0.0.1:5050`. There are two pages:

- **Tailor**: paste a job description and click **Analyze fit**. This runs one fast Claude
  call that returns a 0-10 match score, a chip list of JD keywords your resume already
  demonstrates ("matched"), and a checklist of JD keywords that aren't literally in your
  resume but are honestly connectable to something you've actually done ("suggested" - each
  with a one-sentence reason). Check the ones you personally vouch for, optionally add
  free-text notes, choose whether to generate the resume, cover letter, or both, then click
  **Generate**. The match score is computed deterministically from these classified
  keywords/requirements (not asked as a one-shot LLM judgment), so it won't drift between
  runs on the same JD. Generating both documents runs as two concurrent Claude calls rather
  than one, so it's not much slower than generating just one. The result shows what
  specifically changed and a preview with the reworded text highlighted - the highlighting is
  preview-only, the downloaded PDF is always clean.
  - The tailoring guardrail is the same as the full automation flow: Claude can reword,
    reorder, and re-emphasize existing resume content, but can never invent an employer,
    date, skill, or metric that isn't already in your master resume. Approving a suggested
    keyword is what lets it be woven in - it's you vouching it's true, not the model deciding
    on its own.
- **Search past applications**: ask things like "which resume did I use for Netflix" in a
  simple chat box - this just does a local keyword search over the log below, no Claude call.

Every paste-a-JD generation is logged to `data/tailoring_log.csv` (timestamp, company, title,
location, resume/cover-letter paths, match score) - separate from `data/applications.csv`
above, since these aren't necessarily submitted LinkedIn applications.

## Browser extension (optional)

`extension/` is a Manifest V3 Chrome extension that reads the job you're currently viewing (in
your regular, everyday Chrome - not the dedicated automation profile above, so no separate
LinkedIn login needed), lets you tailor a resume/cover letter for it from a side panel without
copy-pasting the JD, and can **fill in the application form** on the page for you. It talks to
the local web UI's Flask server (`jobapplier.webapp.app`).

**Autofill boundary - read before using it:** the extension fills form fields but **never
clicks Submit/Apply/Next/Review** and never auto-advances a multi-step form - you always
review and submit by hand. It also **never auto-checks consent/certification/terms
checkboxes** (an explicit denylist in `extension/autofill.js` skips anything matching
"certify," "agree," "consent," "terms," etc. before it ever reaches the answer-matching
logic) and **never fabricates an answer** to a free-text question it can't confidently match -
unmatched questions are surfaced in the side panel for you to answer yourself, with an
optional "Save answer" so it's remembered next time (same self-growing `patterns` map
`linkedin_apply/apply_easy.py` already uses in `data/answers/screening_answers.yaml`, now
shared via `common/screening_answers.py`). Every field it does fill gets a visible outline so
a wrong match is easy to catch during review, not something to accidentally skim past. This
is a deliberate, but real, departure from earlier versions of this project (which framed the
extension as intentionally scrape-only, contrasting it with the semi-automated
`linkedin_apply/main.py` flow's own confirm-before-submit gate) - the *no-auto-submit*
half of that safety posture stays fully intact, only the *never-fills-anything* half changes.

Known gaps: file upload only works if the page has a real `<input type="file">` - some ATS
platforms use custom drag-and-drop widgets with no such element, and those get reported as
"attach manually" rather than faked. Indeed in particular sometimes redirects "Apply" to a
different domain the extension was never given access to; if that happens, open the actual
application page and retry rather than the job listing page. Field-matching selectors for
Indeed/Greenhouse/Lever/Workday's *application forms* (as opposed to their job-description
pages, which are checked against real markup) are a generic label-based fallback, not
confirmed against a live page of each - expect to iterate here the same way the JD-scraper
selectors below did.

Supported sites: LinkedIn, Indeed, Greenhouse, Lever, Workday, and ADP (`myjobs.adp.com`).
LinkedIn/Indeed/Greenhouse/Lever/Workday each have their own extractor in
`extension/content.js` (`SITE_EXTRACTORS`); ADP has none yet and runs entirely on the generic
fallback (`extractGeneric()` for scraping, the generic label-walker for autofill) - same
starting point the other four had before their selectors were confirmed against real
markup. Anything a site-specific extractor misses (or any other, unlisted site entirely) also
falls back to the same generic heuristic rather than coming back empty. Company/title/location
mis-detection isn't fatal even then - the backend's `analyze_jd()` re-derives all three from
the pasted JD text anyway, so extraction accuracy matters most for the description text, not
those fields.

If you hit a job site that isn't in this list at all (the content script never even loads
there, so autofill's "couldn't find the application form" error is really "this domain isn't
in `manifest.json` yet," not a form-detection failure) - add its host to both
`host_permissions` and `content_scripts.matches` in `extension/manifest.json`, the same way
ADP was added.

The content scripts run in every frame of the page (`all_frames: true`), not just the top
one - some ATS platforms (ADP included) embed the real application form in a same-origin
iframe rather than the top-level page, and without this the extension would only ever see the
outer page shell (a generic title, a cookie-consent widget) and never the actual form fields.
If autofill still finds nothing on a page that clearly has a form, check whether that form's
iframe is on a DIFFERENT domain than the page itself (view the page source, or right-click the
form and "Inspect") - if so, that domain needs adding to `manifest.json` too, the same as any
other unsupported site.

Setup:
1. Copy `data/answers/screening_answers.example.yaml` to `data/answers/screening_answers.yaml`
   and fill in your real answers, if you haven't already for the `linkedin_apply/` flow -
   autofill reads the same file.
2. Start the backend it depends on: `DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src python3 -m jobapplier.webapp.app`
3. In Chrome, go to `chrome://extensions`, enable **Developer mode**, click **Load unpacked**,
   and select the `extension/` folder.
4. Click the extension's toolbar icon to open its side panel, then open any job posting on a
   supported site - it detects the title/company/location/description automatically (with a
   collapsible box to review/edit the description if extraction misses something). After
   generating a resume/cover letter, an "Autofill this application" button appears.

Like `linkedin_apply/linkedin_search.py`/`linkedin_apply/apply_easy.py`, the DOM selectors
each site's extractor uses in `extension/content.js`/`extension/autofill.js` will need
updating if that site's markup changes - the Indeed/Greenhouse/Lever/Workday ones in
particular are based on each platform's typical markup, not confirmed against a live page of
each, so expect to iterate on them.

## Project structure

`src/jobapplier/` is split into three packages:
- `common/` - shared by both flows: config loading, the `claude` CLI wrapper, resume
  parsing (PDF -> JSON), and PDF rendering (JSON -> resume/cover-letter PDF).
- `linkedin_apply/` - the full search/apply automation (`main.py` is its entry point).
- `webapp/` - the paste-a-JD Flask app (`app.py` is its entry point) that the web UI and
  browser extension both talk to.

`extension/` (the Chrome extension) is separate from the Python package - it's plain
JS/HTML/manifest files that call `webapp/app.py` over HTTP.

## Known limitations

- External-site applications are intentionally never auto-filled (see "Why semi-automatic?" above).
- LinkedIn's page markup changes periodically; if searches start returning zero results or
  the Easy Apply flow stops finding fields, the CSS selectors in `linkedin_apply/linkedin_search.py`
  and `linkedin_apply/apply_easy.py` (grouped at the top of each file as `SELECTORS`) likely
  need updating to match LinkedIn's current DOM.
- Claude access goes through the `claude` CLI (`jobapplier/common/claude_cli.py`) using your Claude
  Code subscription login rather than a Console API key. This means: (1) it must be run from
  a terminal where `claude /login` has actually taken effect - a nested/sandboxed shell may
  not share that login even on the same machine; (2) it draws from the same usage as your
  interactive Claude Code sessions, so heavy job-search runs and heavy coding sessions could
  compete for the same quota; (3) tool use (Bash/Read/Write/etc.) is left disabled for these
  calls by omitting `--allowedTools`, but this wasn't independently confirmed against
  Anthropic's docs - watch the first few runs to make sure Claude only ever returns plain
  JSON and never attempts a file/shell action.
