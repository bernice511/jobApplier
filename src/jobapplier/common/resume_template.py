"""Renders structured resume/cover-letter JSON into ATS-safe PDFs via Jinja2 + WeasyPrint."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from jobapplier.common.config import MASTER_RESUME_PHOTO, MASTER_RESUME_STYLE_JSON
from jobapplier.common.resume_parser import DEFAULT_STYLE

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "jinja"]),
)


def _load_style(style_json_path: Path, photo_path: Path) -> dict:
    """The style fingerprint captured from whatever resume the candidate uploaded (see
    resume_parser.extract_style/extract_photo) - falls back to DEFAULT_STYLE (the tool's
    original hardcoded look, no photo) if no style file exists yet, or if it's missing keys
    added after it was written. The photo, if any, is inlined as a data URI rather than a file
    path so the generated HTML has no dependency on where output_path ends up relative to
    photo_path."""
    style = dict(DEFAULT_STYLE)
    if style_json_path.exists():
        style.update(json.loads(style_json_path.read_text()))
    style["photo_data_uri"] = None
    if style.get("has_photo") and photo_path.exists():
        encoded = base64.b64encode(photo_path.read_bytes()).decode("ascii")
        style["photo_data_uri"] = f"data:image/png;base64,{encoded}"
    return style


def render_resume(
    resume_data: dict, output_path: Path,
    style_json_path: Path = MASTER_RESUME_STYLE_JSON, photo_path: Path = MASTER_RESUME_PHOTO,
) -> Path:
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
    html_str = template.render(**resume_data, style=_load_style(style_json_path, photo_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(str(output_path))
    return output_path


def render_cover_letter(
    cover_letter_data: dict, output_path: Path,
    style_json_path: Path = MASTER_RESUME_STYLE_JSON, photo_path: Path = MASTER_RESUME_PHOTO,
) -> Path:
    """cover_letter_data schema:
    {
      "name": str, "contact": [str, ...], "date": str,
      "greeting": str, "body_paragraphs": [str, ...], "signoff": str
    }
    """
    template = _env.get_template("cover_letter.html.jinja")
    html_str = template.render(**cover_letter_data, style=_load_style(style_json_path, photo_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(str(output_path))
    return output_path
