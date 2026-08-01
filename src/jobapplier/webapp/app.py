"""Local web UI: paste a JD to get a tailored resume + cover letter, and a simple chat box
to ask things like "which resume did I use for Netflix" against the tailoring log.

Run with: PYTHONPATH=src DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -m jobapplier.webapp.app
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from jobapplier.common.config import GENERATED_DIR
from jobapplier.webapp import tailoring_service

app = Flask(__name__)


@app.before_request
def handle_preflight():
    """Lets the browser extension's side panel (a chrome-extension:// origin) call this
    local-only server. Bound to 127.0.0.1 by default (see main()), so a permissive CORS
    policy here only matters to other processes on the same machine."""
    if request.method == "OPTIONS":
        return "", 204


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.get("/")
def index():
    return render_template("tailor.html.jinja", active_page="tailor")


@app.get("/search")
def search_page():
    return render_template("search.html.jinja", active_page="search")


VALID_GENERATE_OPTIONS = {"both", "resume", "cover_letter"}


@app.post("/api/analyze")
def api_analyze():
    jd_text = (request.get_json(silent=True) or {}).get("jd_text", "").strip()
    if not jd_text:
        return jsonify({"error": "Paste a job description first."}), 400
    try:
        analysis = tailoring_service.analyze_jd(jd_text)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(analysis)


@app.post("/api/tailor")
def api_tailor():
    body = request.get_json(silent=True) or {}
    jd_text = body.get("jd_text", "").strip()
    company = body.get("company", "").strip()
    title = body.get("title", "").strip()
    location = body.get("location", "").strip()
    generate = body.get("generate", "both")
    approved_keywords = body.get("approved_keywords", [])
    notes = body.get("notes", "")
    matched_keyword_count = body.get("matched_keyword_count", 0)
    suggested_keyword_count = body.get("suggested_keyword_count", 0)
    core_requirement_count = body.get("core_requirement_count", 0)

    if not jd_text or not company or not title:
        return jsonify({"error": "Run analyze first - missing jd_text/company/title."}), 400
    if generate not in VALID_GENERATE_OPTIONS:
        return jsonify({"error": f"Invalid generate option: {generate}"}), 400

    try:
        record = tailoring_service.tailor_from_jd(
            jd_text, company, title, location,
            generate=generate, approved_keywords=approved_keywords, notes=notes,
            matched_keyword_count=matched_keyword_count,
            suggested_keyword_count=suggested_keyword_count,
            core_requirement_count=core_requirement_count,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(_with_filenames(record))


@app.post("/api/query")
def api_query():
    question = (request.get_json(silent=True) or {}).get("question", "").strip()
    matches = [_with_filenames(r) for r in tailoring_service.search_log(question)]
    return jsonify({"matches": matches})


def _with_filenames(record: dict) -> dict:
    return {
        **record,
        "resume_filename": Path(record["resume_path"]).name if record.get("resume_path") else None,
        "cover_letter_filename": (
            Path(record["cover_letter_path"]).name if record.get("cover_letter_path") else None
        ),
    }


@app.get("/files/<path:filename>")
def files(filename):
    path = (GENERATED_DIR / filename).resolve()
    if GENERATED_DIR.resolve() not in path.parents or not path.exists():
        return "Not found", 404
    return send_file(path)


def main() -> None:
    app.run(debug=False, port=5050)


if __name__ == "__main__":
    main()
