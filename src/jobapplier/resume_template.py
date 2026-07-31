"""Renders structured resume/cover-letter JSON into ATS-safe PDFs via Jinja2 + WeasyPrint."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "jinja"]),
)


def render_resume(resume_data: dict, output_path: Path) -> Path:
    """resume_data schema:
    {
      "name": str,
      "contact": [str, ...],
      "sections": [
        {"title": str, "type": "paragraph", "content": str},
        {"title": str, "type": "skills", "categories": [{"name": str, "items": [str, ...]}]},
        {"title": str, "type": "entries", "entries": [
          {
            "header_left_bold": str, "header_left_normal": str | None,
            "header_right": str,
            "two_line": bool, "sub_left": str | None, "sub_right": str | None,
            "bullets": [str, ...],
            "subentries": [{"header_left_bold": str, "header_right": str, "bullets": [str, ...]}]
          }
        ]}
      ]
    }
    """
    template = _env.get_template("resume.html.jinja")
    html_str = template.render(**resume_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(str(output_path))
    return output_path


def render_cover_letter(cover_letter_data: dict, output_path: Path) -> Path:
    """cover_letter_data schema:
    {
      "name": str, "contact": [str, ...], "date": str,
      "greeting": str, "body_paragraphs": [str, ...], "signoff": str
    }
    """
    template = _env.get_template("cover_letter.html.jinja")
    html_str = template.render(**cover_letter_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(str(output_path))
    return output_path
