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


def _job_url(country: str) -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def fetch_jobs(title: str, location: str, config: Config) -> list[dict]:
    """Returns normalized job dicts: {job_id, title, company, location, url, description,
    posted_date}. Raises AdzunaError on a request failure or malformed response - callers
    decide whether to skip this (title, location) pair for the run or abort entirely, rather
    than this function silently swallowing errors and returning an empty (indistinguishable
    from "no jobs found") list."""
    params = {
        "app_id": config.adzuna_app_id,
        "app_key": config.adzuna_app_key,
        "what": title,
        "where": location,
        "results_per_page": RESULTS_PER_PAGE,
        "content-type": "application/json",
    }
    try:
        response = requests.get(
            _job_url(config.adzuna_country), params=params, timeout=ADZUNA_TIMEOUT_SECONDS
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
