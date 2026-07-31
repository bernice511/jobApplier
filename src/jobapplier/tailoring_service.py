"""Paste-a-JD flow: extract company/title/location straight from the pasted text, tailor
the resume + cover letter via the existing tailor.py, render PDFs, and log the result so it
can be looked up later (see webapp.py)."""
from __future__ import annotations

import re
from datetime import datetime

from jobapplier import resume_diff, resume_parser, tailor, tailoring_log
from jobapplier.claude_cli import call_claude_json
from jobapplier.config import GENERATED_DIR
from jobapplier.resume_preview import render_resume_preview_html
from jobapplier.resume_template import render_cover_letter, render_resume

EXTRACT_META_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

Read the job description below and extract:
{
  "company": str,          // company name
  "title": str,            // job title
  "location": str | null   // location if mentioned, else null
}
Output ONLY the JSON object, exactly once - no commentary, no reasoning, no self-correction,
no markdown code fences, and no second JSON object even if you reconsider partway through.
If you're unsure of a field, use null rather than thinking out loud about it.

--- JOB DESCRIPTION ---
{jd_text}
"""


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "job"


def extract_job_meta(jd_text: str) -> dict:
    prompt = EXTRACT_META_INSTRUCTIONS.replace("{jd_text}", jd_text)
    meta = call_claude_json(prompt)
    return {
        "company": meta.get("company") or "Unknown Company",
        "title": meta.get("title") or "Unknown Title",
        "location": meta.get("location") or "",
    }


def tailor_from_jd(jd_text: str) -> dict:
    """Runs the full paste-JD flow. Returns a dict with company/title/location and the
    generated resume/cover-letter PDF paths."""
    master_resume = resume_parser.parse_and_cache()
    meta = extract_job_meta(jd_text)

    job = {**meta, "description": jd_text}
    tailored = tailor.tailor_application(master_resume, job)

    slug = f"{_slugify(meta['company'])}_{_slugify(meta['title'])}_{datetime.now():%Y%m%d%H%M%S}"
    resume_pdf = GENERATED_DIR / f"{slug}_resume.pdf"
    cover_letter_pdf = GENERATED_DIR / f"{slug}_cover_letter.pdf"

    render_resume(tailored["resume"], resume_pdf)
    render_cover_letter(tailored["cover_letter"], cover_letter_pdf)

    highlighted_resume = resume_diff.diff_resume(master_resume, tailored["resume"])
    preview_html = render_resume_preview_html(highlighted_resume)

    record = {
        **meta,
        "resume_path": str(resume_pdf),
        "cover_letter_path": str(cover_letter_pdf),
        "match_score": tailored.get("match_score"),
    }
    tailoring_log.append_record(record)

    return {
        **record,
        "changes": tailored.get("changes", []),
        "resume_preview_html": preview_html,
    }


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
