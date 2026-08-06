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
