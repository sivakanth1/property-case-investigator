import json

from fastapi import APIRouter, Query, Request

from ..agents.commit_gate import commit_proposal
from ..data.repository import get_property, proposal_view
from ..data.supabase_admin import SupabaseError
from ..data.task_store import TaskStore, task_store_for
from ..db import session_scope
from ..models import TaskProposal
from ..schemas import ProposalDecision, TaskUpdate
from .auth import optional_user
from .errors import api_error, not_found

router = APIRouter(prefix="/api", tags=["tasks"])


def store_for(request: Request) -> TaskStore:
    """Signed-in accounts keep tasks in Supabase (any device); guests keep them on this computer."""
    return task_store_for(optional_user(request), getattr(request.app.state, "supabase", None))


def _property_context(property_id: int | None) -> tuple[int | None, str | None]:
    if property_id is None:
        return None, None
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise not_found("Property")
        return prop.id, prop.hcad


@router.get("/tasks")
def tasks(request: Request, property_id: int | None = Query(default=None)):
    pid, hcad = _property_context(property_id)
    try:
        return store_for(request).list_tasks(property_id=pid, hcad=hcad)
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", f"Could not load your tasks: {exc}")


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, request: Request):
    """Changes our internal task only; it never changes the city's record."""
    store = store_for(request)
    try:
        task = store.get_task(task_id)
        if task is None:
            raise not_found("Task")
        note = body.note.strip() if body.note and body.note.strip() else None
        if body.status and body.status != task["status"]:
            action = "reopened" if body.status == "open" and task["status"] in ("verified", "dismissed") else body.status
        elif note:
            action = "note"
        else:
            raise api_error(422, "no_change", f"Task is already {task['status']}; add a note to record feedback.")
        return store.update(task_id, action, body.status if body.status != task["status"] else None, note)
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", f"Could not save your feedback: {exc}")


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: int, request: Request, body: ProposalDecision | None = None):
    """Explicit human approval still passes through the deterministic commit gate."""
    store = store_for(request)
    with session_scope() as s:
        p = s.get(TaskProposal, proposal_id)
        if p is None:
            raise not_found("Proposal")
        if p.status not in ("draft", "needs_review", "revise"):
            raise api_error(409, "proposal_closed", f"Proposal is already {p.status}.")
        property_id = p.property_id
    _, hcad = _property_context(property_id)
    try:
        outcome = commit_proposal(proposal_id, store, hcad, human_approved=True)
        if outcome["outcome"] in ("created", "refreshed") and body and body.note:
            store.update(outcome["task_id"], "approved_proposal", None, body.note.strip())
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", f"Could not save the task: {exc}")
    with session_scope() as s:
        return {"outcome": outcome, "proposal": proposal_view(s.get(TaskProposal, proposal_id))}


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: int, body: ProposalDecision | None = None):
    with session_scope() as s:
        p = s.get(TaskProposal, proposal_id)
        if p is None:
            raise not_found("Proposal")
        if p.status not in ("draft", "needs_review", "revise"):
            raise api_error(409, "proposal_closed", f"Proposal is already {p.status}.")
        p.status = "rejected"
        p.issues_json = json.dumps(json.loads(p.issues_json or "[]")
                                   + [f"Rejected by reviewer: {(body.note if body and body.note else 'no note')}"])
        s.flush()
        return {"proposal": proposal_view(p)}
