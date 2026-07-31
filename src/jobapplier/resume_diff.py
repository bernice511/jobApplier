"""Compares a tailored resume against the master resume to flag which bullets/paragraphs
were actually reworded, for on-screen preview only (see resume_preview.py). Never touches
the PDF-rendering path in resume_template.py, so downloaded PDFs never carry highlight
markup - that's a display-only annotation computed on our side, not something Claude emits.

Entries are matched by header_left_bold (company/project name), since tailoring must not
change those - see tailor.py's guardrails. Bullets within a matched entry may be reordered,
so they're matched by best text similarity rather than position.
"""
from __future__ import annotations

from difflib import SequenceMatcher

UNCHANGED_THRESHOLD = 0.97  # near-identical text (allowing for trivial punctuation drift)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_match_ratio(text: str, candidates: list[str]) -> float:
    if not candidates:
        return 0.0
    return max(_similarity(text, c) for c in candidates)


def _diff_bullets(master_bullets: list[str], tailored_bullets: list[str]) -> list[dict]:
    return [
        {"text": b, "changed": _best_match_ratio(b, master_bullets) < UNCHANGED_THRESHOLD}
        for b in tailored_bullets
    ]


def diff_resume(master_resume: dict, tailored_resume: dict) -> dict:
    """Returns a copy of tailored_resume's sections where every bullet/paragraph becomes
    {"text": str, "changed": bool} instead of a plain string, for resume_preview.py to
    render with highlights. Structure/keys outside of bullets/paragraph content are passed
    through unchanged."""
    master_entries_by_header = {
        entry["header_left_bold"]: entry
        for section in master_resume.get("sections", [])
        if section["type"] == "entries"
        for entry in section["entries"]
    }

    highlighted_sections = []
    for section in tailored_resume.get("sections", []):
        if section["type"] == "paragraph":
            master_section = next(
                (s for s in master_resume.get("sections", [])
                 if s["type"] == "paragraph" and s["title"] == section["title"]),
                None,
            )
            master_content = master_section["content"] if master_section else ""
            highlighted_sections.append({
                **section,
                "content": {
                    "text": section["content"],
                    "changed": _similarity(section["content"], master_content) < UNCHANGED_THRESHOLD,
                },
            })

        elif section["type"] == "skills":
            highlighted_sections.append(section)

        elif section["type"] == "entries":
            highlighted_entries = []
            for entry in section["entries"]:
                master_entry = master_entries_by_header.get(entry["header_left_bold"], {})
                master_bullets = list(master_entry.get("bullets", []))
                master_subentries_by_header = {
                    sub["header_left_bold"]: sub for sub in master_entry.get("subentries", [])
                }

                highlighted_subentries = []
                for sub in entry.get("subentries", []):
                    master_sub_bullets = list(
                        master_subentries_by_header.get(sub["header_left_bold"], {}).get("bullets", [])
                    )
                    highlighted_subentries.append({
                        **sub,
                        "bullets": _diff_bullets(master_sub_bullets, sub.get("bullets", [])),
                    })

                highlighted_entries.append({
                    **entry,
                    "bullets": _diff_bullets(master_bullets, entry.get("bullets", [])),
                    "subentries": highlighted_subentries,
                })
            highlighted_sections.append({**section, "entries": highlighted_entries})

    return {**tailored_resume, "sections": highlighted_sections}
