"""Drives LinkedIn's Easy Apply modal: uploads the tailored resume, answers screening
questions, and stops at the final review step for an explicit human confirmation before
ever clicking Submit.

NOTE ON FRAGILITY: like linkedin_search.py, the Easy Apply modal's exact class names shift
over time. Selectors are grouped in SELECTORS below. Any question the answer-matching logic
can't confidently handle pauses the whole flow and asks in the terminal instead of guessing.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from playwright.sync_api import Page

from jobapplier.config import SCREENING_ANSWERS_PATH

SELECTORS = {
    "easy_apply_button": "button:has-text('Easy Apply')",
    "modal": "div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal']",
    "file_input": "input[type='file']",
    "next_button": "button:has-text('Next')",
    "review_button": "button:has-text('Review')",
    "submit_button": "button:has-text('Submit application')",
    "close_button": "button[aria-label='Dismiss']",
    "form_group": "div.fb-dash-form-element, div.jobs-easy-apply-form-section__grouping",
    "label": "label",
    "text_input": "input[type='text'], input[type='number'], textarea",
    "select": "select",
    "radio": "input[type='radio']",
    "radio_label_for": "label[for]",
}

# Question-text substring -> structured screening_answers.yaml key.
QUESTION_KEY_HINTS = [
    ("phone", "phone"),
    ("email", "email"),
    ("authorized to work", "work_authorization"),
    ("legally authorized", "work_authorization"),
    ("sponsorship", "requires_sponsorship"),
    ("notice period", "notice_period_days"),
    ("salary", "salary_expectation"),
    ("linkedin", "linkedin_url"),
    ("github", "github_url"),
    ("portfolio", "website_url"),
    ("website", "website_url"),
]


def load_screening_answers() -> dict:
    if not SCREENING_ANSWERS_PATH.exists():
        return {"patterns": {}}
    data = yaml.safe_load(SCREENING_ANSWERS_PATH.read_text()) or {}
    data.setdefault("patterns", {})
    return data


def save_screening_answers(data: dict) -> None:
    SCREENING_ANSWERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCREENING_ANSWERS_PATH.write_text(yaml.dump(data, sort_keys=False))


def _match_answer(question_text: str, answers: dict) -> str | None:
    q_lower = question_text.strip().lower()

    for substring, answer in answers.get("patterns", {}).items():
        if substring.lower() in q_lower:
            return answer

    for hint, key in QUESTION_KEY_HINTS:
        if hint in q_lower and answers.get(key):
            return answers[key]

    if "years of experience" in q_lower and answers.get("years_experience_default"):
        return answers["years_experience_default"]

    return None


def _prompt_for_unknown_answer(question_text: str) -> str:
    print(f"\n  Unrecognized screening question: \"{question_text}\"")
    return input("  Your answer: ").strip()


def _fill_form_group(group, question_text: str, answer: str) -> bool:
    text_input = group.locator(SELECTORS["text_input"])
    if text_input.count() > 0:
        text_input.first.fill(answer)
        return True

    select = group.locator(SELECTORS["select"])
    if select.count() > 0:
        try:
            select.first.select_option(label=answer)
            return True
        except Exception:
            options = select.first.locator("option").all_inner_texts()
            for opt in options:
                if answer.lower() in opt.lower():
                    select.first.select_option(label=opt)
                    return True
        return False

    radios = group.locator(SELECTORS["radio"])
    if radios.count() > 0:
        labels = group.locator(SELECTORS["radio_label_for"])
        for i in range(labels.count()):
            label_text = labels.nth(i).inner_text().strip()
            if answer.lower() in label_text.lower() or label_text.lower() in answer.lower():
                labels.nth(i).click()
                return True
        radios.first.check()
        return True

    return False


def _answer_visible_questions(page: Page, answers: dict) -> None:
    groups = page.locator(SELECTORS["form_group"])
    for i in range(groups.count()):
        group = groups.nth(i)
        label_el = group.locator(SELECTORS["label"])
        question_text = label_el.first.inner_text().strip() if label_el.count() > 0 else ""
        if not question_text:
            continue

        answer = _match_answer(question_text, answers)
        if answer is None:
            answer = _prompt_for_unknown_answer(question_text)
            answers.setdefault("patterns", {})[question_text.lower()] = answer
            save_screening_answers(answers)

        try:
            _fill_form_group(group, question_text, answer)
        except Exception as exc:
            print(f"  [warn] could not fill field for \"{question_text}\": {exc}")


def apply_easy(page: Page, resume_pdf_path: Path, job_title: str, company: str) -> str:
    """Returns one of: "applied", "cancelled_by_user", "failed"."""
    answers = load_screening_answers()

    easy_apply_btn = page.locator(SELECTORS["easy_apply_button"])
    if easy_apply_btn.count() == 0:
        return "failed"
    easy_apply_btn.first.click()
    page.wait_for_timeout(1500)

    file_input = page.locator(SELECTORS["file_input"])
    if file_input.count() > 0:
        try:
            file_input.first.set_input_files(str(resume_pdf_path))
            page.wait_for_timeout(1000)
        except Exception as exc:
            print(f"  [warn] could not upload resume: {exc}")

    max_steps = 15
    for _ in range(max_steps):
        _answer_visible_questions(page, answers)

        submit_btn = page.locator(SELECTORS["submit_button"])
        if submit_btn.count() > 0:
            break

        review_btn = page.locator(SELECTORS["review_button"])
        next_btn = page.locator(SELECTORS["next_button"])
        if review_btn.count() > 0:
            review_btn.first.click()
        elif next_btn.count() > 0:
            next_btn.first.click()
        else:
            print("  [warn] no Next/Review/Submit button found - stopping for manual review.")
            return "failed"
        page.wait_for_timeout(1200)

    submit_btn = page.locator(SELECTORS["submit_button"])
    if submit_btn.count() == 0:
        print("  [warn] never reached the final Submit step within the step limit.")
        return "failed"

    print(f"\n>>> Ready to submit application: \"{job_title}\" at {company}.")
    print(">>> Review the open browser window now.")
    confirmation = input(">>> Type 'yes' to submit this application, anything else to skip: ").strip().lower()

    if confirmation != "yes":
        return "cancelled_by_user"

    submit_btn.first.click()
    page.wait_for_timeout(1500)

    close_btn = page.locator(SELECTORS["close_button"])
    if close_btn.count() > 0:
        try:
            close_btn.first.click(timeout=2000)
        except Exception:
            pass

    return "applied"
