"""Paste-a-JD flow: identify company/title/location AND tailor the resume and/or cover
letter in one Claude call (unlike tailor.py's tailor_application, which is used by the
LinkedIn flow in main.py where company/title/location are already known from LinkedIn's own
metadata and don't need to be re-derived from text). Keeping this as its own prompt - rather
than a second call into tailor.py - is what keeps this flow to a single `claude` CLI round
trip, which is most of where the latency is (each CLI invocation has real subprocess/startup
overhead on top of the model call itself).

The caller can ask for just the resume, just the cover letter, or both (GenerateOption) -
skipping the unneeded half shrinks the prompt/response and cuts generation time further.

Renders PDFs and logs the result so it can be looked up later (see webapp.py)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Literal

from jobapplier import resume_diff, resume_parser, tailoring_log
from jobapplier.claude_cli import call_claude_json
from jobapplier.config import GENERATED_DIR
from jobapplier.resume_preview import render_resume_preview_html
from jobapplier.resume_template import render_cover_letter, render_resume

GenerateOption = Literal["both", "resume", "cover_letter"]

HEADER = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

You are given a raw, pasted job description. First identify the company, job title, and
location from it. {task_sentence}

You are an expert resume writer, not a fabricator: every fact in your output must already
exist in the master resume below. You may reword, reorder, re-emphasize, or select among
existing bullets/skills/sections to better match the job description's language and
priorities - but you must NEVER invent an employer, title, date, skill, tool, metric, or
accomplishment that isn't already present in the master resume.

Return ONLY a JSON object with exactly {n_keys} top-level keys: {key_list}.

"company": the company name, extracted from the job description (string).
"title": the job title, extracted from the job description (string).
"location": the location if mentioned, else null.
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
  what the bullet already says (e.g. if the JD says "LLM orchestration" and a bullet
  already describes building a multi-agent LLM system, it's fine to surface that phrase -
  but don't claim experience with a tool or technique the resume never mentions). Do not
  delete substantive content or metrics.
- In "Technical Skills", you may reorder categories/items to put the most JD-relevant ones
  first, but the set of skills must stay identical to the input (no additions or removals) -
  never add a skill just because the JD mentions it.
- Do not change dates, titles, company names, or numeric metrics.
- Keep every section from the input present in the output, in the same section order, and
  keep entries (companies/projects) within each section in the same order.

"match_score" must be an integer 0-10: your honest estimate of how well the *tailored*
resume's existing skills/experience overlap this JD's key requirements (keyword coverage,
seniority, domain fit). Do not inflate it - a resume genuinely missing JD-critical skills
should score lower, since you cannot fabricate missing skills to raise the score.

"changes" must be a list of 3-8 short strings, each describing one concrete edit you
actually made and why (e.g. "Reordered bullets under Acme Corp to lead with the
multi-agent LLM project, matching the JD's top priority"). Do not list vague statements
like "improved overall quality" - be specific about what moved or was reworded.
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

--- JOB DESCRIPTION (raw paste - extract company/title/location from this) ---
{jd_text}

Today's date (for the cover letter header): {today}
"""


def _build_prompt(jd_text: str, master_resume: dict, generate: GenerateOption) -> str:
    want_resume = generate in ("both", "resume")
    want_cover = generate in ("both", "cover_letter")

    keys = ["company", "title", "location"]
    if want_resume:
        keys += ["resume", "match_score", "changes"]
    if want_cover:
        keys.append("cover_letter")

    if want_resume and want_cover:
        task_sentence = "Then tailor a candidate's resume and draft a cover letter for that job."
    elif want_resume:
        task_sentence = "Then tailor a candidate's resume for that job (no cover letter needed)."
    else:
        task_sentence = "Then draft a cover letter for that job (no resume edit needed)."

    header = HEADER.format(
        task_sentence=task_sentence,
        n_keys=len(keys),
        key_list=", ".join(f'"{k}"' for k in keys),
    )

    body = ""
    if want_resume:
        body += RESUME_BLOCK
    if want_cover:
        body += COVER_LETTER_BLOCK

    footer = (
        FOOTER
        .replace("{master_resume_json}", json.dumps(master_resume, indent=2))
        .replace("{jd_text}", jd_text)
        .replace("{today}", date.today().strftime("%B %d, %Y"))
    )

    return header + body + footer


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "job"


def tailor_from_jd(jd_text: str, generate: GenerateOption = "both") -> dict:
    """Runs the paste-JD flow in a single Claude call, generating only what's asked for.
    Returns a dict with company/title/location and whichever PDF path(s) were generated."""
    want_resume = generate in ("both", "resume")
    want_cover = generate in ("both", "cover_letter")

    master_resume = resume_parser.parse_and_cache()
    prompt = _build_prompt(jd_text, master_resume, generate)
    result = call_claude_json(prompt)

    if want_resume and "resume" not in result:
        raise ValueError("Claude response missing 'resume' key")
    if want_cover and "cover_letter" not in result:
        raise ValueError("Claude response missing 'cover_letter' key")

    company = result.get("company") or "Unknown Company"
    title = result.get("title") or "Unknown Title"
    location = result.get("location") or ""
    slug = f"{_slugify(company)}_{_slugify(title)}_{datetime.now():%Y%m%d%H%M%S}"

    record = {
        "company": company,
        "title": title,
        "location": location,
        "resume_path": "",
        "cover_letter_path": "",
        "match_score": result.get("match_score") if want_resume else "",
    }
    extra = {"changes": [], "resume_preview_html": ""}

    if want_resume:
        resume_pdf = GENERATED_DIR / f"{slug}_resume.pdf"
        render_resume(result["resume"], resume_pdf)
        record["resume_path"] = str(resume_pdf)

        highlighted_resume = resume_diff.diff_resume(master_resume, result["resume"])
        extra["changes"] = result.get("changes", [])
        extra["resume_preview_html"] = render_resume_preview_html(highlighted_resume)

    if want_cover:
        cover_letter_pdf = GENERATED_DIR / f"{slug}_cover_letter.pdf"
        render_cover_letter(result["cover_letter"], cover_letter_pdf)
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
