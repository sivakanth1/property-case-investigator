import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agents.runner import RunManager, mark_interrupted_runs
from .api import auth, investigations, plans, properties, tasks
from .config import DATASET_URL, PROJECTS_RESOURCE_ID, VIOLATIONS_RESOURCE_ID, get_settings
from .data.houston_client import HoustonClient
from .data.repository import load_snapshot_file
from .data.supabase_admin import SupabaseAdmin
from .db import init_engine, session_scope
from .models import Property

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("property_case_investigator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings.database_url)
    interrupted = mark_interrupted_runs()
    if interrupted:
        log.info("Marked %d active run(s) as interrupted after restart", interrupted)
    try:
        seeded = load_snapshot_file(settings.snapshot_path)
        if seeded:
            log.info("Seeded %d properties from the saved real snapshot", seeded)
    except Exception:  # noqa: BLE001 - the app stays usable and shows the data blocker instead
        log.exception("Could not load the saved snapshot at %s", settings.snapshot_path)
    app.state.houston = HoustonClient()
    app.state.supabase = (SupabaseAdmin(settings.supabase_url, settings.supabase_service_key)
                          if settings.accounts_enabled else None)
    if app.state.supabase is None:
        log.info("Accounts disabled: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set in backend/.env")
    app.state.run_manager = RunManager(admin=app.state.supabase)
    app.state.run_manager.start()
    yield
    app.state.run_manager.stop()
    app.state.houston.close()
    if app.state.supabase is not None:
        app.state.supabase.close()


app = FastAPI(title="Property Case Investigator API", version="0.2.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.cors_origins),
    allow_origin_regex=_settings.cors_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth.router)
app.include_router(properties.router)
app.include_router(investigations.router)
app.include_router(tasks.router)
app.include_router(plans.router)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
    message = "; ".join(f"{d['field'] or 'request'}: {d['message']}" for d in details)
    return JSONResponse(status_code=422, content={"error": {"code": "invalid_input", "message": message, "details": details}})


@app.get("/api/health")
def health(request: Request):
    settings = get_settings()
    db_ok = True
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
            count = s.scalar(select(func.count()).select_from(Property)) or 0
    except Exception:  # noqa: BLE001
        db_ok, count = False, 0
    live = settings.live_model_enabled
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "worker_alive": request.app.state.run_manager.alive,
        "model": {
            "mode": "live_model" if live else "deterministic_demo",
            "provider": "Featherless.ai",
            "model": settings.llm_model if live else None,
            "endpoint_host": urlparse(settings.llm_base_url).hostname if live else None,
            "tool_mode": settings.llm_tool_mode,
            "warning": None if live else "FEATHERLESS_API_KEY is not set; AI features run in labeled deterministic demo mode.",
        },
        "accounts": {"configured": getattr(request.app.state, "supabase", None) is not None},
        "data": {
            "source": "City of Houston CKAN datastore (historical code-enforcement records)",
            "dataset_url": DATASET_URL,
            "resources": {"violations": VIOLATIONS_RESOURCE_ID, "projects": PROJECTS_RESOURCE_ID},
            "imported_properties": count,
        },
    }
