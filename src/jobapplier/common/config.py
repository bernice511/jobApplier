"""Loads and validates configuration from environment variables (.env)."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
ANSWERS_DIR = DATA_DIR / "answers"
GENERATED_DIR = DATA_DIR / "generated"

MASTER_RESUME_PDF = RESUME_DIR / "master_resume.pdf"
MASTER_RESUME_JSON = RESUME_DIR / "master_resume.json"
MASTER_RESUME_STYLE_JSON = RESUME_DIR / "master_resume_style.json"
MASTER_RESUME_PHOTO = RESUME_DIR / "master_resume_photo.png"
SCREENING_ANSWERS_PATH = ANSWERS_DIR / "screening_answers.yaml"
APPLICATIONS_CSV = DATA_DIR / "applications.csv"
JOB_ALERTS_JSON = DATA_DIR / "job_alerts.json"
LOGS_DIR = DATA_DIR / "logs"

load_dotenv(PROJECT_ROOT / ".env")


def _split_csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_semicolon_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(";") if item.strip()]


@dataclass
class Config:
    job_titles: list[str] = field(default_factory=lambda: _split_csv_env("JOB_TITLES"))
    locations: list[str] = field(default_factory=lambda: _split_semicolon_env("LOCATIONS"))
    seniority_levels: list[str] = field(default_factory=lambda: _split_csv_env("SENIORITY_LEVEL"))
    max_applications_per_run: int = field(
        default_factory=lambda: int(os.getenv("MAX_APPLICATIONS_PER_RUN", "15"))
    )
    min_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("MIN_DELAY_SECONDS", "8"))
    )
    max_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("MAX_DELAY_SECONDS", "25"))
    )
    browser_profile_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("BROWSER_PROFILE_DIR") or str(DATA_DIR / "browser_profile")
        )
    )
    adzuna_app_id: str = field(default_factory=lambda: os.getenv("ADZUNA_APP_ID", ""))
    adzuna_app_key: str = field(default_factory=lambda: os.getenv("ADZUNA_APP_KEY", ""))
    adzuna_country: str = field(default_factory=lambda: os.getenv("ADZUNA_COUNTRY", "us"))
    # Optional second live-search source (jooble.org/api/about for a free key) - purely
    # additive, the live search endpoint just skips Jooble entirely if this is blank.
    jooble_api_key: str = field(default_factory=lambda: os.getenv("JOOBLE_API_KEY", ""))
    alert_match_threshold: int = field(
        default_factory=lambda: int(os.getenv("ALERT_MATCH_THRESHOLD", "6"))
    )
    max_jobs_to_score_per_run: int = field(
        default_factory=lambda: int(os.getenv("MAX_JOBS_TO_SCORE_PER_RUN", "40"))
    )

    def validate(self) -> list[str]:
        """Returns a list of human-readable problems; empty list means config is OK."""
        problems = []
        if not self.job_titles:
            problems.append("JOB_TITLES is empty - set at least one job title/keyword in .env")
        if not self.locations:
            problems.append("LOCATIONS is empty - set at least one location (or 'Remote') in .env")
        if shutil.which("claude") is None:
            problems.append(
                "The `claude` CLI wasn't found on PATH - install Claude Code and run "
                "`claude /login` before running this tool"
            )
        if self.min_delay_seconds > self.max_delay_seconds:
            problems.append("MIN_DELAY_SECONDS must be <= MAX_DELAY_SECONDS")
        from jobapplier.common import resume_store  # deferred: resume_store imports this module
        if resume_store.get_active() is None:
            problems.append(
                "No active resume set - upload a resume and mark it active on the Resume page"
            )
        return problems

    def validate_job_alerts(self) -> list[str]:
        """Separate from validate() since the job-alerts pipeline is optional and has its own
        prerequisites (an Adzuna account) that the LinkedIn auto-apply flow doesn't need."""
        problems = self.validate()
        if not self.adzuna_app_id or not self.adzuna_app_key:
            problems.append(
                "ADZUNA_APP_ID/ADZUNA_APP_KEY are empty - sign up for a free API key at "
                "https://developer.adzuna.com and set them in .env"
            )
        return problems


def load_config() -> Config:
    return Config()
