"""Log of JD-paste tailoring events (see tailoring_service.py / webapp.py). Kept separate
from tracker.py's applications.csv, since these are resumes/cover letters generated from a
pasted job description, not necessarily submitted LinkedIn applications.

Also tracks whether the candidate actually applied (applied/date_applied) - added after the
fact, so _migrate_if_needed() rewrites any pre-existing CSV (which predates these columns) to
the current header before reading/appending, keeping every row's columns aligned with
FIELDNAMES rather than silently misaligning DictReader's column mapping.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from jobapplier.common.config import DATA_DIR

TAILORING_LOG_CSV = DATA_DIR / "tailoring_log.csv"

FIELDNAMES = [
    "timestamp", "company", "title", "location",
    "resume_path", "cover_letter_path", "match_score",
    "applied", "date_applied",
]


def _record_key(record: dict) -> str:
    """No dedicated id column - the generated PDF's filename (resume or cover letter, minus
    extension) is already unique per tailoring event (see tailoring_service.py's `slug`), so
    it doubles as a stable key for toggling applied-status without adding a new column."""
    path = record.get("resume_path") or record.get("cover_letter_path") or ""
    return Path(path).stem


def _migrate_if_needed() -> None:
    if not TAILORING_LOG_CSV.exists():
        return
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == FIELDNAMES:
            return
        rows = list(reader)
    with open(TAILORING_LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def append_record(record: dict) -> None:
    _migrate_if_needed()
    TAILORING_LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = TAILORING_LOG_CSV.exists()

    row = {field: record.get(field, "") for field in FIELDNAMES}
    if not row["timestamp"]:
        row["timestamp"] = datetime.now().isoformat(timespec="seconds")
    if not row["applied"]:
        row["applied"] = "False"

    with open(TAILORING_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_records() -> list[dict]:
    _migrate_if_needed()
    if not TAILORING_LOG_CSV.exists():
        return []
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["record_key"] = _record_key(row)
        row["applied"] = row.get("applied") == "True"
    return rows


def set_applied(record_key: str, applied: bool) -> bool:
    """Rewrites the whole file (no in-place row update for CSVs) - fine for a small personal
    log. Returns True if record_key matched a row, False otherwise."""
    _migrate_if_needed()
    if not TAILORING_LOG_CSV.exists():
        return False
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    found = False
    for row in rows:
        if _record_key(row) == record_key:
            row["applied"] = "True" if applied else "False"
            row["date_applied"] = date.today().isoformat() if applied else ""
            found = True

    if found:
        with open(TAILORING_LOG_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
    return found
