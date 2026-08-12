"""Tiny key-value store for UI-only settings that aren't tied to a resume or ATS answers (so
far, just the light/dark theme preference) - deliberately separate from screening_answers.yaml,
which is autofill data and requires an active resume to resolve. Same file-based JSON approach
as tailor_cache.py/analyze_cache.py."""
from __future__ import annotations

import json

from jobapplier.common.config import DATA_DIR

APP_SETTINGS_JSON = DATA_DIR / "app_settings.json"

VALID_THEMES = ("light", "dark")
DEFAULT_THEME = "light"


def _load() -> dict:
    if not APP_SETTINGS_JSON.exists():
        return {}
    try:
        return json.loads(APP_SETTINGS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(settings: dict) -> None:
    APP_SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    APP_SETTINGS_JSON.write_text(json.dumps(settings, indent=2))


def get_theme() -> str:
    theme = _load().get("theme")
    return theme if theme in VALID_THEMES else DEFAULT_THEME


def set_theme(theme: str) -> str:
    if theme not in VALID_THEMES:
        raise ValueError(f"theme must be one of {VALID_THEMES}, got {theme!r}")
    settings = _load()
    settings["theme"] = theme
    _save(settings)
    return theme
