"""Log of JD-paste tailoring events (see tailoring_service.py / webapp.py). Kept separate
from tracker.py's applications.csv, since these are resumes/cover letters generated from a
pasted job description, not necessarily submitted LinkedIn applications.

Also tracks pipeline stage (see STATUSES/status) - added after the fact, so
_migrate_if_needed() rewrites any pre-existing CSV (which predates these columns) to the
current header before reading/appending, keeping every row's columns aligned with FIELDNAMES
rather than silently misaligning DictReader's column mapping. "applied"/"date_applied" predate
"status" (back when this was just a binary checkbox) and are kept in sync with it rather than
removed, since date_applied specifically ("when did I apply") isn't recoverable from status
alone once the pipeline moves on to interview/offer/rejected.
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
    "applied", "date_applied", "status", "tags", "source",
]

# Order matters - this is the left-to-right column order of the Kanban board.
STATUSES = ["saved", "applied", "interview", "offer", "rejected"]


def _record_key(record: dict) -> str:
    """No dedicated id column - the generated PDF's filename (resume or cover letter, minus
    extension) is already unique per tailoring event (see tailoring_service.py's `slug`), so
    it doubles as a stable key for toggling applied-status without adding a new column."""
    path = record.get("resume_path") or record.get("cover_letter_path") or ""
    return Path(path).stem


def _parse_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _format_tags(tags: list[str]) -> str:
    # Commas are the field's own separator - stripped from individual tags rather than
    # building CSV-style quoting/escaping for what's meant to be short single-word-ish labels.
    return ",".join(t.strip().replace(",", "") for t in tags if t.strip())


def _load_raw_rows() -> list[dict]:
    _migrate_if_needed()
    if not TAILORING_LOG_CSV.exists():
        return []
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _rewrite_rows(rows: list[dict]) -> None:
    with open(TAILORING_LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def _migrate_if_needed() -> None:
    if not TAILORING_LOG_CSV.exists():
        return
    with open(TAILORING_LOG_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == FIELDNAMES:
            return
        rows = list(reader)
    for row in rows:
        # Rows from before "status" existed only have the old applied/date_applied checkbox -
        # backfill a matching stage rather than leaving every pre-existing row stuck at blank
        # (which would silently vanish from every Kanban column, not just default to "saved").
        if not row.get("status"):
            row["status"] = "applied" if row.get("applied") == "True" else "saved"
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
    if not row["status"]:
        row["status"] = "saved"
    if not row["applied"]:
        row["applied"] = "True" if row["status"] != "saved" else "False"

    with open(TAILORING_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_records() -> list[dict]:
    rows = _load_raw_rows()
    for row in rows:
        row["record_key"] = _record_key(row)
        row["applied"] = row.get("applied") == "True"
        row["status"] = row.get("status") or "saved"
        row["tags"] = _parse_tags(row.get("tags", ""))
        row["source"] = row.get("source") or "Unknown"
    return rows


def set_status(record_key: str, status: str) -> bool:
    """Rewrites the whole file (no in-place row update for CSVs) - fine for a small personal
    log. Returns True if record_key matched a row, False if it didn't or status is invalid.

    Keeps the legacy applied/date_applied columns in sync (applied = status != "saved") so
    anything still reading those directly doesn't regress - date_applied specifically is set
    once, the first time a record leaves "saved", and left alone on every later transition
    (interview/offer/rejected) since it records when you applied, not when you last moved the
    card."""
    if status not in STATUSES:
        return False
    rows = _load_raw_rows()
    if not rows:
        return False

    found = False
    for row in rows:
        if _record_key(row) == record_key:
            row["status"] = status
            row["applied"] = "True" if status != "saved" else "False"
            if status != "saved" and not row.get("date_applied"):
                row["date_applied"] = date.today().isoformat()
            elif status == "saved":
                row["date_applied"] = ""
            found = True

    if found:
        _rewrite_rows(rows)
    return found


def set_tags(record_key: str, tags: list[str]) -> bool:
    """Replaces a record's full tag set (not an add/remove delta) - the caller (the Kanban
    board's tag editor) always has the complete current list already, so there's no
    concurrent-editor race to reconcile here."""
    rows = _load_raw_rows()
    if not rows:
        return False

    found = False
    for row in rows:
        if _record_key(row) == record_key:
            row["tags"] = _format_tags(tags)
            found = True

    if found:
        _rewrite_rows(rows)
    return found
