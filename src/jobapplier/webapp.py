"""Local web UI: paste a JD to get a tailored resume + cover letter, and a simple chat box
to ask things like "which resume did I use for Netflix" against the tailoring log.

Run with: PYTHONPATH=src DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -m jobapplier.webapp
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from jobapplier import tailoring_service
from jobapplier.config import GENERATED_DIR

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("webapp_index.html")


@app.post("/api/tailor")
def api_tailor():
    jd_text = (request.get_json(silent=True) or {}).get("jd_text", "").strip()
    if not jd_text:
        return jsonify({"error": "Paste a job description first."}), 400
    try:
        record = tailoring_service.tailor_from_jd(jd_text)
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
        "resume_filename": Path(record["resume_path"]).name,
        "cover_letter_filename": Path(record["cover_letter_path"]).name,
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
