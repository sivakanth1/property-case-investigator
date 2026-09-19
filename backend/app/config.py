import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")

CKAN_API_BASE = "https://data.houstontx.gov/api/3/action/"
DATASET_URL = "https://data.houstontx.gov/dataset/city-of-houston-building-code-enforcement-violations-don"
VIOLATIONS_RESOURCE_ID = "1446a3ec-2633-4cf1-b15d-6dae9a07c4ed"
PROJECTS_RESOURCE_ID = "496e91d0-1695-4d1f-930b-7c103806613d"
RESOURCE_LABELS = {VIOLATIONS_RESOURCE_ID: "Violations", PROJECTS_RESOURCE_ID: "All Projects"}

ACTION_TYPES = ("verify_current_condition", "review_case_history", "reconcile_records")
PRIORITIES = ("low", "medium", "high")
TASK_STATUSES = ("open", "in_progress", "verified", "dismissed")
FINDING_TYPES = ("case_history", "recurrence", "source_status", "data_quality", "coverage_gap")
# On Render the frontend is served from a sibling *.onrender.com host, so allow those by default there.
# Set CORS_ORIGINS (or CORS_ORIGIN_REGEX) to pin it to your own frontend URL.
ON_RENDER_ORIGINS = r"https://[a-z0-9-]+\.onrender\.com"

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
# Verified 2026-09-19: Qwen3-32B returns native tool calls on Featherless (Qwen3-30B-A3B-Instruct-2507 returns HTTP 500
# whenever tools are sent).
DEFAULT_FEATHERLESS_MODEL = "Qwen/Qwen3-32B"

RUN_ACTIVE_STATUSES = ("queued", "running", "reviewing")
RUN_STATUSES = RUN_ACTIVE_STATUSES + ("completed", "partial", "failed", "interrupted")


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None
    llm_tool_mode: str
    cors_origins: tuple[str, ...]
    cors_origin_regex: str | None
    snapshot_path: Path
    max_rows_per_property: int
    live_refresh_minutes: int
    max_tool_calls: int
    revision_tool_calls: int
    run_time_budget_s: float
    llm_timeout_s: float
    supabase_url: str | None
    supabase_service_key: str | None
    session_days: int

    @property
    def live_model_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def accounts_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)


def get_settings() -> Settings:
    default_db = f"sqlite:///{(BACKEND_DIR / 'property_cases.db').as_posix()}"
    origins = _env("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
    return Settings(
        database_url=_env("DATABASE_URL") or default_db,
        llm_api_key=_env("FEATHERLESS_API_KEY"),
        llm_base_url=_env("FEATHERLESS_BASE_URL") or FEATHERLESS_BASE_URL,
        llm_model=_env("FEATHERLESS_MODEL") or DEFAULT_FEATHERLESS_MODEL,
        llm_tool_mode=(_env("LLM_TOOL_MODE") or "auto").lower(),
        cors_origins=tuple(o.strip().rstrip("/") for o in origins.split(",") if o.strip()),
        cors_origin_regex=_env("CORS_ORIGIN_REGEX") or (ON_RENDER_ORIGINS if _env("RENDER") else None),
        snapshot_path=Path(_env("SNAPSHOT_PATH") or PROJECT_DIR / "sample_data" / "houston_snapshot.json"),
        max_rows_per_property=int(_env("MAX_ROWS_PER_PROPERTY") or 1000),
        live_refresh_minutes=int(_env("LIVE_REFRESH_MINUTES") or 30),
        max_tool_calls=8,
        revision_tool_calls=3,
        run_time_budget_s=float(_env("RUN_TIME_BUDGET_S") or 90),
        llm_timeout_s=float(_env("LLM_TIMEOUT_S") or 45),
        supabase_url=(_env("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_service_key=_env("SUPABASE_SERVICE_ROLE_KEY"),
        session_days=int(_env("SESSION_DAYS") or 7),
    )
