"""Flags when a newly-analyzed job looks like one you've already generated a resume for (or
tracked further along in the pipeline) - so you notice a repost, or the same opening cross-
posted on a different board, before spending another Claude call or applying twice.

Fuzzy title+company match via difflib - this is a deterministic string-similarity check, not
a judgment call, so no LLM involved. Company names need to match closely (typos aside, it's
either the same employer or it isn't); job titles are allowed more slack since the same
opening is often listed with slightly different titles ("Software Engineer" vs "Software
Engineer II" vs "Software Engineer, Backend") even on the same board."""
from __future__ import annotations

from difflib import SequenceMatcher

from jobapplier.webapp import tailoring_log

COMPANY_THRESHOLD = 0.82
TITLE_THRESHOLD = 0.55


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def find_duplicates(company: str, title: str) -> list[dict]:
    """Returns prior tailoring_log records that look like the same job as (company, title),
    most-similar first. Empty if company/title is blank - nothing meaningful to compare."""
    if not _normalize(company) or not _normalize(title):
        return []

    matches = []
    for record in tailoring_log.load_records():
        if not record.get("company") or not record.get("title"):
            continue
        company_sim = _similarity(company, record["company"])
        title_sim = _similarity(title, record["title"])
        if company_sim >= COMPANY_THRESHOLD and title_sim >= TITLE_THRESHOLD:
            matches.append({
                "company": record["company"],
                "title": record["title"],
                "status": record.get("status") or "saved",
                "timestamp": record.get("timestamp", ""),
                "match_score": record.get("match_score", ""),
                "similarity": round((company_sim + title_sim) / 2, 2),
            })

    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches
