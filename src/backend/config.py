"""
    Runtime settings and repository paths for the AutoApplier backend.

    All values come from environment variables so the same image runs locally and when hosted.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parents[1]
PROMPT_PATH = REPO_ROOT / "prompts" / "resume_builder_prompt.txt"
CV_PARSE_PROMPT_PATH = REPO_ROOT / "prompts" / "cv_parse_prompt.txt"
DEFAULT_CRITERIA_PATH = BACKEND_DIR / "criteria.json"


def _int_env(name, default):
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass
class Settings:
    # SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@db:5432/autoapplier. Unset means every
    # account-bound request is refused (fail closed).
    database_url: str = field(default_factory=lambda: os.environ.get("AUTOAPPLIER_DATABASE_URL", ""))
    # Base64url 32-byte key for encrypting profile data at rest (python -m backend.admin gen-key).
    data_key: str = field(default_factory=lambda: os.environ.get("AUTOAPPLIER_DATA_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"))
    rate_limit_per_minute: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_RATE_LIMIT", 10))
    job_ttl_seconds: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_JOB_TTL", 15 * 60))
    worker_count: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_WORKERS", 2))
    compile_timeout_seconds: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_COMPILE_TIMEOUT", 30))
    # Discovery worker: how often each watched source is polled, and how often the worker wakes.
    poll_interval_seconds: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_POLL_INTERVAL", 15 * 60))
    worker_tick_seconds: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_WORKER_TICK", 30))
    # Worker threads running queued tasks (matching, tailoring), and their idle wake-up interval.
    task_threads: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_TASK_THREADS", 2))
    task_tick_seconds: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_TASK_TICK", 5))
    # Most requests per minute to any one host (all Greenhouse boards share one host, for example).
    host_requests_per_minute: int = field(default_factory=lambda: _int_env("AUTOAPPLIER_HOST_RATE", 30))


def load_default_criteria():
    with open(DEFAULT_CRITERIA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
