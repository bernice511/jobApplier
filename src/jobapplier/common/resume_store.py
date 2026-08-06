"""Supports multiple stored resumes with exactly one marked active at a time - the active
resume is what tailoring_service.py, linkedin_apply/main.py, and job_alerts/main.py all use
for parsing/matching/rendering. Each resume lives in its own subdirectory under RESUME_DIR
(RESUME_DIR/<id>/resume.pdf, resume.json, resume_style.json, resume_photo.png), tracked in a
small JSON registry (RESUME_DIR/resumes.json) - same file-based approach as the rest of this
package (webapp/analyze_cache.py, job_alerts/store.py), no real database for a single-user
local tool.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jobapplier.common import resume_parser
from jobapplier.common.config import RESUME_DIR

REGISTRY_JSON = RESUME_DIR / "resumes.json"

# Pre-multi-resume layout: one flat set of files directly under RESUME_DIR. Migrated into a
# "default" entry the first time the registry is read, so an existing user's already-uploaded
# resume isn't orphaned by this change.
_LEGACY_PDF = RESUME_DIR / "master_resume.pdf"
_LEGACY_JSON = RESUME_DIR / "master_resume.json"
_LEGACY_STYLE = RESUME_DIR / "master_resume_style.json"
_LEGACY_PHOTO = RESUME_DIR / "master_resume_photo.png"


class ResumeStoreError(RuntimeError):
    pass


def _paths(resume_id: str) -> dict[str, Path]:
    directory = RESUME_DIR / resume_id
    return {
        "pdf_path": directory / "resume.pdf",
        "json_path": directory / "resume.json",
        "style_json_path": directory / "resume_style.json",
        "photo_path": directory / "resume_photo.png",
    }


def _load_registry() -> list[dict]:
    if not REGISTRY_JSON.exists():
        return []
    try:
        return json.loads(REGISTRY_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_registry(entries: list[dict]) -> None:
    REGISTRY_JSON.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_JSON.write_text(json.dumps(entries, indent=2))


def _ensure_migrated() -> None:
    if REGISTRY_JSON.exists() or not _LEGACY_PDF.exists():
        return
    paths = _paths("default")
    paths["pdf_path"].parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(_LEGACY_PDF), paths["pdf_path"])
    for legacy, new_path in (
        (_LEGACY_JSON, paths["json_path"]),
        (_LEGACY_STYLE, paths["style_json_path"]),
        (_LEGACY_PHOTO, paths["photo_path"]),
    ):
        if legacy.exists():
            shutil.move(str(legacy), new_path)

    display_name = "My Resume"
    if paths["json_path"].exists():
        try:
            display_name = json.loads(paths["json_path"].read_text()).get("name") or display_name
        except (json.JSONDecodeError, OSError):
            pass

    _save_registry([{
        "id": "default",
        "display_name": display_name,
        "is_active": True,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }])


def list_resumes() -> list[dict]:
    _ensure_migrated()
    return _load_registry()


def get_active() -> dict | None:
    for entry in list_resumes():
        if entry.get("is_active"):
            return entry
    return None


def get_active_paths() -> dict[str, Path]:
    """Kwargs for resume_parser.parse_and_cache(); callers needing only style_json_path/
    photo_path (resume_template.render_resume/render_cover_letter) just pick those two keys."""
    active = get_active()
    if active is None:
        raise ResumeStoreError(
            "No active resume set - upload a resume and mark it active on the Resume page first."
        )
    return _paths(active["id"])


def paths_for(resume_id: str) -> dict[str, Path]:
    return _paths(resume_id)


def add_resume(display_name: str, pdf_bytes: bytes) -> dict:
    _ensure_migrated()
    resume_id = uuid.uuid4().hex[:12]
    paths = _paths(resume_id)
    paths["pdf_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["pdf_path"].write_bytes(pdf_bytes)
    parsed = resume_parser.parse_and_cache(
        pdf_path=paths["pdf_path"],
        json_path=paths["json_path"],
        style_json_path=paths["style_json_path"],
        photo_path=paths["photo_path"],
        force=True,
    )
    entry = {
        "id": resume_id,
        "display_name": display_name.strip() or parsed.get("name") or "Untitled resume",
        "is_active": False,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    entries = _load_registry()
    entries.append(entry)
    _save_registry(entries)
    return entry


def set_active(resume_id: str) -> bool:
    entries = _load_registry()
    found = False
    for entry in entries:
        if entry["id"] == resume_id:
            entry["is_active"] = True
            found = True
        else:
            entry["is_active"] = False
    if found:
        _save_registry(entries)
    return found


def delete_resume(resume_id: str) -> bool:
    entries = _load_registry()
    remaining = [entry for entry in entries if entry["id"] != resume_id]
    if len(remaining) == len(entries):
        return False
    shutil.rmtree(RESUME_DIR / resume_id, ignore_errors=True)
    _save_registry(remaining)
    return True
