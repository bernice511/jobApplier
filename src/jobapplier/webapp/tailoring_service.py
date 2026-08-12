"""Paste-a-JD flow, split into two phases so the UI isn't a single long blocking wait:

1. analyze_jd() - one fast Claude call: extracts company/title/location, scores fit against
   the CURRENT (untouched) master resume, and suggests JD keywords that aren't literally in
   the resume but are honestly connectable to something the candidate has actually done. The
   user reviews these and picks which ones they personally vouch for (plus optional free-text
   notes) before anything is written - this is what lets truthful keywords in without the
   model ever being allowed to invent one on its own.

2. tailor_from_jd() - generates the resume and/or cover letter. When both are requested, they
   run as two independent, concurrent `claude` CLI calls (see GenerateOption / ThreadPoolExecutor
   below) rather than one merged call, so wall-clock time is bounded by the slower of the two
   instead of the sum.

Company/title/location are extracted once during analyze_jd() and passed into
tailor_from_jd() by the caller (webapp.py) - the generation calls don't re-derive them, both
for speed and so a resume-call and cover-letter-call can't disagree on the job's identity.

Never touches tailor.py, which is the LinkedIn-flow module (main.py) where company/title/
location come from LinkedIn's own metadata rather than being extracted from raw text.
Renders PDFs and logs the result so it can be looked up later (see webapp.py)."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Literal

from jobapplier.common import resume_parser, resume_store
from jobapplier.common.claude_cli import call_claude_json
from jobapplier.common.config import GENERATED_DIR
from jobapplier.common.resume_template import render_cover_letter, render_resume
from jobapplier.webapp import analyze_cache, resume_diff, resume_patch, tailor_cache, tailoring_log
from jobapplier.webapp.resume_preview import render_resume_preview_html

GenerateOption = Literal["both", "resume", "cover_letter"]

# LinkedIn bundles a lot of page furniture around the actual JD text - its own "AI apply"
# prompt, "people you can reach out to", hiring-insights charts, company boilerplate,
# DEI/sustainability blurbs, "I'm interested" solicitation - with no stable DOM boundary
# between them (see extension/content.js's identical cleanDescription(), which does the same
# trim client-side for auto-detected JDs). This is the server-side backstop: it also applies
# when a JD is pasted by hand into the webapp, which never goes through the extension at all.
# Worth doing because jd_text gets sent as input to EVERY Claude call in this file (extraction,
# each classification batch, resume generation, cover letter generation) - trimming it once
# here cuts input tokens on all of them, not just one.
_JD_START_MARKERS = ["About the job", "About the Job"]
_JD_END_MARKERS = [
    "See how you compare",
    "Candidates who clicked apply",
    "Exclusive Job Seeker Insights",
    "About the company",
    "Show Premium Insights",
    "Interested in working with us",
    "People you can reach out to",
]


def _clean_jd_text(text: str) -> str:
    start = 0
    for marker in _JD_START_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            start = idx + len(marker)
            break

    end = len(text)
    for marker in _JD_END_MARKERS:
        idx = text.find(marker, start)
        if idx != -1 and idx < end:
            end = idx

    return text[start:end].strip()


EXTRACT_REQUIREMENTS_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

Read the raw, pasted job description below. This is an EXTRACTION pass only - just identify
what the JD asks for, don't compare it against any resume yet.

Return ONLY a JSON object with exactly four top-level keys: "company", "title", "location",
"requirements".

"company"/"title"/"location": extracted from the job description (location null if not stated).

"requirements": a flat array covering every distinct core/must-have or clearly important
requirement/skill the JD states (scan its Required/Minimum/Desired Qualifications sections
and any other requirement-bearing text). Aim for the natural number of genuinely distinct
asks - typically 15-25 for a standard JD - don't pad the list by hair-splitting one skill
into several near-duplicate entries, and don't merge clearly different skills into one either.
Each item is a SHORT, crisp ATS-style phrase (2-6 words, e.g. "vector databases", "responsible
AI", "data governance", "process mapping") - NOT the full JD sentence copied verbatim. A JD
sentence that bundles several CLEARLY DIFFERENT skills (e.g. "implementing an AI best
practice (workflow enhancement, responsible AI controls, data governance, automation)")
should become separate items, one per distinct skill named - but a single cohesive
requirement should stay one item, not be split into artificial fragments (e.g. "pursuing a
bachelor's degree in Engineering or Computer Science" is ONE requirement, not two).

Output ONLY the JSON object, exactly once - no commentary, no reasoning, no self-correction,
no markdown code fences, and no second JSON object even if you reconsider partway through.

--- JOB DESCRIPTION ---
{jd_text}
"""

CLASSIFY_BATCH_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

You are given a candidate's master resume and a batch of short JD-requirement phrases
(already extracted from a job description - you don't need the full JD text to classify
them). For EACH phrase in the batch, decide using ONE decision rule: does it refer to the
SAME underlying skill/technology/practice as something already in the resume (even worded
more generally, more specifically, or as a named example of a category the resume
demonstrates), a DIFFERENT skill/technology/practice that's merely related, or nothing at all?

Return ONLY a JSON object with exactly two top-level keys: "matched_keywords",
"suggested_keywords".

"matched_keywords": a flat array containing every phrase FROM THE BATCH that's the SAME
thing as something already in the resume, just worded differently - a paraphrase needs no
approval (e.g. batch has "AI tools/platforms", resume already shows hands-on work with
Claude/LangChain - that's just a named example of the same category the resume already
demonstrates). Use the phrase's own text as given in the batch.

"suggested_keywords": every phrase FROM THE BATCH that names a genuinely DIFFERENT
skill/technology than anything already in the resume, but is honestly connectable to
something the candidate has actually done - needs the candidate's sign-off before use (e.g.
batch has "Kafka streaming pipelines", resume shows Spark+Glue BATCH ETL pipelines - related,
but streaming and batch aren't the same thing, so this can't be silently claimed as a match).
As {"term": str, "based_on": str} - "term" is the phrase's own text, "based_on" is ONE short
sentence (under 20 words) naming the specific existing experience that could truthfully
support it. Only include a phrase with a real, specific, defensible connection.

Any phrase in the batch that fits neither bucket should simply be omitted from both lists -
no need to enumerate what didn't match.

Output ONLY the JSON object, exactly once - no commentary, no reasoning, no self-correction,
no markdown code fences, and no second JSON object even if you reconsider partway through.

--- MASTER RESUME ---
{master_resume_json}

--- REQUIREMENTS BATCH TO CLASSIFY ---
{batch_json}
"""

ANALYZE_BATCH_SIZE = 5
ANALYZE_MAX_BATCHES = 6

HEADER = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

{task_sentence}

You are an expert resume writer, not a fabricator: every fact in your output must already
exist in the master resume below. You may reword, reorder, re-emphasize, or select among
existing bullets/skills/sections to better match the job description's language and
priorities - but you must NEVER invent an employer, title, date, skill, tool, metric, or
accomplishment that isn't already present in the master resume.

Return ONLY a JSON object with exactly {n_keys} top-level keys: {key_list}.
"""

APPROVED_KEYWORDS_BLOCK = """
The candidate has reviewed and personally confirmed the following JD-related terms genuinely
apply to their real experience (with their own reasoning below) - you may naturally weave
this language into existing bullets/skills where it fits, but do not invent a new bullet,
employer, or metric just to use a term; only reword/re-emphasize real, existing content:
{keyword_lines}
"""

NOTES_BLOCK = """
Additional instructions from the candidate to take into account (still must stay truthful -
do not invent facts beyond real content in the master resume, but use these notes to guide
emphasis, wording, or framing):
{notes}
"""

RESUME_BLOCK = """
"resume_patch" describes ONLY what changes from the master resume below - never repeat
unchanged content back. Schema:
{
  "profile_content": str,
  "skills": {"categories": [{"name": str, "items": [str, ...]}]} | null,
  "entries": [
    {
      "header_left_bold": str,
      "bullets": [{"keep": int} | {"text": str}, ...] | null,
      "subentries": [{"header_left_bold": str, "bullets": [{"keep": int} | {"text": str}, ...]}] | null
    }
  ]
}

Tailoring guidance - act as a senior hiring manager reviewing this for an ATS and for human
recruiters:
- "profile_content": the full new text of the Profile/summary paragraph, rewritten to
  foreground the candidate's most relevant existing experience for this job, using the JD's
  own terminology where it truthfully applies, to maximize ATS keyword match. Always provide
  this - a resume's summary should always be worth re-targeting for the specific job.
- "skills": omit entirely (or use null) if Technical Skills doesn't need to change. Otherwise
  the FULL new set of categories/items, reordered to put the most JD-relevant ones first - the
  set of skills must stay identical to the master resume's (no additions or removals) UNLESS a
  term appears in the approved-keywords list below, in which case you may fold it into the
  relevant category as a real skill.
- "entries": ONLY the experience/project entries that need at least one bullet reworded or
  reordered, identified by "header_left_bold" copied EXACTLY from the master resume below -
  omit any entry with nothing worth changing entirely, do not list it just to leave it
  untouched. For each entry you DO include, "bullets" must list every one of that entry's
  bullets, in final order: {"keep": N} to reuse the master's Nth bullet (0-indexed, counting
  from the master resume below) verbatim, or {"text": "..."} for a bullet you're rewording -
  in Action + Context + Result form, kept to about 2 lines each, using the JD's terminology
  WHERE that terminology truthfully describes what the bullet already says. Do not delete
  substantive content or metrics, and do not invent a bullet that doesn't correspond to
  something the candidate actually did. "subentries" works the same way, one level deeper.
- Do not change dates, titles, company names, or numeric metrics - and since those never
  change, never include "header_left_normal"/"header_right"/"two_line"/"sub_left"/"sub_right"
  in a patched entry, only "header_left_bold" and whichever of "bullets"/"subentries" you're
  actually changing.
- Never touch "name", "contact", or "tagline" - they are not part of this patch at all.
"""

COVER_LETTER_BLOCK = """
"cover_letter" must match this schema:
{
  "name": str,
  "contact": [str, ...],
  "date": str,
  "greeting": str,
  "body_paragraphs": [str, ...],
  "signoff": str
}
- "greeting" should be "Dear Hiring Manager," unless a specific hiring contact name is given below.
- 3-4 body paragraphs: (1) which role/company and genuine interest, (2) 1-2 concrete,
  true accomplishments from the resume most relevant to this JD, (3) why this candidate's
  background fits what the JD is asking for, (4) brief closing/call to action.
- Every claim in the cover letter must be traceable to the master resume - no invented facts.
- "signoff" should be "Sincerely," (or similar).
"""

FOOTER = """
Output ONLY the JSON object, exactly once - no commentary, no reasoning, no self-correction,
no markdown code fences, and no second JSON object even if you reconsider partway through.

--- MASTER RESUME (source of truth - do not add facts beyond this) ---
{master_resume_json}

--- JOB (company: {company}, title: {job_title}, location: {job_location}) ---
{jd_text}

Today's date (for the cover letter header): {today}
"""


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "job"


def _compute_match_score(matched_count: int, suggested_count: int, core_requirement_count: int) -> int:
    """Deterministic 0-10 score: matched keywords count as full coverage, suggested ones (not
    yet in the resume, only honestly connectable) count as half - computed from counts rather
    than asked as a holistic LLM judgment, since that produced different scores (e.g. 6 vs 8)
    across identical runs on the same JD/resume. core_requirement_count is the denominator
    (total distinct requirements the JD lists); falls back to the keyword counts themselves if
    Claude didn't return a usable one, so a missing/zero value never divides by zero."""
    denominator = core_requirement_count if core_requirement_count > 0 else max(matched_count + suggested_count, 1)
    coverage = (matched_count + 0.5 * suggested_count) / denominator
    return max(0, min(10, round(coverage * 10)))


# Every Claude call in this file uses a minimal, purpose-built system prompt instead of the
# CLI's default (see claude_cli.py's docstring for the ~25k-cached-token overhead this avoids
# on every single call), but deliberately stays on the CLI's DEFAULT model rather than
# overriding to Haiku. Measured on a real classify_batch call (9.6KB resume, 5-item batch):
# Haiku took 27.7-62.6s and cost $0.021-0.047, while the default model (Sonnet) took 5.5-8.0s
# for $0.035-0.041 - a smaller/cheaper-per-token model was neither faster nor actually cheaper
# here, because it generated far more tokens visibly struggling with the matched/
# suggested/unmet judgment call against a multi-section resume.
#
# Two separate prompts, not one generic one: extraction/classification is a JUDGMENT task
# (does this phrase match something in the resume, yes/no/related), while resume/cover-letter
# generation is a WRITING task. These were briefly merged into one "JSON generation tool"
# wording, then split back apart on a user report of match scores unreliably coming back 0 -
# unconfirmed whether the merged wording was actually the cause (repeated backend testing
# afterward couldn't reproduce a bad score), but reverting to task-accurate wording for the
# judgment call specifically is a safe, no-downside precaution either way, so it stays split.
_CLASSIFY_SYSTEM_PROMPT = (
    "You are a JSON extraction/classification tool. Follow the user's instructions exactly "
    "and respond with nothing but the requested JSON."
)
_GENERATE_SYSTEM_PROMPT = (
    "You are a JSON generation tool. Follow the user's instructions exactly and respond with "
    "nothing but the requested JSON."
)


def _extract_requirements(jd_text: str) -> dict:
    prompt = EXTRACT_REQUIREMENTS_INSTRUCTIONS.replace("{jd_text}", jd_text)
    return call_claude_json(prompt, system_prompt=_CLASSIFY_SYSTEM_PROMPT)


def _classify_batch(master_resume_json: str, batch: list[str]) -> dict:
    prompt = (
        CLASSIFY_BATCH_INSTRUCTIONS
        .replace("{master_resume_json}", master_resume_json)
        .replace("{batch_json}", json.dumps(batch, indent=2))
    )
    return call_claude_json(prompt, system_prompt=_CLASSIFY_SYSTEM_PROMPT)


def _chunk(items: list, n_chunks: int) -> list[list]:
    if not items or n_chunks <= 1:
        return [items] if items else []
    chunk_size = -(-len(items) // n_chunks)  # ceil division
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def analyze_jd(jd_text: str) -> dict:
    """Fast pass: company/title/location + fit score + honest keyword suggestions, with no
    resume rewriting yet. Meant to return quickly so the UI has something to show well before
    full generation would finish.

    Two phases rather than one big call: (1) extract the JD's requirement phrases (cheap -
    just reading the JD, no resume comparison), then (2) classify those phrases against the
    master resume in a few concurrent batches instead of one long sequential pass. Splitting
    the classification work is what actually helps here - trimming each item's JSON down
    (tried first) barely moved the needle, because the real cost is the number of individual
    matched/suggested/unmet judgments, not how verbosely each one is written out. Running
    those judgments as parallel batches is bounded by the slowest batch instead of the sum of
    all of them, the same concurrency trick used for resume+cover-letter generation.

    Checks analyze_cache first - re-opening a job already analyzed against the SAME master
    resume (including a spurious re-extraction from content.js re-publishing an unchanged job,
    see panel.js's isNewJob guard) returns instantly instead of re-running the pipeline."""
    jd_text = _clean_jd_text(jd_text)
    master_resume = resume_parser.parse_and_cache(**resume_store.get_active_paths())

    cached = analyze_cache.get(jd_text, master_resume)
    if cached is not None:
        return cached

    extracted = _extract_requirements(jd_text)
    requirements = extracted.get("requirements", [])

    n_batches = min(ANALYZE_MAX_BATCHES, max(1, -(-len(requirements) // ANALYZE_BATCH_SIZE)))
    batches = _chunk(requirements, n_batches)

    matched_keywords: list[str] = []
    suggested_keywords: list[dict] = []
    if batches:
        # Serialize once and share across all concurrent batch calls, instead of each one
        # independently re-running json.dumps() on the same master resume.
        master_resume_json = json.dumps(master_resume, indent=2)
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(_classify_batch, master_resume_json, batch) for batch in batches
            ]
            for future in futures:
                batch_result = future.result()
                matched_keywords.extend(batch_result.get("matched_keywords", []))
                suggested_keywords.extend(batch_result.get("suggested_keywords", []))

    # Every extracted requirement ends up matched, suggested, or neither - so the total is
    # exactly len(requirements), not a separately-asked-for number that could disagree.
    core_requirement_count = len(requirements)

    result = {
        "company": extracted.get("company") or "Unknown Company",
        "title": extracted.get("title") or "Unknown Title",
        "location": extracted.get("location") or "",
        "match_score": _compute_match_score(len(matched_keywords), len(suggested_keywords), core_requirement_count),
        "matched_keywords": matched_keywords,
        "suggested_keywords": suggested_keywords,
        "core_requirement_count": core_requirement_count,
    }
    analyze_cache.set(jd_text, master_resume, result)
    return result


def _build_prompt(
    jd_text: str,
    master_resume: dict,
    company: str,
    title: str,
    location: str,
    kind: Literal["resume", "cover_letter"],
    approved_keywords: list[dict],
    notes: str,
) -> str:
    if kind == "resume":
        keys = ["resume_patch"]
        task_sentence = "Tailor a candidate's resume for the job described below."
        body = RESUME_BLOCK
    else:
        keys = ["cover_letter"]
        task_sentence = "Draft a cover letter for the job described below."
        body = COVER_LETTER_BLOCK

    header = HEADER.format(
        task_sentence=task_sentence,
        n_keys=len(keys),
        key_list=", ".join(f'"{k}"' for k in keys),
    )

    if approved_keywords:
        keyword_lines = "\n".join(
            f'- "{kw["term"]}" - {kw.get("based_on", "")}' for kw in approved_keywords
        )
        body += APPROVED_KEYWORDS_BLOCK.format(keyword_lines=keyword_lines)

    if notes.strip():
        body += NOTES_BLOCK.format(notes=notes.strip())

    footer = (
        FOOTER
        .replace("{master_resume_json}", json.dumps(master_resume, indent=2))
        .replace("{company}", company)
        .replace("{job_title}", title)
        .replace("{job_location}", location)
        .replace("{jd_text}", jd_text)
        .replace("{today}", date.today().strftime("%B %d, %Y"))
    )

    return header + body + footer


def _generate_resume(jd_text, master_resume, company, title, location, approved_keywords, notes):
    prompt = _build_prompt(
        jd_text, master_resume, company, title, location, "resume", approved_keywords, notes
    )
    result = call_claude_json(prompt, system_prompt=_GENERATE_SYSTEM_PROMPT)
    if "resume_patch" not in result:
        raise ValueError("Claude response missing 'resume_patch' key")
    # Claude only sends back what changed (see RESUME_BLOCK) - this reconstructs the full
    # resume dict render_resume()/resume_diff.diff_resume() expect, same as if Claude had
    # written the whole thing out itself. A malformed patch (unknown header, bad bullet index)
    # raises here, same as the old "missing key" check above - both mean the model didn't
    # follow the schema, and it's better to surface that than silently render something wrong.
    full_resume = resume_patch.apply_patch(master_resume, result["resume_patch"])
    return {"resume": full_resume}


def _generate_cover_letter(jd_text, master_resume, company, title, location, approved_keywords, notes):
    prompt = _build_prompt(
        jd_text, master_resume, company, title, location, "cover_letter", approved_keywords, notes
    )
    result = call_claude_json(prompt, system_prompt=_GENERATE_SYSTEM_PROMPT)
    if "cover_letter" not in result:
        raise ValueError("Claude response missing 'cover_letter' key")
    return result


def tailor_from_jd(
    jd_text: str,
    company: str,
    title: str,
    location: str = "",
    generate: GenerateOption = "both",
    approved_keywords: list[dict] | None = None,
    notes: str = "",
    matched_keyword_count: int = 0,
    suggested_keyword_count: int = 0,
    core_requirement_count: int = 0,
    source: str = "",
) -> dict:
    """Generates the resume and/or cover letter for a job already identified by analyze_jd().
    When generate="both", the two calls run concurrently instead of as one merged call.

    matched_keyword_count/suggested_keyword_count/core_requirement_count come from that same
    analyze_jd() call, so the match score can be recomputed deterministically here (approved
    keywords move from "suggested" to "matched" credit) instead of asking Claude to judge the
    tailored resume's fit all over again - see _compute_match_score's docstring for why."""
    jd_text = _clean_jd_text(jd_text)
    approved_keywords = approved_keywords or []
    active_paths = resume_store.get_active_paths()
    master_resume = resume_parser.parse_and_cache(**active_paths)

    cache_params = {
        "company": company,
        "title": title,
        "location": location,
        "generate": generate,
        "approved_keywords": approved_keywords,
        "notes": notes,
        "matched_keyword_count": matched_keyword_count,
        "suggested_keyword_count": suggested_keyword_count,
        "core_requirement_count": core_requirement_count,
    }
    cached = tailor_cache.get(jd_text, master_resume, cache_params)
    if cached is not None:
        return cached

    want_resume = generate in ("both", "resume")
    want_cover = generate in ("both", "cover_letter")

    resume_result = None
    cover_result = None
    if want_resume and want_cover:
        with ThreadPoolExecutor(max_workers=2) as executor:
            resume_future = executor.submit(
                _generate_resume, jd_text, master_resume, company, title, location,
                approved_keywords, notes,
            )
            cover_future = executor.submit(
                _generate_cover_letter, jd_text, master_resume, company, title, location,
                approved_keywords, notes,
            )
            resume_result = resume_future.result()
            cover_result = cover_future.result()
    elif want_resume:
        resume_result = _generate_resume(
            jd_text, master_resume, company, title, location, approved_keywords, notes
        )
    else:
        cover_result = _generate_cover_letter(
            jd_text, master_resume, company, title, location, approved_keywords, notes
        )

    slug = f"{_slugify(company)}_{_slugify(title)}_{datetime.now():%Y%m%d%H%M%S}"
    # Approved keywords are now genuinely woven into the resume, so they've earned full
    # credit instead of the suggested/half-credit they had at analyze time.
    final_matched = matched_keyword_count + len(approved_keywords)
    final_suggested = max(suggested_keyword_count - len(approved_keywords), 0)
    match_score = _compute_match_score(final_matched, final_suggested, core_requirement_count)

    record = {
        "company": company,
        "title": title,
        "location": location,
        "resume_path": "",
        "cover_letter_path": "",
        "match_score": match_score,
        "source": source,
    }
    extra = {"changes": [], "resume_preview_html": ""}

    if resume_result:
        resume_pdf = GENERATED_DIR / f"{slug}_resume.pdf"
        render_resume(
            resume_result["resume"], resume_pdf,
            style_json_path=active_paths["style_json_path"], photo_path=active_paths["photo_path"],
        )
        record["resume_path"] = str(resume_pdf)

        highlighted_resume = resume_diff.diff_resume(master_resume, resume_result["resume"])
        extra["changes"] = resume_diff.summarize_changes(master_resume, highlighted_resume)
        extra["resume_preview_html"] = render_resume_preview_html(highlighted_resume)

    if cover_result:
        cover_letter_pdf = GENERATED_DIR / f"{slug}_cover_letter.pdf"
        render_cover_letter(
            cover_result["cover_letter"], cover_letter_pdf,
            style_json_path=active_paths["style_json_path"], photo_path=active_paths["photo_path"],
        )
        record["cover_letter_path"] = str(cover_letter_pdf)

    tailoring_log.append_record(record)
    result = {**record, **extra}
    tailor_cache.set(jd_text, master_resume, cache_params, result)
    return result


def search_log(query: str) -> list[dict]:
    """Simple case-insensitive substring match of the query's words against
    company/title/location fields - no Claude call needed for lookups."""
    records = tailoring_log.load_records()
    query = query.strip().lower()
    if not query:
        return list(reversed(records))

    tokens = [t for t in re.split(r"\W+", query) if len(t) >= 3] or [query]
    matches = []
    for record in records:
        haystack = " ".join(
            [record.get("company", ""), record.get("title", ""), record.get("location", "")]
        ).lower()
        if any(token in haystack for token in tokens):
            matches.append(record)
    return list(reversed(matches))
