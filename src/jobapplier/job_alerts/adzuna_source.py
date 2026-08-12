"""Fetches job listings from Adzuna's public search API (https://developer.adzuna.com) - a
free, official aggregator API, not scraping. Requires ADZUNA_APP_ID/ADZUNA_APP_KEY (free
signup) in .env, loaded via common.config.load_config().
"""
from __future__ import annotations

import requests

from jobapplier.common.config import Config

ADZUNA_TIMEOUT_SECONDS = 20
RESULTS_PER_PAGE = 20


class AdzunaError(RuntimeError):
    pass


# Adzuna's own IT/engineering-adjacent categories (from GET /v1/api/jobs/us/categories) - a
# curated subset relevant to this tool, not the full list (which also has retail, hospitality,
# etc.). Letting a search scope to one of these is what makes a broad, high-recall term like
# "intern" or "co-op" usable without it being swamped by unrelated industries.
RELEVANT_CATEGORIES = {
    "it-jobs": "IT Jobs",
    "engineering-jobs": "Engineering Jobs",
    "graduate-jobs": "Graduate Jobs",
    "scientific-qa-jobs": "Scientific & QA Jobs",
}


def _job_url(country: str, page: int) -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def fetch_jobs(
    title: str, location: str, config: Config, category: str | None = None, page: int = 1
) -> list[dict]:
    """Returns normalized job dicts: {job_id, title, company, location, url, description,
    posted_date}. Raises AdzunaError on a request failure or malformed response - callers
    decide whether to skip this (title, location) pair for the run or abort entirely, rather
    than this function silently swallowing errors and returning an empty (indistinguishable
    from "no jobs found") list.

    category, if given, must be one of RELEVANT_CATEGORIES' keys - passed straight through to
    Adzuna's own `category` filter. page lets a caller pull more than the first 20 results for
    a popular query (e.g. plain "internship" has hundreds of matches for a single city)."""
    params = {
        "app_id": config.adzuna_app_id,
        "app_key": config.adzuna_app_key,
        "what": title,
        "where": location,
        "results_per_page": RESULTS_PER_PAGE,
        "content-type": "application/json",
    }
    if category:
        params["category"] = category
    try:
        response = requests.get(
            _job_url(config.adzuna_country, page), params=params, timeout=ADZUNA_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise AdzunaError(f"Adzuna request failed for ({title!r}, {location!r}): {exc}") from exc
    except ValueError as exc:
        raise AdzunaError(f"Adzuna returned non-JSON for ({title!r}, {location!r}): {exc}") from exc

    jobs = []
    for result in payload.get("results", []):
        job_id = result.get("id")
        if not job_id:
            continue  # can't dedupe/track a listing with no stable id - skip it
        jobs.append({
            "job_id": str(job_id),
            "title": result.get("title", "").strip(),
            "company": (result.get("company") or {}).get("display_name", "Unknown Company"),
            "location": (result.get("location") or {}).get("display_name", location),
            "url": result.get("redirect_url", ""),
            "description": result.get("description", ""),
            "posted_date": result.get("created", ""),
        })
    return jobs
