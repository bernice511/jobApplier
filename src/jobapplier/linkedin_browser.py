"""Launches a persistent, real-Chrome Playwright session dedicated to this tool.

Using a dedicated profile directory (not the user's everyday Chrome profile) avoids the
"profile already in use" lock conflict when the user's regular Chrome is open, while still
using real Chrome (channel="chrome") in headed mode - not headless - so traffic looks like
normal browsing rather than an automation fingerprint. The user logs into LinkedIn once
inside this profile; the session cookie persists across runs after that.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

LINKEDIN_BASE = "https://www.linkedin.com"
LOGIN_WAIT_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes to let the user log in by hand


@contextmanager
def launch_browser(profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        try:
            yield context
        finally:
            context.close()


def ensure_logged_in(context: BrowserContext) -> Page:
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(f"{LINKEDIN_BASE}/feed/", wait_until="domcontentloaded")

    looks_logged_out = (
        "/login" in page.url
        or "/checkpoint" in page.url
        or page.locator("input#username").count() > 0
    )
    if looks_logged_out:
        print(
            "\n>>> Please log into LinkedIn in the Chrome window that just opened.\n"
            ">>> This tool will continue automatically once your feed loads "
            f"(waiting up to {LOGIN_WAIT_TIMEOUT_MS // 60000} minutes)...\n"
        )
        page.wait_for_url(f"{LINKEDIN_BASE}/feed/**", timeout=LOGIN_WAIT_TIMEOUT_MS)

    return page
