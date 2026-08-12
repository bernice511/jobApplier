"""Fetches job listings from Jooble's public search API (https://jooble.org/api/about) - a
second aggregator alongside adzuna_source.py, used only by the webapp's live search (not the
scheduled job_alerts pipeline, which stays Adzuna-only for now). Requires JOOBLE_API_KEY in
.env (free signup); if it's blank, callers should just skip this source entirely rather than
treat it as an error, since it's an optional addition, not a required one.

Unlike Adzuna, Jooble's API is POST-with-JSON-body, and only returns a short snippet rather
than the full job description - callers that feed the result into match scoring should know
that a short snippet will score less reliably than Adzuna's full description text.
"""
from __future__ import annotations

import requests

from jobapplier.common.config import Config

JOOBLE_TIMEOUT_SECONDS = 20


class JoobleError(RuntimeError):
    pass


def _search_url(api_key: str) -> str:
    return f"https://jooble.org/api/{api_key}"


def fetch_jobs(query: str, location: str, config: Config, page: int = 1) -> list[dict]:
    """Returns normalized job dicts: {job_id, title, company, location, url, description,
    posted_date} - same shape as adzuna_source.fetch_jobs() so callers can merge both sources
    without caring which one a listing came from. description here is Jooble's short snippet,
    not a full job description. Raises JoobleError on a request failure or malformed response."""
    if not config.jooble_api_key:
        raise JoobleError("JOOBLE_API_KEY is not set in .env.")

    body = {"keywords": query, "location": location, "page": str(page)}
    try:
        response = requests.post(
            _search_url(config.jooble_api_key), json=body, timeout=JOOBLE_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise JoobleError(f"Jooble request failed for ({query!r}, {location!r}): {exc}") from exc
    except ValueError as exc:
        raise JoobleError(f"Jooble returned non-JSON for ({query!r}, {location!r}): {exc}") from exc

    jobs = []
    for result in payload.get("jobs", []):
        job_id = result.get("id")
        if not job_id:
            continue  # can't dedupe/track a listing with no stable id - skip it
        jobs.append({
            "job_id": f"jooble-{job_id}",
            "title": (result.get("title") or "").strip(),
            "company": result.get("company") or "Unknown Company",
            "location": result.get("location") or location,
            "url": result.get("link", ""),
            "description": result.get("snippet", ""),
            "posted_date": result.get("updated", ""),
        })
    return jobs
