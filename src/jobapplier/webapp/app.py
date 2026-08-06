"""Local web UI: paste a JD to get a tailored resume + cover letter, and a simple chat box
to ask things like "which resume did I use for Netflix" against the tailoring log.

Run with: PYTHONPATH=src DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -m jobapplier.webapp.app
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from jobapplier.common import resume_parser, resume_store, screening_answers
from jobapplier.common.config import GENERATED_DIR, RESUME_DIR, load_config
from jobapplier.job_alerts import store as job_alerts_store
from jobapplier.webapp import tailoring_log, tailoring_service

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB - generous for a resume PDF


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


@app.get("/resume")
def resume_page():
    return render_template("resume.html.jinja", active_page="resume")


@app.get("/profile")
def profile_page():
    return render_template("profile.html.jinja", active_page="profile")


@app.get("/alerts")
def alerts_page():
    return render_template("alerts.html.jinja", active_page="alerts")


@app.get("/api/alerts")
def api_alerts():
    threshold = load_config().alert_match_threshold
    records = [
        r for r in job_alerts_store.load_all()
        if not r.get("dismissed") and r.get("match_score", 0) >= threshold
    ]
    records.sort(key=lambda r: (r.get("found_date", ""), r.get("match_score", 0)), reverse=True)
    return jsonify({"alerts": records})


@app.post("/api/alerts/dismiss")
def api_alerts_dismiss():
    job_id = (request.get_json(silent=True) or {}).get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required."}), 400
    if not job_alerts_store.set_dismissed(job_id, True):
        return jsonify({"error": "Unknown job_id."}), 404
    return jsonify({"ok": True})


VALID_GENERATE_OPTIONS = {"both", "resume", "cover_letter"}


@app.get("/api/resumes")
def api_resumes_list():
    return jsonify({"resumes": resume_store.list_resumes()})


@app.post("/api/resumes")
def api_resumes_add():
    file = request.files.get("resume")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400
    display_name = request.form.get("display_name", "").strip()

    try:
        entry = resume_store.add_resume(display_name, file.read())
    except Exception as exc:
        return jsonify({"error": f"Resume saved, but parsing failed: {exc}"}), 500
    return jsonify(entry)


@app.post("/api/resumes/<resume_id>/activate")
def api_resumes_activate(resume_id):
    if not resume_store.set_active(resume_id):
        return jsonify({"error": "Unknown resume."}), 404
    return jsonify({"ok": True})


@app.delete("/api/resumes/<resume_id>")
def api_resumes_delete(resume_id):
    active = resume_store.get_active()
    if active and active["id"] == resume_id:
        return jsonify({"error": "Can't delete the active resume - activate another one first."}), 400
    if not resume_store.delete_resume(resume_id):
        return jsonify({"error": "Unknown resume."}), 404
    return jsonify({"ok": True})


@app.get("/resume-files/<resume_id>")
def resume_file(resume_id):
    path = resume_store.paths_for(resume_id)["pdf_path"].resolve()
    if RESUME_DIR.resolve() not in path.parents or not path.exists():
        return "Not found", 404
    return send_file(path)


def _build_profile() -> dict | None:
    """Combines the active resume's name/contact with screening_answers.yaml's structured
    fields and patterns map into one flat dict - shared by the extension's autofill feature
    (GET /api/autofill-profile) and the webapp's profile-editing page (GET /api/profile), so
    both always agree on what "the current profile" actually is. Returns None if there's no
    active resume yet.

    first_name/last_name come from screening_answers.yaml if the user set them there (many ATS
    forms split name into two fields, and some multi-word names split incorrectly if left to
    the naive fallback below) - otherwise falls back to a naive split of the resume's single
    "name" string. Showing that fallback value (rather than leaving it blank) in the profile
    page lets the user see exactly what's being guessed today and correct it in one edit."""
    active = resume_store.get_active()
    if active is None:
        return None

    resume = resume_parser.parse_and_cache(**resume_store.get_active_paths())
    answers = screening_answers.load_screening_answers()
    name = resume.get("name") or ""
    name_parts = name.split(" ", 1)

    return {
        "resume_id": active["id"],
        "name": name,
        "first_name": answers.get("first_name") or (name_parts[0] if name_parts else ""),
        "last_name": answers.get("last_name") or (name_parts[1] if len(name_parts) > 1 else ""),
        "contact": resume.get("contact", []),
        "phone": answers.get("phone", ""),
        "email": answers.get("email", ""),
        "work_authorization": answers.get("work_authorization", ""),
        "requires_sponsorship": answers.get("requires_sponsorship", ""),
        "notice_period_days": answers.get("notice_period_days", ""),
        "salary_expectation": answers.get("salary_expectation", ""),
        "years_experience_default": answers.get("years_experience_default", ""),
        "linkedin_url": answers.get("linkedin_url", ""),
        "github_url": answers.get("github_url", ""),
        "website_url": answers.get("website_url", ""),
        "patterns": answers.get("patterns", {}),
    }


@app.get("/api/autofill-profile")
def api_autofill_profile():
    """A content script can't read local files directly, so this is the only way profile data
    reaches the browser extension's autofill feature. Includes "resume_id" so the extension
    can attach the active resume's raw PDF (via GET /resume-files/<resume_id>) when autofilling
    WITHOUT having generated a JD-tailored resume this session - autofill is available as soon
    as a job is detected, not gated on running Analyze/Generate first."""
    profile = _build_profile()
    if profile is None:
        return jsonify({"error": "No active resume set - upload a resume on the Resume page first."}), 400
    return jsonify(profile)


@app.get("/api/profile")
def api_profile_get():
    profile = _build_profile()
    if profile is None:
        return jsonify({"error": "No active resume set - upload a resume on the Resume page first."}), 400
    return jsonify(profile)


@app.post("/api/profile")
def api_profile_update():
    fields = request.get_json(silent=True) or {}
    screening_answers.update_answers(fields)
    return jsonify(_build_profile() or {"ok": True})


@app.post("/api/screening-answers/patterns")
def api_screening_answers_add_pattern():
    body = request.get_json(silent=True) or {}
    question = body.get("question", "").strip()
    answer = body.get("answer", "").strip()
    if not question or not answer:
        return jsonify({"error": "question and answer are both required."}), 400
    screening_answers.add_pattern(question, answer)
    return jsonify({"ok": True})


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


@app.get("/api/applications")
def api_applications():
    records = [_with_filenames(r) for r in tailoring_log.load_records()]
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return jsonify({"applications": records})


@app.post("/api/applications/mark_applied")
def api_applications_mark_applied():
    body = request.get_json(silent=True) or {}
    record_key = body.get("record_key", "").strip()
    applied = bool(body.get("applied"))
    if not record_key:
        return jsonify({"error": "record_key is required."}), 400
    if not tailoring_log.set_applied(record_key, applied):
        return jsonify({"error": "Unknown record_key."}), 404
    return jsonify({"ok": True})


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
    app.run(debug=True, port=5050)


if __name__ == "__main__":
    main()
