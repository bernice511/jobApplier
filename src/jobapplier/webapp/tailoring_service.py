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

from jobapplier.common import resume_parser
from jobapplier.common.claude_cli import call_claude_json
from jobapplier.common.config import GENERATED_DIR
from jobapplier.common.resume_template import render_cover_letter, render_resume
from jobapplier.webapp import resume_diff, tailoring_log
from jobapplier.webapp.resume_preview import render_resume_preview_html

GenerateOption = Literal["both", "resume", "cover_letter"]

ANALYZE_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

You are given a raw, pasted job description and a candidate's master resume. This is an
ANALYSIS pass only - do not rewrite the resume yet.

Return ONLY a JSON object with exactly four top-level keys: "company", "title", "location",
"requirements".

"company"/"title"/"location": extracted from the job description (location null if not stated).

"requirements": a list covering EVERY distinct core/must-have or clearly important
requirement or skill the JD states (scan its Required/Minimum/Desired Qualifications sections
and any other requirement-bearing text) - list all of them, don't stop at a round number and
don't skip any to keep the list short; this list is the sole basis for the match score, so an
incomplete list understates it and a padded one inflates it. Each item:
{"requirement": str, "status": "matched" | "suggested" | "unmet", "detail": str | null}

"requirement" must be a SHORT, crisp ATS-style keyword or phrase (2-6 words, e.g. "vector
databases", "responsible AI", "data governance", "process mapping") - NOT the full JD
sentence copied verbatim. JD bullets often bundle several distinct skills into one sentence
(e.g. "implementing an AI best practice (workflow enhancement, responsible AI controls, data
governance, automation)") - split a bundled sentence like that into separate atomic items
("workflow enhancement", "responsible AI", "data governance", "automation"), one requirement
each, rather than one long item containing all of them. This list is shown to the candidate
as keyword chips/checkboxes, so length and atomicity both matter, not just coverage.

"status" uses ONE decision rule: does this requirement refer to the SAME underlying
skill/technology/practice as something already in the resume (even if worded more generally,
more specifically, or as a named example of a category the resume demonstrates), a
DIFFERENT skill/technology/practice that's merely related, or nothing at all?
- "matched": same thing, different wording - a paraphrase needs no approval (e.g. JD says
  "AI tools/platforms (e.g. Microsoft Copilot)", resume already shows hands-on work with
  Claude/LangChain - Copilot is just a named example of the same category the resume already
  demonstrates).
- "suggested": genuinely different thing, only related - needs the candidate's sign-off
  before use (e.g. JD says "Kafka streaming pipelines", resume shows Spark+Glue BATCH ETL
  pipelines - related data-pipeline experience, but streaming and batch aren't the same
  thing, so this can't be silently claimed). Only use this status if there's a real, specific,
  defensible connection - never invent one just to avoid "unmet".
- "unmet": no genuine connection to anything in the resume.

"detail": for "matched", a brief note of which existing resume content demonstrates it; for
"suggested", a specific one-sentence explanation of which existing experience could
truthfully support it (e.g. "Built a RAG pipeline over 100GB+ of biomedical data using
OpenSearch, which is a vector search backend"); for "unmet", null.

Output ONLY the JSON object, exactly once - no commentary, no reasoning, no self-correction,
no markdown code fences, and no second JSON object even if you reconsider partway through.

--- MASTER RESUME ---
{master_resume_json}

--- JOB DESCRIPTION ---
{jd_text}
"""

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
"resume" must be the master resume re-expressed in this exact schema (identical shape to
the input - same section types, same keys):
{
  "name": str,
  "contact": [str, ...],
  "sections": [
    {"title": str, "type": "paragraph", "content": str},
    {"title": str, "type": "skills", "categories": [{"name": str, "items": [str, ...]}]},
    {"title": str, "type": "entries", "entries": [
      {
        "header_left_bold": str, "header_left_normal": str | null,
        "header_right": str,
        "two_line": bool, "sub_left": str | null, "sub_right": str | null,
        "bullets": [str, ...],
        "subentries": [{"header_left_bold": str, "header_right": str, "bullets": [str, ...]}]
      }
    ]}
  ]
}

Tailoring guidance for "resume" - act as a senior hiring manager reviewing this for an
ATS and for human recruiters:
- Rewrite the "Profile"/summary paragraph to foreground the candidate's most relevant
  existing experience for this job, using the JD's own terminology where it truthfully
  applies, to maximize ATS keyword match.
- Within each experience/project entry, you may reorder bullets so the most JD-relevant
  ones come first, and reword bullets - in Action + Context + Result form, kept to about
  2 lines each - to use the JD's terminology WHERE that terminology truthfully describes
  what the bullet already says. Do not delete substantive content or metrics.
- In "Technical Skills", you may reorder categories/items to put the most JD-relevant ones
  first, but the set of skills must stay identical to the input (no additions or removals)
  UNLESS a term appears in the approved-keywords list below, in which case you may fold it
  into the relevant category as a real skill.
- Do not change dates, titles, company names, or numeric metrics.
- Keep every section from the input present in the output, in the same section order, and
  keep entries (companies/projects) within each section in the same order.

"changes" must be a list of 3-8 short strings, each describing one concrete edit you
actually made and why. Do not list vague statements like "improved overall quality" - be
specific about what moved or was reworded.
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


def analyze_jd(jd_text: str) -> dict:
    """Fast pass: company/title/location + fit score + honest keyword suggestions, with no
    resume rewriting yet. Meant to return quickly so the UI has something to show well before
    full generation would finish."""
    master_resume = resume_parser.parse_and_cache()
    prompt = (
        ANALYZE_INSTRUCTIONS
        .replace("{master_resume_json}", json.dumps(master_resume, indent=2))
        .replace("{jd_text}", jd_text)
    )
    result = call_claude_json(prompt)
    requirements = result.get("requirements", [])
    matched_keywords = [r["requirement"] for r in requirements if r.get("status") == "matched"]
    suggested_keywords = [
        {"term": r["requirement"], "based_on": r.get("detail") or ""}
        for r in requirements if r.get("status") == "suggested"
    ]
    # Every requirement is classified exactly once (matched/suggested/unmet), so this count
    # can never be smaller than matched+suggested - unlike asking for three independent lists,
    # which could disagree and let the score below saturate past what the evidence supports.
    core_requirement_count = len(requirements)

    return {
        "company": result.get("company") or "Unknown Company",
        "title": result.get("title") or "Unknown Title",
        "location": result.get("location") or "",
        "match_score": _compute_match_score(len(matched_keywords), len(suggested_keywords), core_requirement_count),
        "matched_keywords": matched_keywords,
        "suggested_keywords": suggested_keywords,
        "core_requirement_count": core_requirement_count,
    }


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
        keys = ["resume", "changes"]
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
    result = call_claude_json(prompt)
    if "resume" not in result:
        raise ValueError("Claude response missing 'resume' key")
    return result


def _generate_cover_letter(jd_text, master_resume, company, title, location, approved_keywords, notes):
    prompt = _build_prompt(
        jd_text, master_resume, company, title, location, "cover_letter", approved_keywords, notes
    )
    result = call_claude_json(prompt)
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
) -> dict:
    """Generates the resume and/or cover letter for a job already identified by analyze_jd().
    When generate="both", the two calls run concurrently instead of as one merged call.

    matched_keyword_count/suggested_keyword_count/core_requirement_count come from that same
    analyze_jd() call, so the match score can be recomputed deterministically here (approved
    keywords move from "suggested" to "matched" credit) instead of asking Claude to judge the
    tailored resume's fit all over again - see _compute_match_score's docstring for why."""
    approved_keywords = approved_keywords or []
    want_resume = generate in ("both", "resume")
    want_cover = generate in ("both", "cover_letter")
    master_resume = resume_parser.parse_and_cache()

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
    }
    extra = {"changes": [], "resume_preview_html": ""}

    if resume_result:
        resume_pdf = GENERATED_DIR / f"{slug}_resume.pdf"
        render_resume(resume_result["resume"], resume_pdf)
        record["resume_path"] = str(resume_pdf)

        highlighted_resume = resume_diff.diff_resume(master_resume, resume_result["resume"])
        extra["changes"] = resume_result.get("changes", [])
        extra["resume_preview_html"] = render_resume_preview_html(highlighted_resume)

    if cover_result:
        cover_letter_pdf = GENERATED_DIR / f"{slug}_cover_letter.pdf"
        render_cover_letter(cover_result["cover_letter"], cover_letter_pdf)
        record["cover_letter_path"] = str(cover_letter_pdf)

    tailoring_log.append_record(record)
    return {**record, **extra}


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
