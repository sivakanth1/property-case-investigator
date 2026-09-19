from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from ..agents.runner import RunConflict, create_run, resume_run
from ..config import get_settings
from ..data.repository import (
    DataBlocker, coverage, finding_view, get_property, loads, proposal_view, task_view,
)
from ..db import iso, session_scope
from ..models import Finding, InvestigationRun, RunEvent, Task, TaskProposal
from ..schemas import InvestigationCreate
from .errors import api_error, not_found

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def run_view(s, run: InvestigationRun) -> dict:
    prop = get_property(s, run.property_id)
    proposals = list(s.scalars(select(TaskProposal).where(TaskProposal.run_id == run.id).order_by(TaskProposal.id)))
    task_ids = sorted({p.task_id for p in proposals if p.task_id})
    tasks = [task_view(s, t) for t in s.scalars(select(Task).where(Task.id.in_(task_ids)))] if task_ids else []
    checkpoint = loads(run.checkpoint_json, {})
    settings = get_settings()
    return {
        "id": run.id, "property_id": run.property_id,
        "property": {"id": prop.id, "hcad": prop.hcad, "address": prop.address},
        "status": run.status, "mode": run.mode, "model": run.model, "summary": run.summary, "error": run.error,
        "created_at": iso(run.created_at), "started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
        "events": [{"id": e.id, "type": e.event_type, "summary": e.summary, "created_at": iso(e.created_at)}
                   for e in s.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id))],
        "findings": [finding_view(f) for f in s.scalars(select(Finding).where(Finding.run_id == run.id).order_by(Finding.id))],
        "proposals": [proposal_view(p) for p in proposals],
        "tasks": tasks,
        "coverage": coverage(s, prop),
        "budget": {"tool_calls_used": checkpoint.get("tool_calls_used", 0), "max_tool_calls": settings.max_tool_calls,
                   "revision_calls_used": checkpoint.get("revision_calls_used", 0),
                   "elapsed_s": checkpoint.get("elapsed_s", 0), "time_budget_s": settings.run_time_budget_s,
                   "phase": checkpoint.get("phase"), "stop_reason": checkpoint.get("stop_reason")},
    }


@router.post("", status_code=202)
def start(body: InvestigationCreate, request: Request):
    try:
        result = create_run(body.property_id)
    except LookupError:
        raise not_found("Property")
    except DataBlocker as exc:
        raise api_error(409, "data_blocker", str(exc))
    except RunConflict as exc:
        raise api_error(409, "run_active", str(exc))
    request.app.state.run_manager.enqueue(result["run_id"])
    return result


@router.get("")
def list_runs(property_id: int | None = Query(default=None)):
    with session_scope() as s:
        stmt = select(InvestigationRun).order_by(InvestigationRun.id.desc()).limit(50)
        if property_id is not None:
            stmt = stmt.where(InvestigationRun.property_id == property_id)
        return [{"id": r.id, "property_id": r.property_id, "status": r.status, "mode": r.mode, "model": r.model,
                 "created_at": iso(r.created_at), "finished_at": iso(r.finished_at)} for r in s.scalars(stmt)]


@router.get("/{run_id}")
def get_run(run_id: int):
    with session_scope() as s:
        run = s.get(InvestigationRun, run_id)
        if run is None:
            raise not_found("Investigation")
        return run_view(s, run)


@router.post("/{run_id}/resume", status_code=202)
def resume(run_id: int, request: Request):
    try:
        result = resume_run(run_id)
    except LookupError:
        raise not_found("Investigation")
    except RunConflict as exc:
        raise api_error(409, "not_resumable", str(exc))
    request.app.state.run_manager.enqueue(run_id)
    return result
