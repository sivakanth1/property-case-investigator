import json

from fastapi import APIRouter, Query

from ..agents.commit_gate import commit_proposal
from ..data.repository import list_tasks, proposal_view, task_view
from ..db import session_scope, utcnow
from ..models import Feedback, Task, TaskProposal
from ..schemas import ProposalDecision, TaskUpdate
from .errors import api_error, not_found

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks")
def tasks(property_id: int | None = Query(default=None)):
    with session_scope() as s:
        return [task_view(s, t) for t in list_tasks(s, property_id)]


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate):
    """Changes our internal task only; it never changes the city's record."""
    with session_scope() as s:
        task = s.get(Task, task_id)
        if task is None:
            raise not_found("Task")
        note = body.note.strip() if body.note and body.note.strip() else None
        if body.status and body.status != task.status:
            action = "reopened" if body.status == "open" and task.status in ("verified", "dismissed") else body.status
            task.status = body.status
        elif note:
            action = "note"
        else:
            raise api_error(422, "no_change", f"Task is already {task.status}; add a note to record feedback.")
        task.updated_at = utcnow()
        s.add(Feedback(task_id=task.id, action=action, note=note))
        s.flush()
        return task_view(s, task)


def _proposal(s, proposal_id: int) -> TaskProposal:
    p = s.get(TaskProposal, proposal_id)
    if p is None:
        raise not_found("Proposal")
    if p.status not in ("draft", "needs_review", "revise"):
        raise api_error(409, "proposal_closed", f"Proposal is already {p.status}.")
    return p


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: int, body: ProposalDecision | None = None):
    """Explicit human approval still passes through the deterministic commit gate."""
    with session_scope() as s:
        p = _proposal(s, proposal_id)
        outcome = commit_proposal(s, p, human_approved=True)
        if outcome["outcome"] in ("created", "refreshed") and body and body.note:
            s.add(Feedback(task_id=outcome["task_id"], action="approved_proposal", note=body.note.strip()))
        s.flush()
        return {"outcome": outcome, "proposal": proposal_view(p)}


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: int, body: ProposalDecision | None = None):
    with session_scope() as s:
        p = _proposal(s, proposal_id)
        p.status = "rejected"
        p.issues_json = json.dumps(json.loads(p.issues_json or "[]") + [f"Rejected by reviewer: {(body.note if body and body.note else 'no note')}"])
        s.flush()
        return {"proposal": proposal_view(p)}
