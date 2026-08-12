"""Reconstructs a full resume dict from master_resume + a sparse patch (see
tailoring_service.RESUME_PATCH_BLOCK for the schema Claude is asked to produce).

Generation used to ask Claude to re-express the ENTIRE resume on every call, even though most
of a resume's content - unchanged bullets, dates, company names, skills that don't need
reordering - is identical from one JD to the next. That's pure output-token waste: decoding is
the slow part of a generation call, and most of what was being decoded was just the master
resume typed back out verbatim. This module is the other half of that fix: it takes a patch
naming only what actually changed and reconstructs the same full resume shape
render_resume()/resume_diff.diff_resume() already expect, so neither of those - or anything
downstream - needs to know a patch was ever involved.

"keep": <index> in a bullet patch means "reuse master's bullet at this position verbatim";
"text": <str> means a reworded bullet. Entries/subentries not mentioned in the patch are used
from master_resume completely unchanged. name/contact/tagline are always copied from
master_resume - the patch never touches them, so Claude is never even asked to reproduce them."""
from __future__ import annotations


class ResumePatchError(ValueError):
    pass


def _resolve_bullets(master_bullets: list, patched_bullets: list | None, label: str) -> list[str]:
    if patched_bullets is None:
        return list(master_bullets)

    resolved = []
    for item in patched_bullets:
        if not isinstance(item, dict):
            raise ResumePatchError(f'{label}: bullet patch item must be an object, got {item!r}')
        if "text" in item:
            resolved.append(item["text"])
        elif "keep" in item:
            idx = item["keep"]
            if not isinstance(idx, int) or idx < 0 or idx >= len(master_bullets):
                raise ResumePatchError(
                    f'{label}: "keep" index {idx!r} is out of range '
                    f'(this entry has {len(master_bullets)} bullets)'
                )
            resolved.append(master_bullets[idx])
        else:
            raise ResumePatchError(f'{label}: bullet patch item needs "text" or "keep", got {item!r}')
    return resolved


def apply_patch(master_resume: dict, patch: dict) -> dict:
    """Returns a full resume dict in the same shape master_resume is in, with the patch's
    changes applied on top. Raises ResumePatchError if the patch references an entry/subentry
    header that doesn't exist in master_resume, or a "keep" index that's out of range - either
    means Claude didn't follow the "match headers/indices exactly" instruction, and silently
    dropping the mismatch would produce a resume missing content the model actually intended
    to include, with no signal that anything went wrong."""
    if "profile_content" not in patch:
        raise ResumePatchError('resume_patch is missing required "profile_content"')

    entry_patches_by_header = {e["header_left_bold"]: e for e in (patch.get("entries") or [])}
    known_headers = {
        entry["header_left_bold"]
        for section in master_resume.get("sections", [])
        if section["type"] == "entries"
        for entry in section["entries"]
    }
    unknown = set(entry_patches_by_header) - known_headers
    if unknown:
        raise ResumePatchError(f"resume_patch referenced unknown entries: {sorted(unknown)}")

    sections = []
    for section in master_resume.get("sections", []):
        if section["type"] == "paragraph":
            sections.append({**section, "content": patch["profile_content"]})

        elif section["type"] == "skills":
            skills_patch = patch.get("skills")
            sections.append(section if skills_patch is None else {**section, **skills_patch})

        elif section["type"] == "entries":
            entries = []
            for entry in section["entries"]:
                header = entry["header_left_bold"]
                entry_patch = entry_patches_by_header.get(header)
                if entry_patch is None:
                    entries.append(entry)
                    continue

                bullets = _resolve_bullets(entry.get("bullets", []), entry_patch.get("bullets"), header)

                sub_patches_by_header = {
                    s["header_left_bold"]: s for s in (entry_patch.get("subentries") or [])
                }
                sub_known = {s["header_left_bold"] for s in entry.get("subentries", [])}
                sub_unknown = set(sub_patches_by_header) - sub_known
                if sub_unknown:
                    raise ResumePatchError(
                        f'resume_patch referenced unknown subentries under "{header}": {sorted(sub_unknown)}'
                    )

                subentries = []
                for sub in entry.get("subentries", []):
                    sub_header = sub["header_left_bold"]
                    sub_patch = sub_patches_by_header.get(sub_header)
                    sub_bullets = _resolve_bullets(
                        sub.get("bullets", []),
                        sub_patch.get("bullets") if sub_patch else None,
                        f'{header} / {sub_header}',
                    )
                    subentries.append({**sub, "bullets": sub_bullets})

                entries.append({**entry, "bullets": bullets, "subentries": subentries})
            sections.append({**section, "entries": entries})

        else:
            sections.append(section)

    return {
        "name": master_resume.get("name", ""),
        "tagline": master_resume.get("tagline"),
        "contact": master_resume.get("contact", []),
        "sections": sections,
    }
