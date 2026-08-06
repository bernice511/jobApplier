"""Daily job-alert pipeline entry point: python -m jobapplier.job_alerts.main

For every (job title, location) pair in config, fetches current listings from Adzuna, skips
anything already evaluated in a prior run (job_alerts/store.py), scores each new listing
against the master resume via webapp.tailoring_service.analyze_jd() - the same deterministic
0-10 match-score/keyword logic already shown on the webapp's "Tailor" page - and records every
scored job (even below-threshold ones, so they're never re-scored) for the /alerts dashboard
to read.

Meant to be run on a schedule (see the launchd plist under scripts/), not interactively -
prints a one-line summary per run so a scheduler's log file is enough to sanity-check it.
"""
from __future__ import annotations

import sys

from jobapplier.common.claude_cli import ClaudeCLIError
from jobapplier.common.config import load_config
from jobapplier.job_alerts import adzuna_source, store
from jobapplier.webapp import tailoring_service


def run() -> int:
    config = load_config()
    problems = config.validate_job_alerts()
    if problems:
        for problem in problems:
            print(f"Config problem: {problem}", file=sys.stderr)
        return 1

    already_seen = store.seen_ids()
    fetched: dict[str, dict] = {}
    any_query_succeeded = False
    for title in config.job_titles:
        for location in config.locations:
            try:
                jobs = adzuna_source.fetch_jobs(title, location, config)
            except adzuna_source.AdzunaError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                continue
            any_query_succeeded = True
            for job in jobs:
                fetched[job["job_id"]] = job  # same listing can surface under several queries

    if not any_query_succeeded:
        print("Error: every Adzuna query failed - check ADZUNA_APP_ID/ADZUNA_APP_KEY.", file=sys.stderr)
        return 1

    new_jobs = [job for job_id, job in fetched.items() if job_id not in already_seen]
    to_score = new_jobs[: config.max_jobs_to_score_per_run]
    skipped_for_cap = len(new_jobs) - len(to_score)

    scored_count = 0
    matched_count = 0
    for job in to_score:
        try:
            analysis = tailoring_service.analyze_jd(job["description"])
        except ClaudeCLIError as exc:
            # If the claude CLI can't be reached (e.g. no login session in this scheduler's
            # context), every remaining call will fail identically - stop now instead of
            # burning through the rest of the batch on a doomed retry loop, and don't mark
            # unscored jobs as seen since they were never actually evaluated.
            print(f"Error: Claude scoring failed ({exc}) - stopping run.", file=sys.stderr)
            break

        match_score = analysis.get("match_score", 0)
        store.add_or_update({
            "job_id": job["job_id"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "url": job["url"],
            "match_score": match_score,
            "matched_keywords": analysis.get("matched_keywords", []),
            "suggested_keywords": analysis.get("suggested_keywords", []),
        })
        scored_count += 1
        if match_score >= config.alert_match_threshold:
            matched_count += 1

    print(
        f"job_alerts: fetched {len(fetched)} listings, {len(new_jobs)} new, "
        f"scored {scored_count} ({skipped_for_cap} deferred to next run), "
        f"{matched_count} matched (>= {config.alert_match_threshold}/10)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
