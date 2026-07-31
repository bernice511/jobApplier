"""Tailors the master resume + drafts a cover letter for a specific job, via Claude.

Guardrail: the model may only reword, reorder, re-emphasize, or select among content that
already exists in the master resume - it must never invent employers, dates, skills, or
metrics that aren't already there. That's enforced entirely through the prompt, so any
future changes to TAILOR_INSTRUCTIONS should preserve those constraints.

Calls Claude via the `claude` CLI (jobapplier.claude_cli), not the Anthropic API - see that
module's docstring for why.
"""
from __future__ import annotations

import json
from datetime import date

from jobapplier.claude_cli import call_claude_json

TAILOR_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

You are tailoring a candidate's resume and drafting a cover letter for one specific job.
You are an expert resume writer, not a fabricator: every fact in your output must already
exist in the master resume below. You may reword, reorder, re-emphasize, or select among
existing bullets/skills/sections to better match the job description's language and
priorities - but you must NEVER invent an employer, title, date, skill, tool, metric, or
accomplishment that isn't already present in the master resume.

Return ONLY a JSON object with exactly two top-level keys: "resume" and "cover_letter".

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

Tailoring guidance for "resume":
- Rewrite the "Profile"/summary paragraph to foreground the candidate's most relevant
  existing experience for this job.
- Within each experience/project entry, you may reorder bullets so the most JD-relevant
  ones come first, and reword bullets to use the JD's terminology WHERE that terminology
  truthfully describes what the bullet already says (e.g. if the JD says "LLM orchestration"
  and a bullet already describes building a multi-agent LLM system, it's fine to surface that
  phrase - but don't claim experience with a tool or technique the resume never mentions).
  Do not delete substantive content or metrics.
- In "Technical Skills", you may reorder categories/items to put the most JD-relevant ones
  first, but the set of skills must stay identical to the input (no additions or removals).
- Do not change dates, titles, company names, or numeric metrics.
- Keep every section from the input present in the output, in the same section order.

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

Output ONLY the JSON object, no commentary, no markdown code fences.

--- MASTER RESUME (source of truth - do not add facts beyond this) ---
{master_resume_json}

--- JOB ---
Company: {company}
Title: {job_title}
Location: {job_location}
Description:
{job_description}

Today's date (for the cover letter header): {today}
"""


def tailor_application(master_resume: dict, job: dict) -> dict:
    """job: {"company": str, "title": str, "location": str, "description": str}
    Returns {"resume": {...same schema as master_resume...}, "cover_letter": {...}}
    """
    prompt = (
        TAILOR_INSTRUCTIONS
        .replace("{master_resume_json}", json.dumps(master_resume, indent=2))
        .replace("{company}", job["company"])
        .replace("{job_title}", job["title"])
        .replace("{job_location}", job.get("location", ""))
        .replace("{job_description}", job["description"])
        .replace("{today}", date.today().strftime("%B %d, %Y"))
    )
    result = call_claude_json(prompt)

    if "resume" not in result or "cover_letter" not in result:
        raise ValueError("Claude response missing 'resume' or 'cover_letter' key")
    return result
