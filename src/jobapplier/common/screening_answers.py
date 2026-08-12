"""Shared load/save for data/answers/screening_answers.yaml - the structured personal-info
and free-text question/answer store originally used only by linkedin_apply/apply_easy.py.
Factored out here (rather than webapp/ importing from linkedin_apply/) so the browser
extension's autofill feature (served via webapp/app.py) can read/grow the same file without
crossing package boundaries the rest of this codebase deliberately keeps separate."""
from __future__ import annotations

import yaml

from jobapplier.common.config import SCREENING_ANSWERS_PATH


def load_screening_answers() -> dict:
    if not SCREENING_ANSWERS_PATH.exists():
        return {"patterns": {}}
    data = yaml.safe_load(SCREENING_ANSWERS_PATH.read_text()) or {}
    data.setdefault("patterns", {})
    return data


def save_screening_answers(data: dict) -> None:
    SCREENING_ANSWERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCREENING_ANSWERS_PATH.write_text(yaml.dump(data, sort_keys=False))


def add_pattern(question: str, answer: str) -> dict:
    """Used by the extension's autofill flow to save an answer for a question it couldn't
    match automatically, so - like apply_easy.py's own unknown-question handling - it's never
    asked again. Same shape/lowercasing as apply_easy.py already produces."""
    data = load_screening_answers()
    data["patterns"][question.strip().lower()] = answer
    save_screening_answers(data)
    return data


# Every structured field a user can edit from the webapp's profile page - "patterns" is
# deliberately excluded, since that's a free-text map managed only via add_pattern().
STRUCTURED_FIELDS = [
    "first_name",
    "last_name",
    "phone",
    "email",
    "work_authorization",
    "requires_sponsorship",
    "notice_period_days",
    "salary_expectation",
    "years_experience_default",
    "linkedin_url",
    "github_url",
    "website_url",
    # These four recur across almost every ATS (Workday/Greenhouse/Lever all ask some form of
    # each) but previously had no structured field - autofill could only pick them up via the
    # slower "learn it the first time you hit it" patterns fallback. Answering them once here
    # means a 1-click apply doesn't stall on the same handful of questions on every application.
    "how_heard",
    "willing_to_relocate",
    "currently_employed",
    "available_start_date",
]


def update_answers(fields: dict) -> dict:
    """Used by the webapp's profile-editing page so structured fields (name, phone, salary
    expectation, etc.) can be corrected from the UI instead of requiring a hand-edit of
    screening_answers.yaml - every autofill/apply_easy.py run afterward picks up the change
    automatically since they all read through this same module. Only known structured fields
    are accepted; anything else in `fields` is silently ignored rather than letting an
    unrelated key (or "patterns" itself) get overwritten by accident."""
    data = load_screening_answers()
    for key in STRUCTURED_FIELDS:
        if key in fields:
            data[key] = fields[key]
    save_screening_answers(data)
    return data
