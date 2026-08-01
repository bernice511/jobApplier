"""Log of JD-paste tailoring events (see tailoring_service.py / webapp.py). Kept separate
from tracker.py's applications.csv, since these are resumes/cover letters generated from a
pasted job description, not necessarily submitted LinkedIn applications."""
from __future__ import annotations

import csv
from datetime import datetime

from jobapplier.common.config import DATA_DIR

TAILORING_LOG_CSV = DATA_DIR / "tailoring_log.csv"

FIELDNAMES = [
    "timestamp", "company", "title", "location",
    "resume_path", "cover_letter_path", "match_score",
]


def append_record(record: dict) -> None:
    TAILORING_LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = TAILORING_LOG_CSV.exists()

    row = {field: record.get(field, "") for field in FIELDNAMES}
    if not row["timestamp"]:
        row["timestamp"] = datetime.now().isoformat(timespec="seconds")

    with open(TAILORING_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_records() -> list[dict]:
    if not TAILORING_LOG_CSV.exists():
        return []
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
