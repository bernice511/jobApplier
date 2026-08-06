"""JSON-backed store of every job the daily alert pipeline has ever evaluated, keyed by
job_id (Adzuna's own listing id). Every evaluated job is recorded - including ones that scored
below the alert threshold - so main.py never re-fetches/re-scores a job it's already seen,
even if that job doesn't clear the bar for the dashboard.

File-based (JSON), matching webapp/analyze_cache.py and linkedin_apply/tracker.py's approach
elsewhere in this package rather than pulling in a real database for a single-user local tool.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from jobapplier.common.config import JOB_ALERTS_JSON

# Jobs found longer ago than this are pruned on save - old postings are almost certainly
# expired/filled, and this bounds the store's growth for a long-running daily job.
MAX_AGE_DAYS = 30


def _load() -> dict[str, dict]:
    if not JOB_ALERTS_JSON.exists():
        return {}
    try:
        return json.loads(JOB_ALERTS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(records: dict[str, dict]) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).date().isoformat()
    pruned = {
        job_id: record for job_id, record in records.items()
        if record.get("found_date", "") >= cutoff
    }
    JOB_ALERTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    JOB_ALERTS_JSON.write_text(json.dumps(pruned, indent=2))


def seen_ids() -> set[str]:
    return set(_load().keys())


def load_all() -> list[dict]:
    return list(_load().values())


def add_or_update(record: dict) -> None:
    """record must include job_id; found_date defaults to today if not set."""
    records = _load()
    record = dict(record)
    record.setdefault("found_date", date.today().isoformat())
    record.setdefault("dismissed", False)
    records[record["job_id"]] = record
    _save(records)


def set_dismissed(job_id: str, dismissed: bool) -> bool:
    """Returns True if job_id was found and updated, False otherwise."""
    records = _load()
    if job_id not in records:
        return False
    records[job_id]["dismissed"] = dismissed
    _save(records)
    return True
