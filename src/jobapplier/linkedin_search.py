"""Drives LinkedIn's own Jobs search UI (not an undocumented API) to find and scrape listings.

NOTE ON FRAGILITY: LinkedIn's DOM class names change periodically and aren't a stable public
contract. All the CSS selectors used to find page elements are grouped in `SELECTORS` below so
they're easy to update in one place if a run starts turning up zero results / zero descriptions.
Every per-card extraction is wrapped so one bad card logs a warning and gets skipped rather than
crashing the whole search.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from playwright.sync_api import Page

LINKEDIN_BASE = "https://www.linkedin.com"

# LinkedIn's "Date posted" filter codes (seconds lookback). r86400 = past 24 hours.
POSTED_LAST_24H = "r86400"

# LinkedIn's "Experience level" filter codes.
SENIORITY_CODES = {
    "internship": "1",
    "entry level": "2",
    "associate": "3",
    "mid-senior level": "4",
    "director": "5",
    "executive": "6",
}

SELECTORS = {
    "job_card": "li[data-occludable-job-id]",
    "job_card_id_attr": "data-occludable-job-id",
    "job_card_title": "a.job-card-list__title, a.job-card-container__link",
    "job_card_company": ".job-card-container__primary-description, .artdeco-entity-lockup__subtitle",
    "job_card_location": ".job-card-container__metadata-item",
    "results_list": "ul.jobs-search__results-list, div.scaffold-layout__list ul",
    "detail_title": "h1.job-details-jobs-unified-top-card__job-title, h1.jobs-unified-top-card__job-title",
    "detail_company": ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name",
    "detail_description": ".jobs-description__content, .jobs-box__html-content",
    "see_more_button": "button:has-text('See more')",
    "easy_apply_button": "button:has-text('Easy Apply')",
    "external_apply_button": "button:has-text('Apply'):not(:has-text('Easy Apply'))",
}


def build_search_url(keywords: str, location: str, seniority_levels: list[str]) -> str:
    params = {"keywords": keywords, "location": location, "f_TPR": POSTED_LAST_24H}
    codes = [
        SENIORITY_CODES[level.strip().lower()]
        for level in seniority_levels
        if level.strip().lower() in SENIORITY_CODES
    ]
    if codes:
        params["f_E"] = ",".join(codes)
    return f"{LINKEDIN_BASE}/jobs/search/?{urlencode(params)}"


def _scroll_load_cards(page: Page, target_count: int, max_scrolls: int = 12) -> None:
    for _ in range(max_scrolls):
        cards = page.locator(SELECTORS["job_card"])
        if cards.count() >= target_count:
            return
        page.mouse.wheel(0, 1800)
        time.sleep(1.2)


def _extract_job_id(card) -> str | None:
    job_id = card.get_attribute(SELECTORS["job_card_id_attr"])
    return job_id.strip() if job_id else None


def get_job_description_text(page: Page) -> str:
    see_more = page.locator(SELECTORS["see_more_button"])
    if see_more.count() > 0:
        try:
            see_more.first.click(timeout=2000)
        except Exception:
            pass
    desc = page.locator(SELECTORS["detail_description"])
    if desc.count() == 0:
        return ""
    return desc.first.inner_text().strip()


def is_easy_apply(page: Page) -> bool:
    return page.locator(SELECTORS["easy_apply_button"]).count() > 0


def get_external_apply_url(page: Page) -> str | None:
    button = page.locator(SELECTORS["external_apply_button"])
    if button.count() == 0:
        return None
    try:
        with page.context.expect_page(timeout=8000) as popup_info:
            button.first.click()
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=8000)
        url = popup.url
        popup.close()
        return url
    except Exception:
        return None


def search_jobs(page: Page, job_titles: list[str], locations: list[str],
                 seniority_levels: list[str], already_seen_ids: set[str],
                 max_per_query: int = 25):
    """Yields job dicts: job_id, title, company, location, description, easy_apply,
    apply_url (external only, else None), job_url. Skips job_ids in already_seen_ids."""
    for title in job_titles:
        for location in locations:
            url = build_search_url(title, location, seniority_levels)
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2)
            _scroll_load_cards(page, target_count=max_per_query)

            cards = page.locator(SELECTORS["job_card"])
            count = min(cards.count(), max_per_query)

            for i in range(count):
                card = cards.nth(i)
                try:
                    job_id = _extract_job_id(card)
                    if not job_id or job_id in already_seen_ids:
                        continue

                    card.scroll_into_view_if_needed()
                    card.click()
                    time.sleep(1.5)

                    job_url = f"{LINKEDIN_BASE}/jobs/view/{job_id}/"
                    detail_title_el = page.locator(SELECTORS["detail_title"])
                    detail_company_el = page.locator(SELECTORS["detail_company"])

                    job_title_text = detail_title_el.first.inner_text().strip() if detail_title_el.count() else title
                    company_text = detail_company_el.first.inner_text().strip() if detail_company_el.count() else ""
                    description = get_job_description_text(page)
                    easy_apply = is_easy_apply(page)
                    apply_url = None if easy_apply else get_external_apply_url(page)

                    already_seen_ids.add(job_id)
                    yield {
                        "job_id": job_id,
                        "title": job_title_text,
                        "company": company_text,
                        "location": location,
                        "description": description,
                        "easy_apply": easy_apply,
                        "apply_url": apply_url,
                        "job_url": job_url,
                    }
                except Exception as exc:
                    print(f"  [warn] skipped a job card due to: {exc}")
                    continue
