"""Compares a tailored resume against the master resume to flag which words/phrases were
actually reworded, for on-screen preview only (see resume_preview.py). Never touches the
PDF-rendering path in resume_template.py, so downloaded PDFs never carry highlight markup -
that's a display-only annotation computed on our side, not something Claude emits.

Entries are matched by header_left_bold (company/project name), since tailoring must not
change those - see tailor.py's guardrails. Bullets within a matched entry may be reordered,
so they're matched by best text similarity rather than position. Within a matched bullet,
highlighting is word-level (not whole-bullet), so a one-word rewording doesn't light up an
entire two-line bullet.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _tokenize(text: str) -> list[str]:
    """Splits into words and whitespace runs, keeping both as tokens so segments can be
    rejoined without losing spacing."""
    return re.findall(r"\S+|\s+", text)


def _word_diff_segments(master_text: str, tailored_text: str) -> list[dict]:
    """Returns [{"text": str, "changed": bool}, ...] covering tailored_text, merging
    consecutive same-status tokens into one segment. Deleted (master-only) words don't
    appear, since the output must render exactly tailored_text."""
    a = _tokenize(master_text)
    b = _tokenize(tailored_text)
    segments: list[dict] = []
    for tag, _, _, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "delete":
            continue
        chunk = "".join(b[j1:j2])
        if not chunk:
            continue
        changed = tag != "equal"
        if segments and segments[-1]["changed"] == changed:
            segments[-1]["text"] += chunk
        else:
            segments.append({"text": chunk, "changed": changed})
    return segments


def _best_match(text: str, candidates: list[str]) -> str:
    if not candidates:
        return ""
    return max(candidates, key=lambda c: _similarity(text, c))


def _diff_text(text: str, candidates: list[str]) -> list[dict]:
    """Word-diffs text against its best-matching candidate. No whole-text similarity
    short-circuit: a long paragraph with one small edit can still score a high overall
    ratio, which would wrongly skip the word-level diff and hide a real change."""
    return _word_diff_segments(_best_match(text, candidates), text)


def _diff_bullets(master_bullets: list[str], tailored_bullets: list[str]) -> list[dict]:
    return [{"segments": _diff_text(b, master_bullets)} for b in tailored_bullets]


def diff_resume(master_resume: dict, tailored_resume: dict) -> dict:
    """Returns a copy of tailored_resume's sections where every bullet/paragraph becomes
    {"segments": [{"text": str, "changed": bool}, ...]} instead of a plain string, for
    resume_preview.py to render with word-level highlights."""
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
            master_content = [master_section["content"]] if master_section else []
            highlighted_sections.append({
                **section,
                "content": {"segments": _diff_text(section["content"], master_content)},
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


def summarize_changes(master_resume: dict, highlighted_resume: dict) -> list[str]:
    """Derives a short "what changed" summary directly from diff_resume()'s output, instead
    of also asking Claude to narrate it in prose - the diff already knows exactly which
    bullets/summary changed, so a separate LLM-written description would just spend output
    tokens restating data this module already has. Skills sections aren't word-diffed (see
    diff_resume() above - they pass through untouched), so those are compared directly
    against master_resume's items instead."""
    master_skills_by_category: dict[str, set[str]] = {
        cat["name"]: set(cat["items"])
        for section in master_resume.get("sections", [])
        if section["type"] == "skills"
        for cat in section["categories"]
    }

    def bullets_changed(bullets: list[dict]) -> int:
        return sum(1 for b in bullets if any(seg["changed"] for seg in b["segments"]))

    lines: list[str] = []
    for section in highlighted_resume.get("sections", []):
        if section["type"] == "paragraph":
            if any(seg["changed"] for seg in section["content"]["segments"]):
                lines.append(f'Updated the "{section["title"]}" section')

        elif section["type"] == "skills":
            for cat in section["categories"]:
                added = [
                    item for item in cat["items"]
                    if item not in master_skills_by_category.get(cat["name"], set())
                ]
                if added:
                    lines.append(f'Added {", ".join(added)} to "{cat["name"]}"')

        elif section["type"] == "entries":
            for entry in section["entries"]:
                count = bullets_changed(entry.get("bullets", []))
                if count:
                    noun = "bullet" if count == 1 else "bullets"
                    lines.append(f'Reworded {count} {noun} in {entry["header_left_bold"]}')
                for sub in entry.get("subentries", []):
                    sub_count = bullets_changed(sub.get("bullets", []))
                    if sub_count:
                        noun = "bullet" if sub_count == 1 else "bullets"
                        lines.append(f'Reworded {sub_count} {noun} in {sub["header_left_bold"]}')

    return lines
