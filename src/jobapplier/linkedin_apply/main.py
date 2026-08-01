"""CLI orchestrator: search -> tailor -> render -> apply (Easy Apply semi-auto, external
sites prepared-only) -> track, for one run."""
from __future__ import annotations

import sys
from datetime import date

from jobapplier.common import resume_parser
from jobapplier.common.config import GENERATED_DIR, load_config
from jobapplier.common.resume_template import render_cover_letter, render_resume
from jobapplier.linkedin_apply import apply_easy, apply_external, tailor, tracker
from jobapplier.linkedin_apply.linkedin_browser import ensure_logged_in, launch_browser
from jobapplier.linkedin_apply.linkedin_search import search_jobs
from jobapplier.linkedin_apply.rate_limiter import RateLimiter


def main() -> None:
    config = load_config()
    problems = config.validate()
    if problems:
        print("Configuration problems found:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nFix these in .env (and make sure your resume PDF is in place) before running.")
        sys.exit(1)

    print("Parsing/loading master resume...")
    master_resume = resume_parser.parse_and_cache()

    already_seen_ids = tracker.load_applied_job_ids()
    print(f"{len(already_seen_ids)} previously-tracked jobs will be skipped.")

    limiter = RateLimiter(
        config.min_delay_seconds, config.max_delay_seconds, config.max_applications_per_run
    )

    with launch_browser(config.browser_profile_dir) as context:
        page = ensure_logged_in(context)

        job_stream = search_jobs(
            page,
            job_titles=config.job_titles,
            locations=config.locations,
            seniority_levels=config.seniority_levels,
            already_seen_ids=already_seen_ids,
        )

        for job in job_stream:
            if not limiter.has_capacity():
                print(f"\nReached MAX_APPLICATIONS_PER_RUN ({config.max_applications_per_run}). Stopping.")
                break

            print(f"\n=== {job['title']} @ {job['company']} ({job['location']}) ===")
            limiter.wait()

            try:
                tailored = tailor.tailor_application(master_resume, job)
            except Exception as exc:
                print(f"  [error] tailoring failed: {exc} - skipping this job.")
                continue

            resume_pdf = GENERATED_DIR / f"{job['job_id']}_resume.pdf"
            cover_letter_pdf = GENERATED_DIR / f"{job['job_id']}_cover_letter.pdf"
            render_resume(tailored["resume"], resume_pdf)
            render_cover_letter(tailored["cover_letter"], cover_letter_pdf)

            if job["easy_apply"]:
                status = apply_easy.apply_easy(page, resume_pdf, job["title"], job["company"])
            else:
                status = apply_external.apply_external(page, job["apply_url"])

            if status == "applied":
                limiter.record_application()

            tracker.append_record({
                "job_id": job["job_id"],
                "title": job["title"],
                "company": job["company"],
                "location": job["location"],
                "date_applied": date.today().isoformat() if status == "applied" else "",
                "status": status,
                "resume_path": str(resume_pdf),
                "cover_letter_path": str(cover_letter_pdf),
                "job_url": job["job_url"],
                "notes": "",
            })
            print(f"  -> status: {status}")

    print("\nDone for this run.")


if __name__ == "__main__":
    main()
