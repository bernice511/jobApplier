"""Parses the master resume PDF into the structured JSON schema used by resume_template.py.

Extraction uses pdfplumber with a tight x_tolerance, which matters: this resume's PDF
font encoding drops space characters at the default tolerance (e.g. "Built and maintained"
becomes "Builtandmaintained"), so x_tolerance must stay low (~1.5) or words run together.

Structuring raw text into sections/entries is delegated to Claude, since resumes vary in
section order/naming and hand-written regex parsing breaks the moment the source resume
changes even slightly.

Calls Claude via the `claude` CLI (jobapplier.claude_cli), not the Anthropic API - see that
module's docstring for why.
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber

from jobapplier.common.claude_cli import call_claude_json
from jobapplier.common.config import (
    MASTER_RESUME_JSON,
    MASTER_RESUME_PDF,
    MASTER_RESUME_PHOTO,
    MASTER_RESUME_STYLE_JSON,
)

SCHEMA_INSTRUCTIONS = """
Do not use any tools (no file reads/writes, no bash, no web access) - just respond with the
JSON described below, nothing else.

Convert the resume text below into this exact JSON schema (no extra commentary, valid JSON only):

{
  "name": str,
  "contact": [str, ...],
  "sections": [
    // one of three shapes, in the same order they appear in the resume:

    {"title": str, "type": "paragraph", "content": str},

    {"title": str, "type": "skills", "categories": [{"name": str, "items": [str, ...]}]},

    {"title": str, "type": "entries", "entries": [
      {
        "header_left_bold": str,          // e.g. company/institution/award/project name
        "header_left_normal": str | null, // text after a "|" on the same line, if present (e.g. award description, or project's school)
        "header_right": str,              // location or year, right-aligned in the original
        "two_line": bool,                 // true if there's a second line (role/degree + dates), like Education/Experience entries
        "sub_left": str | null,           // role or degree text (only if two_line)
        "sub_right": str | null,          // dates (only if two_line)
        "bullets": [str, ...],            // bullet points directly under this entry (before any subentries)
        "subentries": [                   // nested sub-roles within one employer, e.g. distinct projects at the same company
          {"header_left_bold": str, "header_right": str, "bullets": [str, ...]}
        ]
      }
    ]}
  ]
}

Rules:
- Preserve the original wording of every bullet and heading exactly - do not rephrase, summarize, or fix grammar.
- "contact" should be the individual pipe-separated items from the contact line (phone, email, linkedin, github, etc), in order.
- Every section from the resume must be included, in its original order, using whichever of the three "type" shapes fits it.
- Use "subentries": [] (empty list) when an entry has no nested sub-roles.
- Use null (not empty string) for header_left_normal, sub_left, sub_right when not applicable.
- Output ONLY the JSON object, nothing else.

Resume text:
---
{resume_text}
---
"""


_CID_PLACEHOLDER_RE = re.compile(r"\(cid:\d+\)")


def extract_text(pdf_path: Path = MASTER_RESUME_PDF) -> str:
    """pdfplumber falls back to a literal "(cid:NNN)" placeholder for glyphs it can't decode
    to Unicode - typically icon-font glyphs (e.g. a FontAwesome phone/LinkedIn icon in the
    contact line) with no ToUnicode mapping in the embedded font. These are never meaningful
    content, so strip them here rather than let Claude see - and dutifully preserve verbatim,
    per its own instructions - raw PDF-internal glyph IDs."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1.5) or ""
            text = _CID_PLACEHOLDER_RE.sub("", text)
            text = re.sub(r"[ \t]{2,}", " ", text)
            pages.append(text)
    return "\n".join(pages)


def structure_resume(resume_text: str) -> dict:
    prompt = SCHEMA_INSTRUCTIONS.replace("{resume_text}", resume_text)
    return call_claude_json(prompt)


# Visual-style fingerprint of the uploaded PDF, so resume_template.py can render tailored
# resumes in something that actually looks like the resume the candidate uploaded, instead of
# always using one hardcoded design regardless of input. This is a best-effort heuristic read
# of pdfplumber's per-character font/size/color/position data, not a pixel-perfect clone -
# resume_template.py falls back to these DEFAULT_STYLE values (which match the tool's
# original hardcoded look) for anything a given PDF doesn't let us confidently infer.
DEFAULT_STYLE = {
    "body_font_family": "Georgia, 'Times New Roman', Times, serif",
    "body_font_size_pt": 9.3,
    "name_font_size_pt": 16.5,
    "section_title_font_size_pt": 10.0,
    "section_title_bold": True,
    "section_title_uppercase": True,
    "section_title_underline": True,
    "text_color": "#111111",
    "name_color": "#111111",
    "contact_color": "#111111",
    "section_title_color": "#111111",
    "line_height": 1.08,
    "margin_top_in": 0.3,
    "margin_bottom_in": 0.3,
    "margin_left_in": 0.5,
    "margin_right_in": 0.5,
    "two_column_detected": False,
    "has_photo": False,
    "photo_side": "right",
    "photo_width_pt": 60.0,
    "photo_height_pt": 75.0,
}

# PDF font names are often subsetted (e.g. "ABCDEE+Calibri-Bold") and won't be installed as-is
# on the machine running WeasyPrint - map known families to a CSS-safe stack with a same-vibe
# fallback, rather than passing the raw PDF font name straight into CSS.
_FONT_KEYWORDS = {
    "times": "Georgia, 'Times New Roman', Times, serif",
    "georgia": "Georgia, 'Times New Roman', Times, serif",
    "cambria": "Cambria, Georgia, serif",
    "garamond": "Garamond, Georgia, serif",
    "minion": "Garamond, Georgia, serif",
    "calibri": "Calibri, Candara, Arial, sans-serif",
    "arial": "Arial, Helvetica, sans-serif",
    "helvetica": "Helvetica, Arial, sans-serif",
    "verdana": "Verdana, Geneva, sans-serif",
    "tahoma": "Tahoma, Verdana, sans-serif",
    "segoe": "'Segoe UI', Arial, sans-serif",
    "lato": "Lato, Arial, sans-serif",
    "roboto": "Roboto, Arial, sans-serif",
    "opensans": "'Open Sans', Arial, sans-serif",
}


def _css_font_family(pdf_fontname: str) -> str:
    base = pdf_fontname.split("+")[-1].lower()  # strip subset prefix, e.g. "ABCDEE+Calibri"
    for keyword, css_stack in _FONT_KEYWORDS.items():
        if keyword in base:
            return css_stack
    if any(hint in base for hint in ("serif", "roman", "book", "palatino")):
        return "Georgia, 'Times New Roman', Times, serif"
    return "Arial, Helvetica, sans-serif"


def _rgb_to_hex(color) -> str | None:
    if isinstance(color, (int, float)):
        v = round(color * 255)
        return f"#{v:02x}{v:02x}{v:02x}"
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        r, g, b = (round(c * 255) for c in color[:3])
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _majority_hex(chars_subset, fallback: str) -> str:
    counts = Counter(c.get("non_stroking_color") for c in chars_subset if c.get("non_stroking_color") is not None)
    if not counts:
        return fallback
    return _rgb_to_hex(counts.most_common(1)[0][0]) or fallback


def _detect_two_column(page) -> bool:
    """A resume with a right-aligned date next to a left-aligned title (the common case) has
    both sides on the SAME row - a genuine two-column/sidebar layout instead has most rows
    with content on only ONE side, with the other column's content living on different rows
    entirely. That's the signal used here, rather than just checking whether text appears on
    both halves of the page anywhere."""
    words = page.extract_words()
    if len(words) < 20:
        return False
    mid = page.width / 2
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w["top"] / 3), []).append(w)

    single_side = shared = 0
    for row_words in rows.values():
        has_left = any(w["x0"] < mid for w in row_words)
        has_right = any(w["x1"] > mid for w in row_words)
        if has_left and has_right:
            shared += 1
        elif has_left or has_right:
            single_side += 1

    total = single_side + shared
    return total >= 10 and (single_side / total) > 0.7


def extract_style(pdf_path: Path = MASTER_RESUME_PDF) -> dict:
    style = dict(DEFAULT_STYLE)
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return style
        page = pdf.pages[0]
        chars = page.chars
        if not chars:
            return style

        # (fontname, size rounded to the nearest 0.5pt) -> count, to separate the dominant
        # body style from the standout styles used for the name/section titles.
        style_key = lambda c: (c["fontname"], round(c["size"] * 2) / 2)
        style_counts = Counter(style_key(c) for c in chars)
        body_style, _ = style_counts.most_common(1)[0]
        body_font, body_size = body_style
        style["body_font_family"] = _css_font_family(body_font)
        style["body_font_size_pt"] = body_size

        body_chars = [c for c in chars if style_key(c) == body_style]
        body_color = _rgb_to_hex(body_chars[0].get("non_stroking_color"))
        if body_color:
            style["text_color"] = body_color

        # Largest text in the top ~15% of the page (name/header), if bigger than body text.
        # Many templates colorize specific elements (name, contact line, section titles)
        # distinctly from body text rather than using one flat color throughout - detect each
        # independently by its own POSITION/style rather than assuming they all match body's
        # color, since PDF content-stream order doesn't reliably match visual reading order.
        header_cutoff = page.height * 0.15
        header_chars = [c for c in chars if c["top"] <= header_cutoff]
        header_sizes = [round(c["size"] * 2) / 2 for c in header_chars]
        if header_sizes:
            name_size = max(header_sizes)
            if name_size > body_size:
                style["name_font_size_pt"] = name_size
                name_chars = [c for c in header_chars if round(c["size"] * 2) / 2 == name_size]
                style["name_color"] = _majority_hex(name_chars, style["text_color"])
                contact_chars = [c for c in header_chars if round(c["size"] * 2) / 2 != name_size]
                style["contact_color"] = _majority_hex(contact_chars, style["text_color"])

        # Section-title underline rules, detected BEFORE the candidate search below so their
        # y-positions can disambiguate section-title text from other body text that happens
        # to share the same (font, size) style bucket (see below).
        rule_tops = [ln["top"] for ln in page.lines if abs(ln.get("top", 0) - ln.get("bottom", 0)) < 0.5]
        rule_tops += [rc["top"] for rc in page.rects if rc["height"] < 1.5 and rc["width"] > 50]
        style["section_title_underline"] = len(rule_tops) >= 2

        # Section-title candidate: a distinct style at-or-above body size (many templates,
        # especially LaTeX ones, bold the section title at the SAME size as body text rather
        # than enlarging it) but below name size, that recurs several times (once per
        # section) - prefer a bold-looking font name among those.
        candidates = [
            key for key, count in style_counts.items()
            if key != body_style and body_size <= key[1] < style["name_font_size_pt"] and count >= 2
        ]
        bold_hints = ("bold", "-bd", "black", "heavy")
        bold_candidates = [c for c in candidates if any(h in c[0].lower() for h in bold_hints)]
        chosen = bold_candidates or candidates
        if chosen:
            title_style = max(chosen, key=lambda c: c[1])
            style["section_title_font_size_pt"] = title_style[1]
            style["section_title_bold"] = any(h in title_style[0].lower() for h in bold_hints)
            title_chars = [c for c in chars if style_key(c) == title_style]

            # This style bucket often ALSO covers unrelated bold body text (e.g. job titles,
            # company names) that happens to share the same font/size - which would otherwise
            # swamp a plain majority vote on case/color. Underlined rules sit directly below
            # the actual section title, so chars just above a rule's y-position are the real
            # section titles; fall back to the whole bucket if there are no rules to anchor on.
            near_rule = [c for c in title_chars if any(0 <= rt - c["bottom"] < 12 for rt in rule_tops)]
            title_chars = near_rule or title_chars

            title_text = "".join(c["text"] for c in title_chars)
            if any(ch.isalpha() for ch in title_text):
                style["section_title_uppercase"] = not any(ch.islower() for ch in title_text)
            style["section_title_color"] = _majority_hex(title_chars, style["text_color"])

        style["two_column_detected"] = _detect_two_column(page)

        x0s = [c["x0"] for c in chars]
        x1s = [c["x1"] for c in chars]
        tops = [c["top"] for c in chars]
        bottoms = [c["bottom"] for c in chars]
        style["margin_left_in"] = round(min(x0s) / 72, 2)
        style["margin_right_in"] = round((page.width - max(x1s)) / 72, 2)
        style["margin_top_in"] = round(min(tops) / 72, 2)
        style["margin_bottom_in"] = round((page.height - max(bottoms)) / 72, 2)

    return style


def extract_photo(pdf_path: Path = MASTER_RESUME_PDF) -> dict | None:
    """Best-effort extraction of an embedded headshot (common on many non-US resume formats,
    entirely absent from the tailored output before this - the old pipeline only ever read
    text). Picks the LARGEST embedded image on the first page, on the assumption a resume
    embeds at most one real photo and anything smaller is a decorative icon/logo. Renders the
    image's bounding box via pdfplumber's page rasterizer rather than pulling the raw PDF
    image stream directly, since that sidesteps having to handle whatever compression filter
    the embedded image happens to use. Returns None if the page has no images, or if
    rendering fails for any reason."""
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages or not pdf.pages[0].images:
            return None
        page = pdf.pages[0]
        largest = max(page.images, key=lambda im: im["width"] * im["height"])
        bbox = (largest["x0"], largest["top"], largest["x1"], largest["bottom"])
        try:
            pil_image = page.crop(bbox).to_image(resolution=200).original
        except Exception:
            return None

        buffer = io.BytesIO()
        pil_image.convert("RGB").save(buffer, format="PNG")
        photo_center_x = (largest["x0"] + largest["x1"]) / 2
        return {
            "png_bytes": buffer.getvalue(),
            "side": "left" if photo_center_x < page.width / 2 else "right",
            "width_pt": round(largest["width"], 1),
            "height_pt": round(largest["height"], 1),
        }


def parse_and_cache(pdf_path: Path = MASTER_RESUME_PDF, json_path: Path = MASTER_RESUME_JSON,
                     style_json_path: Path = MASTER_RESUME_STYLE_JSON,
                     photo_path: Path = MASTER_RESUME_PHOTO, force: bool = False) -> dict:
    # Style/photo extraction is cheap/local (pdfplumber only) - refresh it independently of
    # the structured-content cache below so an existing cache from before this support was
    # added doesn't force a redundant Claude re-structuring call just to backfill it.
    if not style_json_path.exists() or force:
        style_json_path.parent.mkdir(parents=True, exist_ok=True)
        style = extract_style(pdf_path)
        photo = extract_photo(pdf_path)
        if photo:
            style["has_photo"] = True
            style["photo_side"] = photo["side"]
            style["photo_width_pt"] = photo["width_pt"]
            style["photo_height_pt"] = photo["height_pt"]
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            photo_path.write_bytes(photo["png_bytes"])
        else:
            style["has_photo"] = False
            photo_path.unlink(missing_ok=True)  # don't leave a stale photo from a prior upload
        style_json_path.write_text(json.dumps(style, indent=2))

    if json_path.exists() and not force:
        return json.loads(json_path.read_text())

    text = extract_text(pdf_path)
    structured = structure_resume(text)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(structured, indent=2))
    return structured


if __name__ == "__main__":
    data = parse_and_cache(force=True)
    print(f"Parsed resume -> {MASTER_RESUME_JSON}")
