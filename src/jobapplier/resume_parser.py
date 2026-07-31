"""Parses the master resume PDF into the structured JSON schema used by resume_template.py.

Extraction uses pdfplumber with a tight x_tolerance, which matters: this resume's PDF
font encoding drops space characters at the default tolerance (e.g. "Built and maintained"
becomes "Builtandmaintained"), so x_tolerance must stay low (~1.5) or words run together.

Structuring raw text into sections/entries is delegated to Claude, since resumes vary in
section order/naming and hand-written regex parsing breaks the moment the source resume
changes even slightly.

Calls Claude via the `claude` CLI (jobapplier.claude_cli), not the Anthropic API - see that
module's docstring for why.
"""
from __future__ import annotations

import json
from pathlib import Path

import pdfplumber

from jobapplier.claude_cli import call_claude_json
from jobapplier.config import MASTER_RESUME_JSON, MASTER_RESUME_PDF

SCHEMA_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

Convert the resume text below into this exact JSON schema (no extra commentary, valid JSON only):

{
  "name": str,
  "contact": [str, ...],
  "sections": [
    // one of three shapes, in the same order they appear in the resume:

    {"title": str, "type": "paragraph", "content": str},

    {"title": str, "type": "skills", "categories": [{"name": str, "items": [str, ...]}]},

    {"title": str, "type": "entries", "entries": [
      {
        "header_left_bold": str,          // e.g. company/institution/award/project name
        "header_left_normal": str | null, // text after a "|" on the same line, if present (e.g. award description, or project's school)
        "header_right": str,              // location or year, right-aligned in the original
        "two_line": bool,                 // true if there's a second line (role/degree + dates), like Education/Experience entries
        "sub_left": str | null,           // role or degree text (only if two_line)
        "sub_right": str | null,          // dates (only if two_line)
        "bullets": [str, ...],            // bullet points directly under this entry (before any subentries)
        "subentries": [                   // nested sub-roles within one employer, e.g. distinct projects at the same company
          {"header_left_bold": str, "header_right": str, "bullets": [str, ...]}
        ]
      }
    ]}
  ]
}

Rules:
- Preserve the original wording of every bullet and heading exactly - do not rephrase, summarize, or fix grammar.
- "contact" should be the individual pipe-separated items from the contact line (phone, email, linkedin, github, etc), in order.
- Every section from the resume must be included, in its original order, using whichever of the three "type" shapes fits it.
- Use "subentries": [] (empty list) when an entry has no nested sub-roles.
- Use null (not empty string) for header_left_normal, sub_left, sub_right when not applicable.
- Output ONLY the JSON object, nothing else.

Resume text:
---
{resume_text}
---
"""


def extract_text(pdf_path: Path = MASTER_RESUME_PDF) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text(x_tolerance=1.5) or "")
    return "\n".join(pages)


def structure_resume(resume_text: str) -> dict:
    prompt = SCHEMA_INSTRUCTIONS.replace("{resume_text}", resume_text)
    return call_claude_json(prompt)


def parse_and_cache(pdf_path: Path = MASTER_RESUME_PDF,
                     json_path: Path = MASTER_RESUME_JSON, force: bool = False) -> dict:
    if json_path.exists() and not force:
        return json.loads(json_path.read_text())

    text = extract_text(pdf_path)
    structured = structure_resume(text)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(structured, indent=2))
    return structured


if __name__ == "__main__":
    data = parse_and_cache(force=True)
    print(f"Parsed resume -> {MASTER_RESUME_JSON}")
