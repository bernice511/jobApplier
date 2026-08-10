"""Cache for tailor_from_jd() results - the generated resume/cover letter itself, not just the
analyze score. Re-generating for a job with the exact same inputs (JD text, resume, generate
mode, approved keywords, notes, and the keyword counts that feed the deterministic match-score
formula) reuses the existing PDFs and match score instead of re-invoking Claude and re-rendering.
Any real change to those inputs - different approved keywords, added notes, a resume re-upload -
produces a fresh cache miss and a real regeneration, so "Regenerate with feedback" (which always
changes notes) still calls Claude every time, which is the whole point of that button.

Same file-based JSON approach as analyze_cache.py, and deliberately not merged with it - that
cache stores a score, this one stores rendered files on disk, and a cache hit here additionally
has to confirm those files still exist before trusting the cached paths."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from jobapplier.common.config import DATA_DIR

TAILOR_CACHE_JSON = DATA_DIR / "tailor_cache.json"

# Caps the file's growth for a long-running server - old entries are evicted oldest-cached-first
# once this is exceeded, same policy as analyze_cache.py.
MAX_ENTRIES = 200


def _cache_key(jd_text: str, master_resume: dict, params: dict) -> str:
    jd_fingerprint = hashlib.sha256(jd_text.encode()).hexdigest()
    resume_fingerprint = hashlib.sha256(
        json.dumps(master_resume, sort_keys=True).encode()
    ).hexdigest()[:16]
    params_fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{jd_fingerprint}:{resume_fingerprint}:{params_fingerprint}"


def _load() -> dict:
    if not TAILOR_CACHE_JSON.exists():
        return {}
    try:
        return json.loads(TAILOR_CACHE_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(cache: dict) -> None:
    if len(cache) > MAX_ENTRIES:
        oldest_first = sorted(cache.items(), key=lambda kv: kv[1].get("cached_at", ""))
        cache = dict(oldest_first[-MAX_ENTRIES:])
    TAILOR_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    TAILOR_CACHE_JSON.write_text(json.dumps(cache, indent=2))


def get(jd_text: str, master_resume: dict, params: dict) -> dict | None:
    entry = _load().get(_cache_key(jd_text, master_resume, params))
    if not entry:
        return None
    result = entry["result"]
    # Nothing deletes files from GENERATED_DIR today, but trusting a stale path over just
    # re-generating would be worse than treating a missing file as a miss.
    for path_key in ("resume_path", "cover_letter_path"):
        path = result.get(path_key)
        if path and not Path(path).exists():
            return None
    return result


def set(jd_text: str, master_resume: dict, params: dict, result: dict) -> None:
    cache = _load()
    cache[_cache_key(jd_text, master_resume, params)] = {
        "result": result,
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(cache)
