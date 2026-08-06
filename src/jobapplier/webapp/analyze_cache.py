"""Cache for analyze_jd() results, so re-opening a job you already analyzed (or a spurious
re-extraction from content.js re-publishing the same job) is instant instead of re-running the
full extract+classify Claude pipeline. Keyed on the JD text AND the master resume's content,
so a resume re-upload naturally invalidates every prior entry without needing an explicit
"clear cache" step - stale entries just stop matching and sit unused.

File-based (JSON), matching tailoring_log.py's approach elsewhere in this package rather than
pulling in a real database for a single-user local tool."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from jobapplier.common.config import DATA_DIR

ANALYZE_CACHE_JSON = DATA_DIR / "analyze_cache.json"

# Caps the file's growth for a long-running server - old entries (from stale resume versions,
# or JDs never revisited) are evicted oldest-cached-first once this is exceeded.
MAX_ENTRIES = 200


def _cache_key(jd_text: str, master_resume: dict) -> str:
    jd_fingerprint = hashlib.sha256(jd_text.encode()).hexdigest()
    resume_fingerprint = hashlib.sha256(
        json.dumps(master_resume, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{jd_fingerprint}:{resume_fingerprint}"


def _load() -> dict:
    if not ANALYZE_CACHE_JSON.exists():
        return {}
    try:
        return json.loads(ANALYZE_CACHE_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(cache: dict) -> None:
    if len(cache) > MAX_ENTRIES:
        oldest_first = sorted(cache.items(), key=lambda kv: kv[1].get("cached_at", ""))
        cache = dict(oldest_first[-MAX_ENTRIES:])
    ANALYZE_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    ANALYZE_CACHE_JSON.write_text(json.dumps(cache, indent=2))


def get(jd_text: str, master_resume: dict) -> dict | None:
    entry = _load().get(_cache_key(jd_text, master_resume))
    return entry["result"] if entry else None


def set(jd_text: str, master_resume: dict, result: dict) -> None:
    cache = _load()
    cache[_cache_key(jd_text, master_resume)] = {
        "result": result,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)
