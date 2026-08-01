"""Renders the highlighted-diff resume (see resume_diff.py) as an HTML snippet for on-screen
preview in the webapp - separate from resume_template.py's PDF path, so highlight markup
never ends up in the downloadable PDF."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "jinja"]),
)


def render_resume_preview_html(highlighted_resume: dict) -> str:
    template = _env.get_template("resume_preview.html.jinja")
    return template.render(**highlighted_resume)
