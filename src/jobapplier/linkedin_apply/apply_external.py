"""Handles jobs that redirect off LinkedIn to a third-party ATS (Workday, Greenhouse, etc.).

By design this never auto-fills anything - external ATS forms are too varied to generalize
safely. It just opens the application page in a new tab so the tailored resume/cover letter
(already rendered by the caller) are ready for the user to submit by hand.
"""
from __future__ import annotations

from playwright.sync_api import Page


def apply_external(page: Page, apply_url: str | None) -> str:
    """Returns "manual_pending" if the page was opened, "manual_pending_no_url" if there
    was no apply_url to open (e.g. detection failed) - either way, no auto-fill is attempted."""
    if not apply_url:
        print("  [warn] no external apply URL captured - marking as manual_pending with no link.")
        return "manual_pending_no_url"

    new_page = page.context.new_page()
    new_page.goto(apply_url, wait_until="domcontentloaded")
    print(f"\n>>> Opened external application page: {apply_url}")
    print(">>> This is a third-party site, so it won't be auto-filled - "
          "the tailored resume/cover letter are saved and ready for you to attach by hand.")
    return "manual_pending"
