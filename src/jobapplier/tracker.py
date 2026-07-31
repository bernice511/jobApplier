"""Reads/writes the applications.csv tracking log. job_id is the dedupe key: once a job_id
appears in this file, main.py's search will never surface or reapply to it again."""
from __future__ import annotations

import csv
from datetime import date

from jobapplier.config import APPLICATIONS_CSV

FIELDNAMES = [
    "job_id", "title", "company", "location",
    "date_found", "date_applied", "status",
    "resume_path", "cover_letter_path", "job_url", "notes",
]


def load_applied_job_ids() -> set[str]:
    if not APPLICATIONS_CSV.exists():
        return set()
    with open(APPLICATIONS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["job_id"] for row in reader if row.get("job_id")}


def append_record(record: dict) -> None:
    APPLICATIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = APPLICATIONS_CSV.exists()

    row = {field: record.get(field, "") for field in FIELDNAMES}
    if not row["date_found"]:
        row["date_found"] = date.today().isoformat()

    with open(APPLICATIONS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
